from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def worker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True,
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Новая задача"), KeyboardButton(text="📋 Задачи")],
            [KeyboardButton(text="👥 Работники"), KeyboardButton(text="💰 Расчёт оплаты")],
            [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True,
    )
