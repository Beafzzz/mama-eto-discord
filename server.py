import asyncio
import websockets
import json
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# Хранилище для комнат и клиентов
rooms = {}

async def handle_client(websocket):
    async for message in websocket:
        data = json.loads(message)
        room_id = data.get("room")
        if not room_id:
            continue

        # Логируем подключение клиента
        logging.info(f"Client connected to room {room_id}")

        # Создаем комнату, если она не существует
        if room_id not in rooms:
            rooms[room_id] = []
            logging.info(f"Room {room_id} created")

        # Присоединяем клиента к комнате
        if websocket not in rooms[room_id]:
            rooms[room_id].append(websocket)
            logging.info(f"Client joined room {room_id}")

        # Отправляем список подключенных пользователей
        await websocket.send(json.dumps({
            "type": "user_list",
            "users": [str(id(client)) for client in rooms[room_id]]
        }))

        # Пересылаем сообщение другим участникам комнаты
        if data["type"] in ["offer", "answer", "candidate"]:
            for client in rooms[room_id]:
                if client != websocket:
                    await client.send(json.dumps(data))
                    logging.info(f"Message forwarded in room {room_id}")

        # Удаляем клиента из комнаты при отключении
        try:
            await websocket.wait_closed()
        finally:
            if room_id in rooms:
                rooms[room_id].remove(websocket)
                logging.info(f"Client left room {room_id}")
                if not rooms[room_id]:
                    del rooms[room_id]
                    logging.info(f"Room {room_id} deleted")

# Запуск WebSocket-сервера
async def main():
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        logging.info("WebSocket server started on ws://0.0.0.0:8765")
        await asyncio.Future()  # Бесконечный цикл для поддержания работы сервера

# Запуск сервера
if __name__ == "__main__":
    asyncio.run(main())