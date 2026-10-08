import asyncio
import websockets
import json

async def main():
    async with websockets.connect('ws://127.0.0.1:8000/ws?replay=bank_scam&speed=10') as websocket:
        count = 0
        while count < 8:
            data = await websocket.recv()
            event = json.loads(data)
            if event['type'] == 'transcript':
                count += 1
            elif event['type'] in ['risk', 'reality_pause']:
                print(f"({event.get('at', '')}, {event['type']}, {event.get('level', '')})")

if __name__ == '__main__':
    asyncio.run(main())
