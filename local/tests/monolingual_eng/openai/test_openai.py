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


async def chat_completion(query, text_array):
    # logging.info(f"Sending query to OpenAI: {query}")
    response = await aclient.chat.completions.create(model='gpt-4', messages=[{'role': 'user', 'content': query}],
                                                     temperature=1, stream=True)

    async for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content is not None:
            # print(delta.content, end='', flush=True)
            # logging.debug(f"Received content from OpenAI: {delta.content}")
            # await text_queue.put(delta.content)  # Place the content into the queue

            text_array.append(delta.content)

            # # Keep track of every char received
            # for char in delta.content:
            #     chars_to_send.append(char)

        else:
            # logging.info("Received end of OpenAI response")
            # await text_queue.put(None)  # Sentinel value to indicate no more items will be added
            pass


def test_openai_response():
    
    with open('test_data.json', 'r') as f:
        tests = json.load(f)
    
    for test in tests:
        if 'openai_output' not in test:
            print(f"Running test {test['test_num']}")

            text_array = []
            query = test['query_template'].format(text = test['text'])
            asyncio.run(chat_completion(query, text_array))
            test['openai_output'] = text_array
        else:
            print(f"Skipping test {test['test_num']}")

    with open('test_data.json', 'w') as f:
        json.dump(tests, f, indent=2)


if __name__ == "__main__":
    test_openai_response()