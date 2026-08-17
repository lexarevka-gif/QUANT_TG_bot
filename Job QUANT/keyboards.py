from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def worker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Доступные задачи"), KeyboardButton(text="📋 Мои задачи")],
            [KeyboardButton(text="📸 Фотоотчёт"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True,
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Новая задача"), KeyboardButton(text="📋 Задачи")],
            [KeyboardButton(text="👥 Работники"), KeyboardButton(text="💰 Расчёт оплаты")],
            [KeyboardButton(text="📤 Экспорт Excel"), KeyboardButton(text="📢 Рассылка")],
            [KeyboardButton(text="📋 Шаблоны"), KeyboardButton(text="🏢 Подразделения")],
            [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="📖 Помощь")],
        ],
        resize_keyboard=True,
    )
