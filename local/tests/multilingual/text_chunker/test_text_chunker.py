import sys
import json
import logging
import asyncio
import unittest

sys.path.append('../../../')  # Add the parent directory to the Python path
from local.sandbox import setup_logger, multi_log  # Import the text_chunker function

# Setup individual loggers for specific functions
app_logger = setup_logger('app')
setup_logger('text_chunker')

async def text_chunker(input_queue, output_queue):
    """Split text into chunks (words) and place them into an output queue."""
    buffer = ""
    logger = logging.getLogger('text_chunker')

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
                    buffer = ""
            else:
                buffer += char
        
        logger.debug(f"Buffer: {repr(buffer)}")



class TestTextChunker(unittest.TestCase):
    async def async_test_text_chunker_01(self):
        input_queue = asyncio.Queue()
        output_queue = asyncio.Queue()

        # Put some sample text into the input queue
        with open('inputs/01.json', 'r') as f:
            texts = json.load(f)
        
        for text in texts:
            await input_queue.put(text)
        await input_queue.put(None)  # Signal the end of input

        # Run the text_chunker function
        await text_chunker(input_queue, output_queue)

        # # Check the output queue
        # expected_output = ["Hello, ", "world! ", "This is a test. ", None]
        # for expected_text in expected_output:
        #     output_text = await output_queue.get()
        #     self.assertEqual(output_text, expected_text)

    def test_01(self):
        asyncio.run(self.async_test_text_chunker_01())

if __name__ == "__main__":
    unittest.main()
