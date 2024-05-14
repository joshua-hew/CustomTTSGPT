import os
import json
import base64
import shutil
import logging
import asyncio
import subprocess
import websockets
import speech_recognition as sr

from openai import AsyncOpenAI

def setup_logger(name):
    """Sets up a logger for a given name."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Get the directory of the current file
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Construct the logs directory path
    logs_dir = os.path.join(current_dir, 'logs')
    
    # Create the logs directory if it does not exist
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    # Create a file handler that logs to a separate file for each named logger.
    file_handler = logging.FileHandler(os.path.join(logs_dir, f'{name}.log'), mode='w')
    file_handler.setLevel(logging.DEBUG)

    # Create a formatter and add it to the handler.
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # Add the handler to the logger.
    logger.addHandler(file_handler)

    return logger

# def setup_logger(name):
#     """Sets up a logger for a given name."""
#     logger = logging.getLogger(name)
#     logger.setLevel(logging.DEBUG)

#     # Create a file handler that logs to a separate file for each named logger.
#     file_handler = logging.FileHandler(f'logs/{name}.log', mode='w')
#     file_handler.setLevel(logging.DEBUG)

#     # Create a formatter and add it to the handler.
#     formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     file_handler.setFormatter(formatter)

#     # Add the handler to the logger.
#     logger.addHandler(file_handler)

#     return logger


def multi_log(message, level=logging.INFO, loggers=None):
    """
    Logs a message to the specified named loggers.

    Args:
        message (str): The message to log.
        level (int): The logging level (e.g., logging.INFO, logging.ERROR).
        loggers (list[str]): A list of names of the loggers to log the message to.
    """

    # Log to the named loggers
    if loggers:
        for logger_name in loggers:
            logger = logging.getLogger(logger_name)
            logger.log(level, message)


# Setup individual loggers for specific functions
app_logger = setup_logger('app')
setup_logger('chat_completion')
setup_logger('text_chunker')
setup_logger('send_text')
setup_logger('listen')
setup_logger('stream')
setup_logger('get_remaining_chars_to_send')

# Define API keys and voice ID
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = 'HxxnFvSdN4AyRUpj6yh7'

# Set OpenAI API key
aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)

class MPVProcessSingleton:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MPVProcessSingleton, cls).__new__(cls)
            cls._instance.process = None
        return cls._instance

    def is_installed(self, lib_name):
        return shutil.which(lib_name) is not None

    def start_process(self):
        if not self.is_installed("mpv"):
            app_logger.error("mpv not found, necessary to stream audio. Install it for proper functionality.")
            raise ValueError("mpv not found, necessary to stream audio. Install instructions: https://mpv.io/installation/")

        if self.process is None or self.process.poll() is not None:
            multi_log("Starting mpv process", loggers=['app', 'stream'])
            self.process = subprocess.Popen(
                ["mpv", "--no-cache", "--no-terminal", "--", "fd://0"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def stop_process(self):
        if self.process and self.process.stdin:
            multi_log("Stopping mpv process...", loggers=['app', 'stream'])
            # self.process.terminate()
            self.process.stdin.close()
            self.process.wait()
            self.process = None
            multi_log("Stopped mpv process", loggers=['app', 'stream'])



async def text_chunker(input_queue, output_queue):
    """Split text into chunks (words) and place them into an output queue."""
    
    # Setup logger
    logger = logging.getLogger('text_chunker')
    multi_log("Text chunker started", loggers=['app', 'text_chunker'])
    
    # Define text buffer
    buffer = ""

    async def put_in_queue(data, queue):
        logger.debug(f"Adding to queue: {repr(data)}")
        await queue.put(data)

    while True:
        text = await input_queue.get()
        logger.debug(f"Chunker received text: {repr(text)}")
        
        if text is None:  # End of input
            multi_log("Text chunker reached end of text queue.", loggers=['app', 'text_chunker'])
            if buffer:
                await put_in_queue(buffer + " ", output_queue)
                buffer = "" # Reset buffer
            await put_in_queue(None, output_queue) # Signal completion
            break

        for char in text:
            if char == " ": # We have reached end of word. Send contents of buffer
                if buffer:
                    await put_in_queue(buffer + " ", output_queue)
                    buffer = "" # Reset buffer
            else:
                buffer += char
        
        logger.debug(f"Buffer: {repr(buffer)}")

    # Log end of function execution
    multi_log("Text chunker finished", loggers=['app', 'text_chunker'])


async def send_text(websocket, chunked_text_queue):
    """Send chunked text from the queue to ElevenLabs API."""
    
    # Setup logger
    logger = logging.getLogger('send_text')
    multi_log("Send text started", loggers=['app', 'send_text'])
    
    while True:
        chunked_text = await chunked_text_queue.get()
        if chunked_text is None:  # End of chunked text. Signal the end of the text stream
            multi_log("Send text reached end of chunked text queue. Sending EOS signal.", loggers=['app', 'send_text'])
            await websocket.send(json.dumps({"text": ""}))
            break
        text_message = {"text": chunked_text, "try_trigger_generation": False}
        logger.debug(f"Sending text to ElevenLabs for TTS: {repr(chunked_text)}")
        await websocket.send(json.dumps(text_message))
    
    # Log end of function execution
    multi_log("Send text finished", loggers=['app', 'send_text'])


async def listen(websocket, audio_queue, chars_received):
    """Listen to the websocket for audio data and stream it."""

    # Setup logger
    logger = logging.getLogger('listen')
    multi_log("Listen started", loggers=['app', 'listen'])

    while True:
        message = await websocket.recv()
        data = json.loads(message)
        
        if data.get("audio"):   # Audio key might be absent, or value could be null. Don't proceed if either
            audio_data = base64.b64decode(data.pop('audio'))
            logger.debug(f"Data received (audio-omitted): {json.dumps(data)}")
            await audio_queue.put(audio_data)  # Place audio data into the queue
        else:
            logger.debug(f"Data received: {json.dumps(data)}")
        
        if data.get("normalizedAlignment"):   
            if data["normalizedAlignment"].get("chars"):
                chars_received.extend(data["normalizedAlignment"]["chars"])  # Accumulate received characters
        
        if data.get('isFinal'):
            multi_log("Received final audio response", loggers=['app', 'listen'])
            logger.debug(f"Chars received: {json.dumps(chars_received)}")
            await audio_queue.put(None)  # Signal the end of the stream
            break
        
    # Log end of function execution
    multi_log("Listen finished", loggers=['app', 'listen'])

async def stream(audio_queue):
    # Setup logger
    logger = logging.getLogger('stream')
    multi_log("Stream started", loggers=['app', 'stream'])
    
    # Setup audio process
    mpv_singleton = MPVProcessSingleton()
    mpv_singleton.start_process()
    mpv_process = mpv_singleton.process

    
    while True:
        chunk = await audio_queue.get()
        if chunk is None:  # Check for the signal to end streaming
            multi_log("Stream reached end of audio queue", loggers=['app', 'stream'])
            break
        mpv_process.stdin.write(chunk)
        mpv_process.stdin.flush()

    mpv_singleton.stop_process()

    # Log end of function execution
    multi_log("Stream finished", loggers=['app', 'stream'])


def get_remaining_chars_to_send(chars_to_send: list, chars_received: list) -> list:
    """Returns an array of the remaining characters that haven't been converted to speech."""

    logger = logging.getLogger('get_remaining_chars_to_send')

    # Log inputs for troubleshooting
    logger.debug(f"Characters to send. Len: {len(chars_to_send)}.")
    logger.debug(f"{json.dumps(chars_to_send)}")
    logger.debug(f"Characters received. Len: {len(chars_received)}.")
    logger.debug(f"{json.dumps(chars_received)}")


    def custom_decode(char):
        special_chars = {
            "\u2018": "'",  # Left single quotation mark
            "\u2019": "'",  # Right single quotation mark
            "\u201C": '"',  # Left double quotation mark
            "\u201D": '"',  # Right double quotation mark
            "\u2013": "-",  # En dash
            "\u2014": "-",  # Em dash
            "\u2026": "...",  # Horizontal ellipsis
            "\u2022": "*",  # Bullet
            "\u00A3": "GBP",  # Pound sign
            "\u20AC": "EUR",  # Euro sign
            "\u00D7": "x",  # Multiplication sign
            "\u00F7": "/",  # Division sign
            # Add more special characters as needed
        }
        return special_chars.get(char, char)

    # Format chars to send for easier comparison with characters received
    chars_to_send_formatted = [custom_decode(c) for c in chars_to_send]

    # Format chars received for easier comparison
    chars_received_formatted = chars_received[1:]   # Remove leading space in chars_received
    
    # Log formatted versions for troubleshooting
    logger.debug(f"Characters to send formatted. Len: {len(chars_to_send_formatted)}.")
    logger.debug(f"{json.dumps(chars_to_send_formatted)}")
    logger.debug(f"Characters received formatted. Len: {len(chars_received_formatted)}.")
    logger.debug(f"{json.dumps(chars_received_formatted)}")


    # Determine where to continue in the text queue.
    # Continue point is the index after the last character received succesfully. 
    continue_point = None
    
    # The index in the second array.
    # This is where the matching character should be in the second array.
    # The matching character is allowed to be at most (j + displacement tolerance away)
    j = 0
    displacement_tolerance = 2

    for i in range(len(chars_to_send_formatted)):
        # The character to match
        char = chars_to_send_formatted[i]

        if char == "\n":
            continue

        # If j pointer in bounds:
        if j < len(chars_received_formatted):
            
            # The matching character is allowed to be at most (j + displacement tolerance away)
            # If the matching character is not exactly at j, but within the tolerance, update j, and log that occurrence
            # Else, throw an error and populate logs
            try:
                index_of_next_matching_char = chars_received_formatted.index(char, j)
                if index_of_next_matching_char == j:
                    pass
                
                elif index_of_next_matching_char - j <= displacement_tolerance:
                    logger.warning(f"Idiosyncracy found. Expected matching char '{char}' to be at {j}. Was found at {index_of_next_matching_char}")
                    logger.debug(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                    logger.debug(f"Context for chars_received: {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")

                    # Set j to index of matching char 
                    j = index_of_next_matching_char
                
                else:
                    logger.error(f"Index of next matching char not within tolerance. Expected char '{char}' to be at {j}. Was found at {index_of_next_matching_char}. Displacement tolerance: {displacement_tolerance}")
                    logger.error(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                    logger.error(f"Context for chars_received (centered at j): {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")
                    logger.error(f"Context for chars_received (centered at index of next_next_matching_char): {json.dumps(chars_received_formatted[max(0, index_of_next_matching_char-10):index_of_next_matching_char+10])}")
                    raise Exception(f"Could not confirm that the current char was received within the displacement tolerance. Char: '{char}'")

                # Increment j
                j += 1
            

            except ValueError as e: # If can't find matching char in chars received
                logger.warning(f"Index() could not find a match for char '{char}' at position {i}")
                logger.debug(f"Context for chars_to_send: {json.dumps(chars_to_send_formatted[max(0, i-10):i+10])}")
                logger.debug(f"Context for chars_received (centered at j): {json.dumps(chars_received_formatted[max(0, j-10):j+10])}")
                continue_point = i
                break


        else: # If reached end of received chars, then the current un-matched char is the continue point
            logger.info("Found continue point")
            continue_point = i
            break

    # If loop exited naturally (went out of bounds), then all characters were matched...
    if continue_point is None:
        logger.info("All characters received. No need to find continue point.")
        return []

    logger.debug(f"Continue point: {continue_point}")
    remaining_chars = chars_to_send[continue_point:]

    logger.debug(f"Remaining chars. Len {len(remaining_chars)}")
    logger.debug(f"{json.dumps(remaining_chars)}")
    return remaining_chars


async def connect_to_elevenlabs(voice_id, api_key):
    # Setup logger
    logger = logging.getLogger('app')
    
    try:
        uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_multilingual_v2&xi_api_key={api_key}"
        websocket = await websockets.connect(uri)
        logger.info("WebSocket connection established with ElevenLabs API.")
        init_message = {
            "text": " ",
            "voice_settings": {"stability": 0.70, "similarity_boost": 0.75},
            "xi_api_key": api_key,
        }
        await websocket.send(json.dumps(init_message))
        return websocket
        
    except Exception as e:
        raise e

async def text_to_speech_input_streaming(voice_id, text_queue, chars_to_send):
    chunked_text_queue = asyncio.Queue() 
    audio_queue = asyncio.Queue()
    chars_received = []

    while True:
        try:
            elevenlabs_websocket = await connect_to_elevenlabs(voice_id, ELEVENLABS_API_KEY)

            # Start the text_chunker, send_text, listen, and stream concurrently
            await asyncio.gather(
                text_chunker(text_queue, chunked_text_queue),
                send_text(elevenlabs_websocket, chunked_text_queue),
                listen(elevenlabs_websocket, audio_queue, chars_received),
                stream(audio_queue)
            )
            
            break  # Exit the loop if everything went well
        
        except websockets.exceptions.ConnectionClosed as e:
            app_logger.warning(f"WebSocket connection closed unexpectedly: {e}. Retrying...")
            
            remaining_chars = get_remaining_chars_to_send(chars_to_send, chars_received)

            # Add remaining text to queue
            text_queue = asyncio.Queue()
            await text_queue.put(''.join(remaining_chars))
            await text_queue.put(None)

            # Reset chars_to_send, chars_received, chunked_text queue
            chars_to_send = remaining_chars
            chars_received = []
            chunked_text_queue = asyncio.Queue()


        except Exception as e:
            app_logger.error(f"An unexpected error occurred: {e}")
            break

        finally:
            await elevenlabs_websocket.close()

async def chat_completion(messages, text_queue, chars_to_send):
    logger = logging.getLogger('chat_completion')
    multi_log(f"Sending query to OpenAI: {messages[-1]}", loggers=['app', 'chat_completion'])

    response = await aclient.chat.completions.create(
        model='gpt-4', 
        messages=messages,
        temperature=1, 
        stream=True
    )

    role = None
    response_content = []
    
    async for chunk in response:        
        delta = chunk.choices[0].delta

        # Role only returned in first chunk. First chunk always empty string.
        if delta.content == '':
            role = delta.role
            logger.debug(f"Role: {role}")

        if delta.content is not None:
            if delta.content != "": # OpenAI usually starts response with empty string
                print(delta.content, end='', flush=True)
                logger.debug(f"Received content from OpenAI: {repr(delta.content)}")
                response_content.append(delta.content)
                await text_queue.put(delta.content)  # Place the content into the queue

                # Keep track of every char received
                for char in delta.content:
                    chars_to_send.append(char)
            
            else:
                logger.debug(f"Delta.content is empty string: {repr(delta.content)}")

        else:
            multi_log("Received end of OpenAI response", loggers=['app', 'chat_completion'])
            logger.info(f"Response content: {json.dumps(response_content)}")
            logger.debug(f"chars_to_send: {json.dumps(chars_to_send)}")
            await text_queue.put(None)  # Sentinel value to indicate no more items will be added
            
            # Return dict containing the role + response string
            response_content_string = "".join(response_content)
            ret_val = {'role': role, 'content': response_content_string}
            logger.debug(f"ret_val: {json.dumps(ret_val)}")
            return ret_val
    

# async def main():
#     app_logger.info("Program started")
#     user_query = "Hello, tell me a short story in 100 words or less and in spanish?"
#     # user_query = "Hello, tell me a short story in 200 words or less? Also, can you tell the story in a mix of english and spanish?"
#     # user_query = "Hello, tell me a short story in 100 words or less? Also, can you tell the story in a mix of english and japanese?"
#     # user_query = "Hello, can you give me an inspirational quote from someone famous? I'm feeling a little tired but I want to get inspired to work hard today."
#     # user_query = "Hello, can you tell me a story that is exactly 500 words long?"
#     # user_query = "Hello, can you summarize the tragedy of darth plageuis the wise in 100 words or less?"

#     text_queue = asyncio.Queue()
#     chars_to_send = []
#     await asyncio.gather(
#         chat_completion(user_query, text_queue, chars_to_send),
#         text_to_speech_input_streaming(VOICE_ID, text_queue, chars_to_send)
#     )
#     app_logger.info("Program finished")

def speech_to_text():
    # Initialize the recognizer
    r = sr.Recognizer()

    # Use the default microphone as the audio source
    with sr.Microphone() as source:
        print("Please say something:")
        # Listen for the first phrase and extract the audio data
        audio = r.listen(source)

        try:
            # Use Google's speech recognition
            text = r.recognize_google(audio)
            print("You said: " + text)
            return text
        except sr.UnknownValueError:
            print("Google Speech Recognition could not understand audio")
        except sr.RequestError as e:
            print("Could not request results from Google Speech Recognition service; {0}".format(e))

async def get_conversation_history():
    # Todo: implement conversation history database.
    # PK: user_id
    # SK: conversation_id

    return []

async def main():
    app_logger.info("Program started")
    
    messages = await get_conversation_history()

    while True:
        
        
        user_query = input("Enter your query or type 'exit' to quit: ")
        
        # user_query = speech_to_text()
        # if user_query is None: 
        #     continue
        

        messages.append({'role': 'user', 'content': user_query})

        # Ensure messages token size is under the limit. TODO: Implement better solution
        if len(messages) > 10:
            messages = messages[-10:]

        if user_query.lower() == 'exit':
            break
        
        text_queue = asyncio.Queue()
        chars_to_send = []
        values = await asyncio.gather(
            chat_completion(messages, text_queue, chars_to_send),
            text_to_speech_input_streaming(VOICE_ID, text_queue, chars_to_send)
        )

        messages.append(values[0])
        print('\n')

    app_logger.info("Program finished")


# Main execution
if __name__ == "__main__":
    asyncio.run(main())

    
