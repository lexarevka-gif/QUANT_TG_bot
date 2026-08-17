import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from config import BOT_TOKEN
from database import init_db
from handlers import routers
from handlers.common import router as common_router


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Регистрация"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="my_stats", description="Моя статистика"),
        BotCommand(command="new_task", description="Создать задачу (админ)"),
        BotCommand(command="tasks", description="Список задач (админ)"),
        BotCommand(command="workers", description="Список работников (админ)"),
        BotCommand(command="payroll", description="Расчёт оплаты (админ)"),
    ]
    await bot.set_my_commands(commands)


async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(common_router)
    for r in routers:
        dp.include_router(r)

    await init_db()
    await set_commands(bot)
    logging.info("Бот запущен")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
