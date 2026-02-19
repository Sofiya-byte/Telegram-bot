import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from database import init_db
from handlers import register_handlers
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

load_dotenv()

API_TOKEN = os.getenv("TOKEN")


async def main():
    init_db()
    print("Database initialized")

    bot = Bot(token=API_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    register_handlers(dp)

    print("Bot started...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")
