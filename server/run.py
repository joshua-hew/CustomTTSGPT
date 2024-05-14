#!/usr/bin/env python

import os
import uuid
import signal
import asyncio
import websockets
from datetime import datetime

from chatbot import ChatBot

async def handle_connection(websocket):
    # Setup logging for this connection
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")   # ISO 8601 standard
    log_dir = f"logs/{timestamp}"
    os.makedirs(log_dir, exist_ok=True)

    # Instantiate the ChatBot class
    chat_bot = ChatBot(websocket, os.path.realpath(log_dir))

    # Run main function
    chat_bot.run()
    

async def echo(websocket):
    async for message in websocket:
        await websocket.send(message)

async def server():
    # Set the stop condition when receiving SIGTERM.
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    loop.add_signal_handler(signal.SIGTERM, stop.set_result, None)

    async with websockets.serve(handle_connection, "localhost", 8765):
        await stop

asyncio.run(server())

# Test multiple connections