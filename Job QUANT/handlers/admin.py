from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from database import async_session
from models import User, Task, TaskApplication, TaskStatus, ApplicationStatus
from config import ADMIN_IDS, RANKS, JOB_ROLES, rank_name
from datetime import datetime

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


class CreateTask(StatesGroup):
    title = State()
    description = State()
    date = State()
    time = State()
    location = State()
    payment = State()


class SetRank(StatesGroup):
    user_select = State()
    rank_value = State()


class SendDetails(StatesGroup):
    task_select = State()
    details_text = State()


# --- Создание задачи ---

@router.message(Command("new_task"))
async def cmd_new_task(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав администратора.")
        return
    await state.set_state(CreateTask.title)
    await message.answer("Создание новой задачи.\nВведите название задачи:")


@router.message(CreateTask.title)
async def task_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(CreateTask.description)
    await message.answer("Введите описание задачи (или '-' чтобы пропустить):")


@router.message(CreateTask.description)
async def task_description(message: Message, state: FSMContext):
    desc = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(description=desc)
    await state.set_state(CreateTask.date)
    await message.answer("Введите дату задачи (например, 25.08.2026):")


@router.message(CreateTask.date)
async def task_date(message: Message, state: FSMContext):
    await state.update_data(date=message.text.strip())
    await state.set_state(CreateTask.time)
    await message.answer("Введите время задачи (например, 10:00) или '-' чтобы пропустить:")


@router.message(CreateTask.time)
async def task_time(message: Message, state: FSMContext):
    time_val = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(time=time_val)
    await state.set_state(CreateTask.location)
    await message.answer("Введите место задачи (или '-' чтобы пропустить):")


@router.message(CreateTask.location)
async def task_location(message: Message, state: FSMContext):
    location = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(location=location)
    await state.set_state(CreateTask.payment)

    rank_lines = "\n".join(f"  {name}" for _, name in sorted(RANKS.items()))
    await message.answer(
        f"💰 Укажите оплату за задачу.\n"
        f"Введите сумму для каждого ранга (каждую с новой строки):\n\n"
        f"{rank_lines}\n\n"
        f"Пример:\n500\n600\n700\n800\n1000\n1200\n\n"
        f"Если у всех одинаковая ставка — введите одно число"
    )


@router.message(CreateTask.payment)
async def task_payment(message: Message, state: FSMContext):
    text = message.text.strip()
    rank_ids = sorted(RANKS.keys())
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) == 1:
        try:
            single_rate = float(lines[0])
        except ValueError:
            await message.answer("Введите число. Попробуйте ещё раз:")
            return
        rates = {r: single_rate for r in rank_ids}
    else:
        if len(lines) != len(rank_ids):
            await message.answer(
                f"Нужно {len(rank_ids)} значений (по числу рангов), а вы ввели {len(lines)}.\n"
                f"Попробуйте ещё раз:"
            )
            return
        try:
            values = [float(line) for line in lines]
        except ValueError:
            await message.answer("Все значения должны быть числами. Попробуйте ещё раз:")
            return
        rates = dict(zip(rank_ids, values))

    data = await state.get_data()

    async with async_session() as session:
        task = Task(
            title=data["title"],
            description=data["description"],
            date=data["date"],
            time=data["time"],
            location=data["location"],
            created_by=message.from_user.id,
        )
        task.payment_rates = rates
        session.add(task)
        await session.commit()
        task_id = task.id

    await state.clear()

    summary = f"Задача #{task_id} создана!\n\n"
    summary += f"📋 {data['title']}\n"
    if data["description"]:
        summary += f"📝 {data['description']}\n"
    summary += f"📅 {data['date']}\n"
    if data.get("time"):
        summary += f"🕐 {data['time']}\n"
    if data.get("location"):
        summary += f"📍 {data['location']}\n"

    summary += "\n💰 Оплата:\n"
    for rank_id in sorted(rates.keys()):
        summary += f"  {rank_name(rank_id)}: {rates[rank_id]:.0f}₽\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Отправить всем работникам", callback_data=f"broadcast_{task_id}")]
    ])
    await message.answer(summary, reply_markup=kb)


# --- Рассылка задачи ---

async def _broadcast_task(bot, task_id: int, reply_func):
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            await reply_func("Задача не найдена.")
            return

        workers = await session.execute(select(User).where(User.role == "worker"))
        workers = workers.scalars().all()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Записаться", callback_data=f"apply_{task.id}")]
    ])

    sent = 0
    for worker in workers:
        worker_rate = task.rate_for_rank(worker.rank)

        text = f"📢 Новая задача #{task.id}!\n\n"
        text += f"📋 {task.title}\n"
        if task.description:
            text += f"📝 {task.description}\n"
        text += f"📅 {task.date}\n"
        if task.time:
            text += f"🕐 {task.time}\n"
        if task.location:
            text += f"📍 {task.location}\n"
        text += f"\n💰 Ваша оплата ({rank_name(worker.rank)}): {worker_rate:.0f}₽"

        try:
            await bot.send_message(worker.tg_id, text, reply_markup=kb)
            sent += 1
        except Exception:
            pass

    await reply_func(f"Задача отправлена {sent} работникам.")


@router.message(Command("broadcast_task"))
async def cmd_broadcast_task(message: Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /broadcast_task <ID задачи>")
        return

    await _broadcast_task(message.bot, int(args[1]), message.answer)


@router.callback_query(F.data.startswith("broadcast_"))
async def cb_broadcast_task(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    task_id = int(callback.data.split("_")[1])
    await callback.answer("Рассылка...")
    await _broadcast_task(callback.bot, task_id, callback.message.answer)


# --- Отправка подробностей записавшимся ---

@router.message(Command("send_details"))
async def cmd_send_details(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /send_details <ID задачи>")
        return

    task_id = int(args[1])
    await state.update_data(task_id=task_id)
    await state.set_state(SendDetails.details_text)
    await message.answer("Введите подробности для отправки записавшимся работникам:")


@router.message(SendDetails.details_text)
async def send_details_text(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    details = message.text.strip()

    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            await message.answer("Задача не найдена.")
            await state.clear()
            return

        task.details = details
        await session.commit()

        apps = await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.status != ApplicationStatus.NOT_SHOWED.value)
        )
        apps = apps.scalars().all()

        user_ids = [app.user_id for app in apps]
        if not user_ids:
            await message.answer("На эту задачу никто не записался.")
            await state.clear()
            return

        users = await session.execute(select(User).where(User.id.in_(user_ids)))
        users = users.scalars().all()

    text = f"📋 Подробности по задаче #{task_id} — {task.title}\n\n{details}"
    sent = 0
    for user in users:
        try:
            await message.bot.send_message(user.tg_id, text)
            sent += 1
        except Exception:
            pass

    await state.clear()
    await message.answer(f"Подробности отправлены {sent} работникам.")


# --- Список работников (с кнопками) ---

@router.message(Command("workers"))
async def cmd_workers(message: Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        result = await session.execute(select(User).where(User.role == "worker"))
        workers = result.scalars().all()

    if not workers:
        await message.answer("Работников пока нет.")
        return

    buttons = []
    for w in workers:
        label = f"{w.full_name} | {rank_name(w.rank)} | {w.job_role}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"worker_{w.id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👥 Работники (нажмите для управления):", reply_markup=kb)


# --- Карточка работника ---

async def _worker_card(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        confirmed = (await session.execute(
            select(func.count())
            .where(TaskApplication.user_id == user_id)
            .where(TaskApplication.status == ApplicationStatus.CONFIRMED.value)
        )).scalar() or 0

    text = (
        f"👤 {user.full_name}\n"
        f"📱 {user.phone or '—'}\n"
        f"🏅 Ранг: {rank_name(user.rank)}\n"
        f"💼 Роль: {user.job_role}\n"
        f"✅ Задач выполнено: {confirmed}\n"
        f"tg_id: {user.tg_id}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏅 Изменить ранг", callback_data=f"chrank_{user.id}")],
        [InlineKeyboardButton(text="💼 Изменить роль", callback_data=f"chrole_{user.id}")],
        [InlineKeyboardButton(text="🗑 Удалить работника", callback_data=f"delworker_{user.id}")],
        [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="back_workers")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("worker_"))
async def cb_worker_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])
    text, kb = await _worker_card(user_id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "back_workers")
async def cb_back_workers(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    async with async_session() as session:
        result = await session.execute(select(User).where(User.role == "worker"))
        workers = result.scalars().all()

    if not workers:
        await callback.message.edit_text("Работников пока нет.")
        await callback.answer()
        return

    buttons = []
    for w in workers:
        label = f"{w.full_name} | {rank_name(w.rank)} | {w.job_role}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"worker_{w.id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("👥 Работники (нажмите для управления):", reply_markup=kb)
    await callback.answer()


# --- Изменение ранга ---

@router.callback_query(F.data.startswith("chrank_"))
async def cb_change_rank(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])

    buttons = []
    for rank_id, name in sorted(RANKS.items()):
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"setrank_{user_id}_{rank_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"worker_{user_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Выберите новый ранг:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("setrank_"))
async def cb_set_rank(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    user_id = int(parts[1])
    rank = int(parts[2])

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        user.rank = rank
        await session.commit()

    await callback.answer(f"Ранг → {rank_name(rank)}", show_alert=True)
    text, kb = await _worker_card(user_id)
    await callback.message.edit_text(text, reply_markup=kb)


# --- Изменение роли ---

@router.callback_query(F.data.startswith("chrole_"))
async def cb_change_role(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])

    buttons = []
    for role in JOB_ROLES:
        buttons.append([InlineKeyboardButton(text=role, callback_data=f"setrole_{user_id}_{role}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"worker_{user_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Выберите роль:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("setrole_"))
async def cb_set_role(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_", 2)
    user_id = int(parts[1])
    job_role = parts[2]

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        user.job_role = job_role
        await session.commit()

    await callback.answer(f"Роль → {job_role}", show_alert=True)
    text, kb = await _worker_card(user_id)
    await callback.message.edit_text(text, reply_markup=kb)


# --- Удаление работника ---

@router.callback_query(F.data.startswith("delworker_"))
async def cb_delete_worker_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Да, удалить", callback_data=f"confirmdelete_{user_id}"),
            InlineKeyboardButton(text="◀️ Отмена", callback_data=f"worker_{user_id}"),
        ]
    ])
    await callback.message.edit_text(
        f"Удалить работника {user.full_name}?\nЭто действие необратимо.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirmdelete_"))
async def cb_confirm_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user:
            await session.execute(
                TaskApplication.__table__.delete().where(TaskApplication.user_id == user_id)
            )
            await session.delete(user)
            await session.commit()
            name = user.full_name

    await callback.answer(f"{name} удалён", show_alert=True)
    await callback.message.edit_text(f"✅ Работник {name} удалён.")


async def _get_confirmed_count(user_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .where(TaskApplication.user_id == user_id)
            .where(TaskApplication.status == ApplicationStatus.CONFIRMED.value)
        )
        return result.scalar() or 0


# --- Список задач ---

@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        result = await session.execute(select(Task).order_by(Task.id.desc()).limit(20))
        tasks = result.scalars().all()

    if not tasks:
        await message.answer("Задач пока нет.")
        return

    lines = []
    for t in tasks:
        app_count = await _get_app_count(t.id)
        lines.append(f"#{t.id} [{t.status}] {t.title} — {t.date} | записалось: {app_count}")

    await message.answer("📋 Задачи:\n\n" + "\n".join(lines))


async def _get_app_count(task_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).where(TaskApplication.task_id == task_id)
        )
        return result.scalar() or 0
