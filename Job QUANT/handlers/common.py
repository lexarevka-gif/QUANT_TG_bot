from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from config import ADMIN_IDS, RANKS
from keyboards import admin_keyboard, worker_keyboard

router = Router()


@router.message(Command("help"))
@router.message(F.text == "📖 Помощь")
async def cmd_help(message: Message):
    is_admin = message.from_user.id in ADMIN_IDS
    kb = admin_keyboard() if is_admin else worker_keyboard()

    text = "📖 Список команд:\n\n"
    text += "👤 Общие:\n"
    text += "/start — регистрация\n"
    text += "/my_stats — моя статистика и заработок\n"
    text += "/help — эта справка\n"

    text += "\n📸 Фотоотчёт:\n"
    text += "/photo_start <ID> — фото начала работы\n"
    text += "/photo_end <ID> — фото конца работы\n"

    if is_admin:
        text += "\n🔧 Админ:\n"
        text += "/new_task — создать задачу\n"
        text += "/broadcast_task <ID> — разослать задачу работникам\n"
        text += "/send_details <ID> — отправить подробности записавшимся\n"
        text += "/tasks — список задач\n"
        text += "/workers — список работников\n"
        rank_list = ", ".join(f"{k}={v}" for k, v in sorted(RANKS.items()))
        text += f"/set_rank <tg_id> <ранг> — ранги: {rank_list}\n"
        text += "/payroll — расчёт оплаты всех работников\n"

    await message.answer(text, reply_markup=kb)


@router.message(F.text == "📊 Моя статистика")
async def btn_my_stats(message: Message):
    from handlers.payment import cmd_my_stats
    await cmd_my_stats(message)


@router.message(F.text == "➕ Новая задача")
async def btn_new_task(message: Message, state: FSMContext):
    from handlers.admin import cmd_new_task
    await cmd_new_task(message, state)


@router.message(F.text == "📋 Задачи")
async def btn_tasks(message: Message):
    from handlers.admin import cmd_tasks
    await cmd_tasks(message)


@router.message(F.text == "👥 Работники")
async def btn_workers(message: Message):
    from handlers.admin import cmd_workers
    await cmd_workers(message)


@router.message(F.text == "💰 Расчёт оплаты")
async def btn_payroll(message: Message):
    from handlers.admin import cmd_payroll
    await cmd_payroll(message)
