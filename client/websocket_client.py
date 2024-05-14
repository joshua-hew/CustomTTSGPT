import json
import asyncio
import websockets

# async def websocket_client():
#     uri = "ws://127.0.0.1:8765"
#     async with websockets.connect(uri) as websocket:
#         await websocket.send("Hello, server!")
#         response = await websocket.recv()
#         print(f"Received from server: {response}")

async def websocket_client():
    uri = "ws://127.0.0.1:8765"
    async with websockets.connect(uri) as websocket:
        data = {
            "user_input": "Hello ChatGPT! Can you tell me a story in 100 words or less?"
        }
        await websocket.send(json.dumps(data))
        response = await websocket.recv()
        print(f"Received from server: {response}")

asyncio.run(websocket_client())
