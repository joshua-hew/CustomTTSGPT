import os
import json
import base64
import asyncio
import unittest
import websockets
from openai import AsyncOpenAI


# Environment variables
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
VOICE_ID = 'HxxnFvSdN4AyRUpj6yh7'

# Set OpenAI API key
aclient = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def text_chunker(input_queue, output_queue):
    """Split text into chunks, ensuring to not break sentences, and place them into an output queue."""
    splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")
    buffer = ""

    while True:
        text = await input_queue.get()
        if text is None:  # End of input
            if buffer:
                await output_queue.put(buffer + " ")
            await output_queue.put(None)  # Signal completion
            break

        if buffer.endswith(splitters):
            await output_queue.put(buffer + " ")
            buffer = text
        elif text.startswith(splitters):
            await output_queue.put(buffer + text[0] + " ")
            buffer = text[1:]
        else:
            buffer += text
            
        # print(repr(buffer))
        # print(buffer)

async def send_text(websocket, chunked_text_queue):
    """Send chunked text from the queue to ElevenLabs API."""
    while True:
        chunked_text = await chunked_text_queue.get()
        if chunked_text is None:  # End of chunked text
            # Signal the end of the text stream
            await websocket.send(json.dumps({"text": ""}))
            break
        text_message = {"text": chunked_text, "try_trigger_generation": True}
        await websocket.send(json.dumps(text_message))


async def listen(websocket, responses):
    """Listen to the websocket for audio data and stream it."""

    while True:
        message = await websocket.recv()
        data = json.loads(message)
        responses.append(data)

        # Handle audio...

        if data.get('isFinal'):
            break


async def text_to_speech_streaming(text_queue):
    chunked_text_queue = asyncio.Queue() 
    responses = []

    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream-input?model_id=eleven_monolingual_v1&xi_api_key={ELEVENLABS_API_KEY}"
    async with websockets.connect(uri) as websocket:
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
            listen(websocket, responses)
        )

        return responses
        # print(json.dumps(responses))





async def test_repeating_special_characters():
    
    with open('../openai/test_data.json', 'r') as f:
        tests = json.load(f)
    
    for test in tests:
        if 'elevenlabs_output' not in test:
            print(f"Running test {test['test_num']}")

            # Create text queue used for input
            text_queue = asyncio.Queue() 
            for text in test['openai_output']:
                await text_queue.put(text)
            await text_queue.put(None)
            
            # Start text-to-speech process
            responses = await text_to_speech_streaming(text_queue)

            # Modify
            for response in responses:
                response.pop("audio", None)
                if response["normalizedAlignment"]:
                    response["normalizedAlignment"].pop("charStartTimesMs", None)
                    response["normalizedAlignment"].pop("charDurationsMs", None)
                if response["alignment"]:
                    response["alignment"].pop("charStartTimesMs", None)
                    response["alignment"].pop("charDurationsMs", None)

            test["elevenlabs_output"] = responses
        else:
            print(f"Skipping test {test['test_num']}")

    with open('test_data.json', 'w') as f:
        json.dump(tests, f, indent=2)
    
    
    
    # # Test 1
    # with open('../openai/output_01.json', 'r') as f:
    #     text_array = json.load(f)
    
    # text_queue = asyncio.Queue() 
    # for content in text_array:
    #     await text_queue.put(content)
    # await text_queue.put(None)

    # await text_to_speech_streaming(text_queue)

    
    


if __name__ == '__main__':
    asyncio.run(test_repeating_special_characters())



# class TestTextToSpeech(unittest.TestCase):

#     def test_repeating_special_characters(self):
        
#         # Test 1
#         text_queue = asyncio.Queue()
#         user_query = "Hi ChatGPT, please only respond with the following text. Do not include anything else in your response: Hello\n\nWorld"
        
        
#         self.assertEqual(True, True)  # Example assertion

# if __name__ == '__main__':
#     unittest.main()
