import os
import json
import base64
import shutil
import logging
import asyncio
import websockets

from openai import AsyncOpenAI

class ChatBot:

    def __init__(self, client_websocket, log_dir):
        self.client_websocket   = client_websocket
        self.openai_api_key     = os.environ.get("OPENAI_API_KEY", "")
        self.elevenlabs_api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice_id           = 'HxxnFvSdN4AyRUpj6yh7'

        self.aclient = AsyncOpenAI(api_key=self.openai_api_key)

        self.setup_logging(log_dir)

    def setup_logging(self, log_dir):
        """Creates seperate log files for each function in this object."""

        # Create the directory if it does not exist
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # List of log files to create. One for overall app. One for each major function.
        log_files = [
            'app',
            'chat_completion',
            'text_chunker',
            'send_text',
            'listen',
            'stream',
            'get_remaining_chars_to_send'
        ]
        
        # Set up logging configuration
        for name in log_files:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            
            # Each function gets its own log file
            file_handler = logging.FileHandler(os.path.join(log_dir, f"{name}.log"), mode='w')
            file_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    def multi_log(self, message, logger_names, level=logging.INFO):
        """Logs a message to the specified named loggers."""

        # Log to the named loggers
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            logger.log(level, message)


    async def text_chunker(self, input_queue, output_queue):
        """Split text into chunks (words) and place them into an output queue."""
        
        # Setup logger
        logger = logging.getLogger('text_chunker')
        self.multi_log("Text chunker started", loggers=['app', 'text_chunker'])
        
        # Define text buffer
        buffer = ""

        async def put_in_queue(data, queue):
            logger.debug(f"Adding to queue: {repr(data)}")
            await queue.put(data)

        while True:
            text = await input_queue.get()
            logger.debug(f"Chunker received text: {repr(text)}")
            
            if text is None:  # End of input
                self.multi_log("Text chunker reached end of text queue.", loggers=['app', 'text_chunker'])
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
        self.multi_log("Text chunker finished", loggers=['app', 'text_chunker'])
    

    async def send_text(self, websocket, chunked_text_queue):
        """Send chunked text from the queue to ElevenLabs API."""
        
        # Setup logger
        logger = logging.getLogger('send_text')
        self.multi_log("Send text started", loggers=['app', 'send_text'])
        
        while True:
            chunked_text = await chunked_text_queue.get()
            if chunked_text is None:  # End of chunked text. Signal the end of the text stream
                self.multi_log("Send text reached end of chunked text queue. Sending EOS signal.", loggers=['app', 'send_text'])
                await websocket.send(json.dumps({"text": ""}))
                break
            text_message = {"text": chunked_text, "try_trigger_generation": False}
            logger.debug(f"Sending text to ElevenLabs for TTS: {repr(chunked_text)}")
            await websocket.send(json.dumps(text_message))

        # Log end of function execution
        self.multi_log("Send text finished", loggers=['app', 'send_text'])


    async def listen(self, websocket, audio_queue, chars_received):
        """Listen to the websocket for audio data and stream it."""

        # Setup logger
        logger = logging.getLogger('listen')
        self.multi_log("Listen started", loggers=['app', 'listen'])

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
                self.multi_log("Received final audio response", loggers=['app', 'listen'])
                logger.debug(f"Chars received: {json.dumps(chars_received)}")
                await audio_queue.put(None)  # Signal the end of the stream
                break

        # Log end of function execution
        self.multi_log("Listen finished", loggers=['app', 'listen'])


    async def stream(self, audio_queue, client_websocket):

        # Setup logger
        logger = logging.getLogger('stream')
        self.multi_log("Stream started", loggers=['app', 'stream'])
        
        while True:
            chunk = await audio_queue.get()
            if chunk is None:  # Check for the signal to end streaming
                self.multi_log("Stream reached end of audio queue", loggers=['app', 'stream'])
                break
            
            client_websocket.send(json.dumps(chunk))

        # Send an end / sentinel signal? Keep websocket open?
        # TODO

        # Log end of function execution
        self.multi_log("Stream finished", loggers=['app', 'stream'])
            

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


    async def connect_to_elevenlabs(self, voice_id, api_key):
        # Setup logger
        logger = logging.getLogger('app')

        try:
            uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_multilingual_v2&xi_api_key={api_key}"
            async with websockets.connect(uri) as websocket:
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

    async def text_to_speech_input_streaming(self, text_queue, chars_to_send):
        logger = logging.getLogger('app')
        
        chunked_text_queue = asyncio.Queue() 
        audio_queue = asyncio.Queue()
        chars_received = []

        while True:
            try:
                elevenlabs_websocket = self.connect_to_elevenlabs(self.voice_id, self.elevenlabs_api_key)

                # Start the text_chunker, send_text, listen, and stream concurrently
                await asyncio.gather(
                    self.text_chunker(text_queue, chunked_text_queue),
                    self.send_text(elevenlabs_websocket, chunked_text_queue),
                    self.listen(elevenlabs_websocket, audio_queue, chars_received),
                    self.stream(audio_queue)
                )
                    
                break  # Exit the loop if everything went well
            
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed unexpectedly: {e}. Retrying...")
                
                remaining_chars = self.get_remaining_chars_to_send(chars_to_send, chars_received)

                # Add remaining text to queue
                text_queue = asyncio.Queue()
                await text_queue.put(''.join(remaining_chars))
                await text_queue.put(None)

                # Reset chars_to_send, chars_received, chunked_text queue, and audio_queue
                chars_to_send = remaining_chars
                chars_received = []
                chunked_text_queue = asyncio.Queue()
            
            finally:
                await elevenlabs_websocket.close()

    async def chat_completion(self, messages, text_queue, chars_to_send):
        logger = logging.getLogger('chat_completion')
        self.multi_log(f"Sending query to OpenAI: {messages[-1]}", loggers=['app', 'chat_completion'])

        response = await self.aclient.chat.completions.create(
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
                self.multi_log("Received end of OpenAI response", loggers=['app', 'chat_completion'])
                logger.info(f"Response content: {json.dumps(response_content)}")
                logger.debug(f"chars_to_send: {json.dumps(chars_to_send)}")
                await text_queue.put(None)  # Sentinel value to indicate no more items will be added
                
                # Return dict containing the role + response string
                response_content_string = "".join(response_content)
                ret_val = {'role': role, 'content': response_content_string}
                logger.debug(f"ret_val: {json.dumps(ret_val)}")
                return ret_val
            
    
    
    async def main(self):
        message_history = []    # TODO: change to database
        
        async for message in self.client_websocket:
            # Get query
            query = json.loads(message)
            print(repr(query))

            # Append query to message history
            message_history.append(query)

            # Reset message history if too long
            if len(message_history) > 10:
                message_history = message_history[-1:]

            # Process query and send audio to client
            pass
    
    
    def run(self):
        asyncio.run(self.main())