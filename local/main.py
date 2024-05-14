import asyncio
import websockets
import json
import base64
import shutil
import os
import subprocess
import logging
from openai import AsyncOpenAI

import logging

# Setup file logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Log everything at DEBUG level and above

# Setup logging to file for detailed debug information
file_handler_debug = logging.FileHandler('debug_log.log', mode='w')
file_handler_debug.setLevel(logging.DEBUG)
file_handler_debug.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Setup logging to file for info messages
file_handler_info = logging.FileHandler('info_log.log', mode='w')
file_handler_info.setLevel(logging.INFO)
file_handler_info.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Add handlers to the logger
logger.addHandler(file_handler_debug)
logger.addHandler(file_handler_info)


# Define API keys and voice ID
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = 'HxxnFvSdN4AyRUpj6yh7'

# Set OpenAI API key
aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)

def is_installed(lib_name):
    return shutil.which(lib_name) is not None

async def text_chunker(input_queue, output_queue):
    """Split text into chunks, ensuring to not break sentences, and place them into an output queue."""
    splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")
    buffer = ""

    while True:
        text = await input_queue.get()
        if text is None:  # End of input
            logger.info("Text chuncker reached end of text queue")
            if buffer:
                await output_queue.put(buffer + " ")
            await output_queue.put(None)  # Signal completion
            break

        logging.debug(f"Chunker received text: {text}")
        if buffer.endswith(splitters):
            await output_queue.put(buffer + " ")
            buffer = text
        elif text.startswith(splitters):
            await output_queue.put(buffer + text[0] + " ")
            buffer = text[1:]
        else:
            buffer += text


async def send_text(websocket, chunked_text_queue):
    """Send chunked text from the queue to ElevenLabs API."""
    while True:
        chunked_text = await chunked_text_queue.get()
        if chunked_text is None:  # End of chunked text
            # Signal the end of the text stream
            logger.info("Send text reached end of chunked text queue. Sending EOS signal")
            await websocket.send(json.dumps({"text": ""}))
            break
        text_message = {"text": chunked_text, "try_trigger_generation": True}
        logging.debug(f"Sending text to ElevenLabs for TTS: {text_message}")
        await websocket.send(json.dumps(text_message))
    


async def listen(websocket, audio_queue):
    """Listen to the websocket for audio data and stream it."""
    received_chars = []  # List to accumulate received characters

    while True:
        try:
            message = await websocket.recv()
            data = json.loads(message)
            if data.get("audio"):
                audio_data = base64.b64decode(data["audio"])
                logging.debug(f"Decoded audio chunk size: {len(audio_data)}")
                await audio_queue.put(audio_data)  # Place audio data into the queue
            if "normalizedAlignment" in data and data["normalizedAlignment"] is not None and "chars" in data["normalizedAlignment"]:
                received_chars.extend(data["normalizedAlignment"]["chars"])  # Accumulate received characters
            if data.get('isFinal'):
                logger.info("Received final audio response")
                await audio_queue.put(None)  # Signal the end of the stream
                break
        except websockets.exceptions.ConnectionClosed as e:
            logging.error(f"WebSocket connection closed unexpectedly: {e}.")
            break
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}.")
            logging.error(message)
            break

    # logging.info(f"Received characters from ElevenLabs {len(received_chars)}: {''.join(received_chars)}")  # Log the accumulated characters for troubleshooting
    logging.info(f"Boop {len(received_chars)}: {received_chars}")  # Log the accumulated characters for troubleshooting

async def stream(audio_queue):
    if not is_installed("mpv"):
        logging.error("mpv not found, necessary to stream audio. Install it for proper functionality.")
        raise ValueError("mpv not found, necessary to stream audio. Install instructions: https://mpv.io/installation/")

    mpv_process = subprocess.Popen(
        ["mpv", "--no-cache", "--no-terminal", "--", "fd://0"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    logging.info("Started streaming audio")
    while True:
        chunk = await audio_queue.get()
        if chunk is None:  # Check for the signal to end streaming
            logging.info("Stream reached end of audio queue. Stopped streaming audio")
            break
        logging.debug(f"Streaming audio chunk of size: {len(chunk)}")
        mpv_process.stdin.write(chunk)
        mpv_process.stdin.flush()

    if mpv_process.stdin:
        mpv_process.stdin.close()
    mpv_process.wait()


async def text_to_speech_input_streaming(voice_id, text_queue):
    audio_queue = asyncio.Queue()
    chunked_text_queue = asyncio.Queue()  # Queue for chunked text

    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id=eleven_monolingual_v1&xi_api_key={ELEVENLABS_API_KEY}"
    async with websockets.connect(uri) as websocket:
        logging.info("WebSocket connection established with ElevenLabs API.")
        init_message = {
            "text": " ",
            "voice_settings": {"stability": 0.70, "similarity_boost": 0.75},
            "xi_api_key": ELEVENLABS_API_KEY,
        }
        await websocket.send(json.dumps(init_message))

        # Start the text_chunker, send_text, listen, and stream concurrently
        await asyncio.gather(
            text_chunker(text_queue, chunked_text_queue),
            send_text(websocket, chunked_text_queue),
            listen(websocket, audio_queue),
            stream(audio_queue),
        )


async def chat_completion(query, text_queue):
    logging.info(f"Sending query to OpenAI: {query}")
    response = await aclient.chat.completions.create(model='gpt-4', messages=[{'role': 'user', 'content': query}],
                                                     temperature=1, stream=True)

    char_array = []

    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content is not None:
            print(delta.content, end='', flush=True)
            logging.debug(f"Received content from OpenAI: {delta.content}")
            await text_queue.put(delta.content)  # Place the content into the queue

            # Keep track of every char received
            for char in delta.content:
                if char != "/n":
                    char_array.append(char)
        else:
            logging.info("Received end of OpenAI response")
            await text_queue.put(None)  # Sentinel value to indicate no more items will be added
    
    # logging.info(f"Received characters from OpenAI {len(char_array)}: {''.join(char_array)}")  # Log the accumulated characters for troubleshooting
    logging.info(f"beep {len(char_array)}: {char_array}")  # Log the accumulated characters for troubleshooting

async def main():
    logging.debug("Program started")
    # user_query = "Hello, tell me a very short story."
    user_query = "Hey there, can you give me an inspirational quote from someone famous? I'm feeling a little tired but I want to get inspired to work hard today."
    # user_query = "Hello, can you tell me a story that is exactly 500 words long?"
    # user_query = "Hello, can you explain how async and yields work in python?"

    text_queue = asyncio.Queue()
    await asyncio.gather(
        chat_completion(user_query, text_queue),
        text_to_speech_input_streaming(VOICE_ID, text_queue)
    )


# Main execution
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    logging.debug("Program finished")
