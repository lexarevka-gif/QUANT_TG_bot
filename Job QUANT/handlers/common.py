from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from handlers.admin import is_admin
from keyboards import admin_keyboard, worker_keyboard

router = Router()


@router.message(Command("help"))
@router.message(F.text == "📖 Помощь")
async def cmd_help(message: Message):
    admin = await is_admin(message.from_user.id)
    kb = admin_keyboard() if admin else worker_keyboard()

    text = "📖 Список команд:\n\n"
    text += "👤 Общие:\n"
    text += "/start — регистрация\n"
    text += "/profile — мой профиль\n"
    text += "/my_tasks — мои задачи\n"
    text += "/my_stats — моя статистика и заработок\n"
    text += "/available_tasks — доступные задачи\n"
    text += "/help — эта справка\n"

    text += "\n📸 Фотоотчёт:\n"
    text += "/photo_start <ID> — фото начала работы\n"
    text += "/photo_end <ID> — фото конца работы\n"

    if admin:
        text += "\n🔧 Админ:\n"
        text += "/new_task — создать задачу\n"
        text += "/templates — шаблоны задач\n"
        text += "/broadcast_task <ID> — разослать задачу\n"
        text += "/send_details <ID> — отправить подробности\n"
        text += "/close_task <ID> — закрыть задачу\n"
        text += "/cancel_task <ID> — отменить задачу\n"
        text += "/tasks — список задач (с фильтром)\n"
        text += "/workers — работники (по рангам)\n"
        text += "/divisions — подразделения\n"
        text += "/mass_send — рассылка всем\n"
        text += "/payroll — расчёт оплаты\n"
        text += "/payroll_xlsx — экспорт в Excel\n"

    await message.answer(text, reply_markup=kb)


@router.message(F.text == "📸 Фотоотчёт")
async def btn_photo_report(message: Message):
    from handlers.photo_report import cmd_photo_report
    await cmd_photo_report(message)


@router.message(F.text == "📢 Доступные задачи")
async def btn_available_tasks(message: Message):
    from handlers.tasks import cmd_available_tasks
    await cmd_available_tasks(message)


@router.message(F.text == "👤 Профиль")
async def btn_profile(message: Message):
    from handlers.payment import cmd_profile
    await cmd_profile(message)


@router.message(F.text == "📋 Мои задачи")
async def btn_my_tasks(message: Message):
    from handlers.payment import cmd_my_tasks
    await cmd_my_tasks(message)


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
    from handlers.payment import cmd_payroll
    await cmd_payroll(message)


@router.message(F.text == "📤 Экспорт Excel")
async def btn_export_xlsx(message: Message):
    from handlers.payment import cmd_payroll_xlsx
    await cmd_payroll_xlsx(message)


@router.message(F.text == "📢 Рассылка")
async def btn_mass_send(message: Message, state: FSMContext):
    from handlers.admin import cmd_mass_send
    await cmd_mass_send(message, state)


@router.message(F.text == "📋 Шаблоны")
async def btn_templates(message: Message):
    from handlers.admin import cmd_templates
    await cmd_templates(message)


@router.message(F.text == "🏢 Подразделения")
async def btn_divisions(message: Message):
    from handlers.admin import cmd_divisions
    await cmd_divisions(message)
