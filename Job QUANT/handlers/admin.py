import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from database import async_session
from models import User, Task, TaskApplication, TaskPoint, TaskStatus, ApplicationStatus, Division
from config import (
    ADMIN_IDS, ADMIN_MIN_RANK, RANKS, JOB_ROLES,
    rank_name, PAYMENT_TIERS, get_monthly_coefficient,
    BROADCAST_BATCH_SIZE, BROADCAST_DELAY,
)
from datetime import datetime

router = Router()


async def is_admin(tg_id: int) -> bool:
    if tg_id in ADMIN_IDS:
        return True
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == tg_id)
        )).scalar_one_or_none()
        return user is not None and user.rank >= ADMIN_MIN_RANK


class CreateTask(StatesGroup):
    division = State()
    title = State()
    description = State()
    date = State()
    time = State()
    location = State()
    points = State()
    quota = State()
    role_filter = State()
    payment = State()


class SetRank(StatesGroup):
    user_select = State()
    rank_value = State()


class SendDetails(StatesGroup):
    task_select = State()
    details_text = State()


class BroadcastMessage(StatesGroup):
    waiting_text = State()


class SearchWorker(StatesGroup):
    waiting_query = State()


class AddDivision(StatesGroup):
    name = State()
    counts_coeff = State()


class CreateFromTemplate(StatesGroup):
    date = State()
    time = State()
    edit_title = State()
    edit_desc = State()
    edit_location = State()
    edit_division = State()


class EditTask(StatesGroup):
    title = State()
    description = State()
    date = State()
    time = State()
    location = State()


# ====================================================================
# Создание задачи
# ====================================================================

@router.message(Command("new_task"))
async def cmd_new_task(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("У вас нет прав администратора.")
        return

    async with async_session() as session:
        divs = (await session.execute(select(Division).order_by(Division.id))).scalars().all()

    buttons = []
    for d in divs:
        buttons.append([InlineKeyboardButton(text=d.name, callback_data=f"newtaskdiv_{d.id}")])
    buttons.append([InlineKeyboardButton(text="Без подразделения", callback_data="newtaskdiv_0")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите подразделение для задачи:", reply_markup=kb)


@router.callback_query(F.data.startswith("newtaskdiv_"))
async def cb_new_task_div(callback: CallbackQuery, state: FSMContext):
    div_id = int(callback.data.split("_")[1])
    await state.update_data(division_id=div_id if div_id else None)
    await state.set_state(CreateTask.title)
    await callback.message.answer("Введите название задачи:")
    await callback.answer()


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
    await state.set_state(CreateTask.points)
    await message.answer(
        "📍 Укажите точки задачи (адрес | вместимость), каждую с новой строки.\n"
        "Пример:\nул. Ленина 10 | 2\nпр. Мира 5 | 3\n\n"
        "Или '-' если точек нет:"
    )


@router.message(CreateTask.points)
async def task_points(message: Message, state: FSMContext):
    txt = message.text.strip()
    if txt == "-":
        await state.update_data(points_data=[])
    else:
        points_data = []
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" not in line:
                await message.answer("Неверный формат. Используйте: адрес | вместимость\nПопробуйте ещё раз:")
                return
            parts = line.split("|", 1)
            address = parts[0].strip()
            try:
                capacity = int(parts[1].strip())
            except ValueError:
                await message.answer(f"Вместимость должна быть числом: «{parts[1].strip()}»\nПопробуйте ещё раз:")
                return
            if capacity < 1:
                await message.answer("Вместимость должна быть >= 1. Попробуйте ещё раз:")
                return
            points_data.append({"address": address, "capacity": capacity})
        await state.update_data(points_data=points_data)

    await state.set_state(CreateTask.quota)
    await message.answer("Максимальное количество работников (или '-' без ограничения):")


@router.message(CreateTask.quota)
async def task_quota(message: Message, state: FSMContext):
    txt = message.text.strip()
    if txt == "-":
        await state.update_data(max_workers=None)
    else:
        try:
            await state.update_data(max_workers=int(txt))
        except ValueError:
            await message.answer("Введите число или '-'. Попробуйте ещё раз:")
            return

    buttons = []
    for role in JOB_ROLES:
        if role != "Без роли":
            buttons.append([InlineKeyboardButton(text=role, callback_data=f"rolefilter_{role}")])
    buttons.append([InlineKeyboardButton(text="Для всех", callback_data="rolefilter_all")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Для какой роли задача?", reply_markup=kb)


@router.callback_query(F.data.startswith("rolefilter_"))
async def cb_role_filter(callback: CallbackQuery, state: FSMContext):
    role = callback.data.split("_", 1)[1]
    role_filter = None if role == "all" else role
    await state.update_data(job_role_filter=role_filter)
    await state.set_state(CreateTask.payment)

    tier_lines = "\n".join(f"  {name}" for _, name in sorted(PAYMENT_TIERS.items()))
    await callback.message.answer(
        f"💰 Укажите оплату за задачу.\n"
        f"Введите сумму для каждого уровня (каждую с новой строки):\n\n"
        f"{tier_lines}\n\n"
        f"Пример:\n500\n600\n700\n800\n1000\n\n"
        f"Если у всех одинаковая ставка — введите одно число"
    )
    await callback.answer()


@router.message(CreateTask.payment)
async def task_payment(message: Message, state: FSMContext):
    text = message.text.strip()
    tier_ids = sorted(PAYMENT_TIERS.keys())
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) == 1:
        try:
            single_rate = float(lines[0])
        except ValueError:
            await message.answer("Введите число. Попробуйте ещё раз:")
            return
        rates = {r: single_rate for r in tier_ids}
    else:
        if len(lines) != len(tier_ids):
            await message.answer(
                f"Нужно {len(tier_ids)} значений, а вы ввели {len(lines)}.\nПопробуйте ещё раз:"
            )
            return
        try:
            values = [float(line) for line in lines]
        except ValueError:
            await message.answer("Все значения должны быть числами. Попробуйте ещё раз:")
            return
        rates = dict(zip(tier_ids, values))

    data = await state.get_data()

    async with async_session() as session:
        task = Task(
            title=data["title"],
            description=data["description"],
            date=data["date"],
            time=data["time"],
            location=data.get("location"),
            division_id=data.get("division_id"),
            max_workers=data.get("max_workers"),
            job_role_filter=data.get("job_role_filter"),
            created_by=message.from_user.id,
        )
        task.payment_rates = rates
        session.add(task)
        await session.flush()
        task_id = task.id

        points_data = data.get("points_data", [])
        for pd in points_data:
            session.add(TaskPoint(task_id=task_id, address=pd["address"], capacity=pd["capacity"]))

        div_name = ""
        if task.division_id:
            div = (await session.execute(select(Division).where(Division.id == task.division_id))).scalar_one_or_none()
            div_name = div.name if div else ""

        await session.commit()

    await state.clear()

    summary = f"Задача #{task_id} создана!\n\n"
    summary += f"📋 {data['title']}\n"
    if data.get("description"):
        summary += f"📝 {data['description']}\n"
    summary += f"📅 {data['date']}\n"
    if data.get("time"):
        summary += f"🕐 {data['time']}\n"
    if data.get("location"):
        summary += f"📍 {data['location']}\n"
    if div_name:
        summary += f"🏢 {div_name}\n"
    if data.get("max_workers"):
        summary += f"👥 Макс: {data['max_workers']}\n"
    if data.get("job_role_filter"):
        summary += f"💼 Роль: {data['job_role_filter']}\n"

    if points_data:
        summary += "\n📍 Точки:\n"
        for pd in points_data:
            summary += f"  • {pd['address']} (мест: {pd['capacity']})\n"

    summary += "\n💰 Оплата:\n"
    for tier_id in sorted(rates.keys()):
        summary += f"  {PAYMENT_TIERS[tier_id]}: {rates[tier_id]:.0f}₽\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Отправить работникам", callback_data=f"broadcast_{task_id}")],
        [InlineKeyboardButton(text="💾 Сохранить как шаблон", callback_data=f"savetempl_{task_id}")],
    ])
    await message.answer(summary, reply_markup=kb)


# ====================================================================
# Шаблоны
# ====================================================================

@router.callback_query(F.data.startswith("savetempl_"))
async def cb_save_template(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.is_template = True
            await session.commit()

    await callback.answer("Шаблон сохранён!", show_alert=True)


@router.message(Command("templates"))
async def cmd_templates(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await _show_templates_list(message.answer)


async def _show_templates_list(reply_func, edit_func=None):
    async with async_session() as session:
        templates = (await session.execute(
            select(Task).where(Task.is_template == True).order_by(Task.id.desc())
        )).scalars().all()

    if not templates:
        if edit_func:
            await edit_func("Шаблонов нет.")
        else:
            await reply_func("Шаблонов нет.")
        return

    buttons = []
    for t in templates:
        buttons.append([InlineKeyboardButton(
            text=f"📋 {t.title}", callback_data=f"templcard_{t.id}"
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = "📋 Шаблоны — выберите шаблон:"
    if edit_func:
        await edit_func(text, reply_markup=kb)
    else:
        await reply_func(text, reply_markup=kb)


@router.callback_query(F.data.startswith("templcard_"))
async def cb_template_card(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    tmpl_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        tmpl = (await session.execute(select(Task).where(Task.id == tmpl_id))).scalar_one_or_none()
        if not tmpl:
            await callback.answer("Шаблон не найден.", show_alert=True)
            return

        div_name = ""
        if tmpl.division_id:
            div = (await session.execute(select(Division).where(Division.id == tmpl.division_id))).scalar_one_or_none()
            div_name = div.name if div else ""

    text = f"📋 Шаблон: {tmpl.title}\n\n"
    if tmpl.description:
        text += f"📝 {tmpl.description}\n"
    if tmpl.location:
        text += f"📍 {tmpl.location}\n"
    if div_name:
        text += f"🏢 {div_name}\n"
    if tmpl.max_workers:
        text += f"👥 Макс: {tmpl.max_workers}\n"
    if tmpl.job_role_filter:
        text += f"💼 Роль: {tmpl.job_role_filter}\n"

    rates = tmpl.payment_rates
    if rates:
        text += "\n💰 Оплата:\n"
        for tier_id in sorted(rates.keys()):
            text += f"  {PAYMENT_TIERS.get(tier_id, f'Уровень {tier_id}')}: {rates[tier_id]:.0f}₽\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать задачу из шаблона", callback_data=f"usetempl_{tmpl_id}")],
        [InlineKeyboardButton(text="🗑 Удалить шаблон", callback_data=f"deltempl_{tmpl_id}")],
        [InlineKeyboardButton(text="◀️ Назад к шаблонам", callback_data="templs_back")],
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "templs_back")
async def cb_templates_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    await _show_templates_list(callback.message.answer, callback.message.edit_text)
    await callback.answer()


@router.callback_query(F.data.startswith("deltempl_"))
async def cb_delete_template(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    tmpl_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        tmpl = (await session.execute(select(Task).where(Task.id == tmpl_id))).scalar_one_or_none()
        if tmpl:
            tmpl.is_template = False
            await session.commit()

    await callback.answer("Шаблон удалён.", show_alert=True)
    await _show_templates_list(callback.message.answer, callback.message.edit_text)


# --- Создание задачи из шаблона ---

@router.callback_query(F.data.startswith("usetempl_"))
async def cb_use_template(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    template_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        tmpl = (await session.execute(select(Task).where(Task.id == template_id))).scalar_one_or_none()
        if not tmpl:
            await callback.answer("Шаблон не найден.", show_alert=True)
            return

        await state.update_data(
            template_id=template_id,
            t_title=tmpl.title,
            t_description=tmpl.description,
            t_time_override=tmpl.time,
            t_location=tmpl.location,
            t_division_id=tmpl.division_id,
            t_max_workers=tmpl.max_workers,
            t_job_role_filter=tmpl.job_role_filter,
            t_payment_rates_json=tmpl.payment_rates_json,
        )

    await state.set_state(CreateFromTemplate.date)
    await callback.message.answer("📅 Введите дату задачи (например, 25.08.2026):")
    await callback.answer()


@router.message(CreateFromTemplate.date)
async def template_date(message: Message, state: FSMContext):
    await state.update_data(t_date=message.text.strip())
    await state.set_state(CreateFromTemplate.time)
    data = await state.get_data()
    current_time = data.get("t_time_override") or "не задано"
    await message.answer(f"🕐 Введите время или '-' чтобы оставить ({current_time}):")


@router.message(CreateFromTemplate.time)
async def template_time(message: Message, state: FSMContext):
    txt = message.text.strip()
    if txt != "-":
        await state.update_data(t_time_override=txt)
    await state.set_state(None)
    await _show_template_summary(message, state)


async def _show_template_summary(msg_or_cb, state: FSMContext):
    data = await state.get_data()

    title = data.get("t_title", "—")
    desc = data.get("t_description") or "—"
    date = data.get("t_date", "—")
    time_val = data.get("t_time_override") or "—"
    location = data.get("t_location") or "—"
    div_id = data.get("t_division_id")
    max_w = data.get("t_max_workers")
    role_f = data.get("t_job_role_filter") or "все"

    div_name = "—"
    if div_id:
        async with async_session() as session:
            div = (await session.execute(select(Division).where(Division.id == div_id))).scalar_one_or_none()
            div_name = div.name if div else "—"

    text = "📋 Задача из шаблона — проверьте данные:\n\n"
    text += f"📌 Название: {title}\n"
    text += f"📝 Описание: {desc}\n"
    text += f"📅 Дата: {date}\n"
    text += f"🕐 Время: {time_val}\n"
    text += f"📍 Место: {location}\n"
    text += f"🏢 Подразделение: {div_name}\n"
    text += f"👥 Макс. работников: {max_w or 'без ограничения'}\n"
    text += f"💼 Роль: {role_f}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Название", callback_data="tmpledit_title"),
         InlineKeyboardButton(text="✏️ Описание", callback_data="tmpledit_desc")],
        [InlineKeyboardButton(text="✏️ Место", callback_data="tmpledit_location"),
         InlineKeyboardButton(text="✏️ Подразделение", callback_data="tmpledit_division")],
        [InlineKeyboardButton(text="✅ Создать задачу", callback_data="tmplcreate")],
    ])

    reply = msg_or_cb.answer if hasattr(msg_or_cb, "answer") else msg_or_cb.message.answer
    await reply(text, reply_markup=kb)


@router.callback_query(F.data == "tmpledit_title")
async def cb_tmpl_edit_title(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateFromTemplate.edit_title)
    await callback.message.answer("Введите новое название:")
    await callback.answer()


@router.message(CreateFromTemplate.edit_title)
async def tmpl_new_title(message: Message, state: FSMContext):
    await state.update_data(t_title=message.text.strip())
    await state.set_state(None)
    await _show_template_summary(message, state)


@router.callback_query(F.data == "tmpledit_desc")
async def cb_tmpl_edit_desc(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateFromTemplate.edit_desc)
    await callback.message.answer("Введите новое описание (или '-' убрать):")
    await callback.answer()


@router.message(CreateFromTemplate.edit_desc)
async def tmpl_new_desc(message: Message, state: FSMContext):
    val = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(t_description=val)
    await state.set_state(None)
    await _show_template_summary(message, state)


@router.callback_query(F.data == "tmpledit_location")
async def cb_tmpl_edit_location(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateFromTemplate.edit_location)
    await callback.message.answer("Введите новое место (или '-' убрать):")
    await callback.answer()


@router.message(CreateFromTemplate.edit_location)
async def tmpl_new_location(message: Message, state: FSMContext):
    val = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(t_location=val)
    await state.set_state(None)
    await _show_template_summary(message, state)


@router.callback_query(F.data == "tmpledit_division")
async def cb_tmpl_edit_division(callback: CallbackQuery, state: FSMContext):
    async with async_session() as session:
        divs = (await session.execute(select(Division).order_by(Division.id))).scalars().all()

    buttons = []
    for d in divs:
        buttons.append([InlineKeyboardButton(text=d.name, callback_data=f"tmplsetdiv_{d.id}")])
    buttons.append([InlineKeyboardButton(text="Без подразделения", callback_data="tmplsetdiv_0")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Выберите подразделение:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("tmplsetdiv_"))
async def cb_tmpl_set_div(callback: CallbackQuery, state: FSMContext):
    div_id = int(callback.data.split("_")[1])
    await state.update_data(t_division_id=div_id if div_id else None)
    await _show_template_summary(callback, state)
    await callback.answer()


@router.callback_query(F.data == "tmplcreate")
async def cb_tmpl_create(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    data = await state.get_data()

    async with async_session() as session:
        task = Task(
            title=data["t_title"],
            description=data.get("t_description"),
            date=data["t_date"],
            time=data.get("t_time_override"),
            location=data.get("t_location"),
            division_id=data.get("t_division_id"),
            max_workers=data.get("t_max_workers"),
            job_role_filter=data.get("t_job_role_filter"),
            payment_rates_json=data.get("t_payment_rates_json", "{}"),
            created_by=callback.from_user.id,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Отправить работникам", callback_data=f"broadcast_{task_id}")]
    ])
    await callback.message.answer(
        f"✅ Задача #{task_id} создана!\n"
        f"📋 {data['t_title']}\n📅 {data['t_date']}",
        reply_markup=kb,
    )
    await callback.answer()


# ====================================================================
# Рассылка задачи (с троттлингом и поддержкой точек)
# ====================================================================

async def _broadcast_task(bot, task_id: int, reply_func):
    async with async_session() as session:
        result = await session.execute(
            select(Task).options(selectinload(Task.points)).where(Task.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            await reply_func("Задача не найдена.")
            return

        query = select(User).where(User.role == "worker")
        workers = (await session.execute(query)).scalars().all()

        if task.job_role_filter:
            workers = [w for w in workers if task.job_role_filter in w.job_roles]

        div_name = ""
        if task.division_id:
            div = (await session.execute(select(Division).where(Division.id == task.division_id))).scalar_one_or_none()
            div_name = div.name if div else ""

        points = task.points

    has_points = bool(points)

    sent = 0
    errors = 0
    for i, worker in enumerate(workers):
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
        if div_name:
            text += f"🏢 {div_name}\n"
        text += f"\n💰 Ваша оплата ({rank_name(worker.rank)}): {worker_rate:.0f}₽"
        if task.max_workers:
            text += f"\n👥 Мест: {task.max_workers}"

        if has_points:
            text += "\n\n📍 Выберите точку:"
            buttons = []
            for pt in points:
                buttons.append([InlineKeyboardButton(
                    text=f"📍 {pt.address} (мест: {pt.capacity})",
                    callback_data=f"applypoint_{task.id}_{pt.id}",
                )])
            kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Записаться", callback_data=f"apply_{task.id}")]
            ])

        try:
            await bot.send_message(worker.tg_id, text, reply_markup=kb)
            sent += 1
        except Exception:
            errors += 1

        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_DELAY)

    result_text = f"Задача отправлена {sent} работникам."
    if errors:
        result_text += f"\n⚠️ Ошибок: {errors}"
    await reply_func(result_text)


@router.message(Command("broadcast_task"))
async def cmd_broadcast_task(message: Message):
    if not await is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /broadcast_task <ID задачи>")
        return
    await _broadcast_task(message.bot, int(args[1]), message.answer)


@router.callback_query(F.data.startswith("broadcast_"))
async def cb_broadcast_task(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    task_id = int(callback.data.split("_")[1])
    await callback.answer("Рассылка...")
    await _broadcast_task(callback.bot, task_id, callback.message.answer)


# ====================================================================
# Отправка подробностей (с троттлингом)
# ====================================================================

@router.message(Command("send_details"))
async def cmd_send_details(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
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
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await message.answer("Задача не найдена.")
            await state.clear()
            return
        task.details = details
        await session.commit()

        apps = (await session.execute(
            select(TaskApplication).where(
                TaskApplication.task_id == task_id,
                TaskApplication.status != ApplicationStatus.NOT_SHOWED.value,
            )
        )).scalars().all()
        user_ids = [app.user_id for app in apps]
        if not user_ids:
            await message.answer("На эту задачу никто не записался.")
            await state.clear()
            return
        users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()

    text = f"📋 Подробности по задаче #{task_id} — {task.title}\n\n{details}"
    sent = 0
    errors = 0
    for i, user in enumerate(users):
        try:
            await message.bot.send_message(user.tg_id, text)
            sent += 1
        except Exception:
            errors += 1
        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_DELAY)

    await state.clear()
    result_text = f"Подробности отправлены {sent} работникам."
    if errors:
        result_text += f"\n⚠️ Ошибок: {errors}"
    await message.answer(result_text)


# ====================================================================
# Массовая рассылка (с троттлингом)
# ====================================================================

@router.message(Command("mass_send"))
async def cmd_mass_send(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastMessage.waiting_text)
    await message.answer("Введите сообщение для рассылки всем работникам:")


@router.message(BroadcastMessage.waiting_text)
async def process_mass_send(message: Message, state: FSMContext):
    text = message.text.strip()
    await state.clear()
    async with async_session() as session:
        workers = (await session.execute(select(User).where(User.role == "worker"))).scalars().all()
    sent = 0
    errors = 0
    for i, w in enumerate(workers):
        try:
            await message.bot.send_message(w.tg_id, f"📢 Сообщение от администрации:\n\n{text}")
            sent += 1
        except Exception:
            errors += 1
        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_DELAY)
    result_text = f"Сообщение отправлено {sent} работникам."
    if errors:
        result_text += f"\n⚠️ Ошибок: {errors}"
    await message.answer(result_text)


# ====================================================================
# Отмена задачи
# ====================================================================

@router.message(Command("cancel_task"))
async def cmd_cancel_task(message: Message):
    if not await is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /cancel_task <ID задачи>")
        return
    task_id = int(args[1])
    await _cancel_task(message.bot, task_id, message.answer)


async def _cancel_task(bot, task_id: int, reply_func):
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await reply_func("Задача не найдена.")
            return
        if task.status == TaskStatus.CANCELLED.value:
            await reply_func("Задача уже отменена.")
            return

        task.status = TaskStatus.CANCELLED.value
        apps = (await session.execute(
            select(TaskApplication).where(TaskApplication.task_id == task_id)
        )).scalars().all()
        await session.commit()

        notified = 0
        for app in apps:
            user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()
            if user:
                try:
                    await bot.send_message(
                        user.tg_id,
                        f"🚫 Задача #{task_id} — {task.title} отменена администратором."
                    )
                    notified += 1
                except Exception:
                    pass

    await reply_func(f"🚫 Задача #{task_id} отменена. Уведомлено: {notified}")


# ====================================================================
# Подразделения
# ====================================================================

@router.message(Command("divisions"))
async def cmd_divisions(message: Message):
    if not await is_admin(message.from_user.id):
        return
    await _show_divisions(message.answer)


async def _show_divisions(reply_func, edit_func=None):
    async with async_session() as session:
        divs = (await session.execute(select(Division).order_by(Division.id))).scalars().all()

    if not divs:
        text = "Подразделений нет."
        buttons = [[InlineKeyboardButton(text="➕ Добавить подразделение", callback_data="add_division")]]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        if edit_func:
            await edit_func(text, reply_markup=kb)
        else:
            await reply_func(text, reply_markup=kb)
        return

    buttons = []
    for d in divs:
        coeff_mark = "✅" if d.counts_for_coeff else "❌"
        buttons.append([
            InlineKeyboardButton(text=f"{d.name} — коэфф: {coeff_mark}", callback_data=f"divinfo_{d.id}"),
            InlineKeyboardButton(
                text="Убрать коэфф" if d.counts_for_coeff else "Дать коэфф",
                callback_data=f"divtoggle_{d.id}",
            ),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить подразделение", callback_data="add_division")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = "🏢 Подразделения:\n(нажмите кнопку справа чтобы вкл/выкл коэффициент)"
    if edit_func:
        await edit_func(text, reply_markup=kb)
    else:
        await reply_func(text, reply_markup=kb)


@router.callback_query(F.data.startswith("divtoggle_"))
async def cb_toggle_div_coeff(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    div_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        div = (await session.execute(select(Division).where(Division.id == div_id))).scalar_one_or_none()
        if div:
            div.counts_for_coeff = not div.counts_for_coeff
            await session.commit()
            new_state = "включён" if div.counts_for_coeff else "выключен"

    await callback.answer(f"Коэффициент {new_state} для «{div.name}»", show_alert=True)
    await _show_divisions(callback.message.answer, callback.message.edit_text)


@router.callback_query(F.data.startswith("divinfo_"))
async def cb_div_info(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "add_division")
async def cb_add_division(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(AddDivision.name)
    await callback.message.answer("Введите название нового подразделения:")
    await callback.answer()


@router.message(AddDivision.name)
async def add_division_name(message: Message, state: FSMContext):
    await state.update_data(div_name=message.text.strip())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="divcoeff_yes")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="divcoeff_no")],
    ])
    await message.answer("Учитывать в коэффициенте?", reply_markup=kb)


@router.callback_query(F.data.startswith("divcoeff_"))
async def cb_div_coeff(callback: CallbackQuery, state: FSMContext):
    counts = callback.data == "divcoeff_yes"
    data = await state.get_data()
    name = data["div_name"]
    await state.clear()

    async with async_session() as session:
        session.add(Division(name=name, counts_for_coeff=counts))
        await session.commit()

    await callback.answer("Подразделение добавлено!", show_alert=True)
    await callback.message.answer(f"🏢 Подразделение «{name}» добавлено.")


# ====================================================================
# Работники по рангам
# ====================================================================

@router.message(Command("workers"))
async def cmd_workers(message: Message):
    if not await is_admin(message.from_user.id):
        return

    buttons = []
    for rank_id, name in sorted(RANKS.items()):
        buttons.append([InlineKeyboardButton(text=f"🏅 {name}", callback_data=f"wrkrank_{rank_id}")])
    buttons.append([InlineKeyboardButton(text="👥 Все работники", callback_data="wrkrank_all")])
    buttons.append([InlineKeyboardButton(text="🔍 Поиск по имени", callback_data="wrksearch")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👥 Работники — выберите ранг:", reply_markup=kb)


@router.callback_query(F.data.startswith("wrkrank_"))
async def cb_workers_by_rank(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    rank_key = callback.data.split("_", 1)[1]

    async with async_session() as session:
        if rank_key == "all":
            result = await session.execute(select(User).where(User.role == "worker"))
            title = "👥 Все работники"
        else:
            rank_id = int(rank_key)
            result = await session.execute(select(User).where(User.role == "worker", User.rank == rank_id))
            title = f"👥 {rank_name(rank_id)}"
        workers = result.scalars().all()

    if not workers:
        await callback.answer("В этой категории нет работников.", show_alert=True)
        return

    buttons = []
    for w in workers:
        roles = ", ".join(w.job_roles) if w.job_roles else w.job_role
        label = f"{w.full_name} | {rank_name(w.rank)} | {roles}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"worker_{w.id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_ranks")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"{title} ({len(workers)}):", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "back_ranks")
async def cb_back_ranks(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    buttons = []
    for rank_id, name in sorted(RANKS.items()):
        buttons.append([InlineKeyboardButton(text=f"🏅 {name}", callback_data=f"wrkrank_{rank_id}")])
    buttons.append([InlineKeyboardButton(text="👥 Все работники", callback_data="wrkrank_all")])
    buttons.append([InlineKeyboardButton(text="🔍 Поиск по имени", callback_data="wrksearch")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("👥 Работники — выберите ранг:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "wrksearch")
async def cb_search_worker(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        return
    await state.set_state(SearchWorker.waiting_query)
    await callback.message.answer("🔍 Введите имя или часть имени для поиска:")
    await callback.answer()


@router.message(SearchWorker.waiting_query)
async def process_search_worker(message: Message, state: FSMContext):
    query = message.text.strip().lower()
    await state.clear()
    async with async_session() as session:
        all_workers = (await session.execute(select(User).where(User.role == "worker"))).scalars().all()
    found = [w for w in all_workers if query in w.full_name.lower()]
    if not found:
        await message.answer(f"По запросу «{message.text.strip()}» никого не найдено.")
        return
    buttons = []
    for w in found:
        roles = ", ".join(w.job_roles) if w.job_roles else w.job_role
        label = f"{w.full_name} | {rank_name(w.rank)} | {roles}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"worker_{w.id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"🔍 Найдено ({len(found)}):", reply_markup=kb)


# ====================================================================
# Карточка работника (с задачами / коэфф / оплатой)
# ====================================================================

async def _calc_worker_monthly_stats(user_id: int, user_rank: int):
    now = datetime.utcnow()
    month, year = now.month, now.year
    month_start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        month_end = f"{year + 1:04d}-01-01"
    else:
        month_end = f"{year:04d}-{month + 1:02d}-01"

    async with async_session() as session:
        confirmed_apps = (await session.execute(
            select(TaskApplication)
            .options(selectinload(TaskApplication.task))
            .where(
                TaskApplication.user_id == user_id,
                TaskApplication.status == ApplicationStatus.CONFIRMED.value,
            )
        )).scalars().all()

        coeff_divs = set((await session.execute(
            select(Division.id).where(Division.counts_for_coeff == True)
        )).scalars().all())

        penalties = (await session.execute(
            select(func.coalesce(func.sum(TaskApplication.penalty_amount), 0))
            .where(
                TaskApplication.user_id == user_id,
                TaskApplication.status == ApplicationStatus.PENALTY.value,
            )
        )).scalar() or 0

    month_apps = []
    for app in confirmed_apps:
        td = _parse_task_date_str(app.task.date)
        if td and month_start <= td < month_end:
            month_apps.append(app)

    coeff_count = sum(
        1 for app in month_apps
        if app.task.division_id and app.task.division_id in coeff_divs
    )
    coeff = get_monthly_coefficient(coeff_count)
    base_sum = sum(app.task.rate_for_rank(user_rank) for app in month_apps)
    earned = base_sum * coeff - penalties

    return len(confirmed_apps), len(month_apps), coeff, earned, penalties


def _parse_task_date_str(date_str: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


async def _worker_card(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    total_confirmed, month_confirmed, coeff, earned, penalties = await _calc_worker_monthly_stats(user_id, user.rank)

    roles = ", ".join(user.job_roles) if user.job_roles else user.job_role
    now = datetime.utcnow()
    text = (
        f"👤 {user.full_name}\n"
        f"📱 {user.phone or '—'}\n"
        f"🏅 Ранг: {rank_name(user.rank)}\n"
        f"💼 Роли: {roles}\n\n"
        f"📊 Статистика ({now.strftime('%m.%Y')}):\n"
        f"  Задач за месяц: {month_confirmed}\n"
        f"  Задач всего: {total_confirmed}\n"
        f"  Коэффициент: x{coeff}\n"
        f"  Штрафы: -{penalties:.0f}₽\n"
        f"  Заработано: {earned:.0f}₽\n\n"
        f"tg_id: {user.tg_id}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏅 Изменить ранг", callback_data=f"chrank_{user.id}")],
        [InlineKeyboardButton(text="💼 Изменить роли", callback_data=f"chrole_{user.id}")],
        [InlineKeyboardButton(text="🗑 Удалить работника", callback_data=f"delworker_{user.id}")],
        [InlineKeyboardButton(text="◀️ Назад к списку", callback_data="back_ranks")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("worker_"))
async def cb_worker_card(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])
    text, kb = await _worker_card(user_id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# --- Ранг ---

@router.callback_query(F.data.startswith("chrank_"))
async def cb_change_rank(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
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
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    user_id, rank = int(parts[1]), int(parts[2])
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        user.rank = rank
        await session.commit()
    await callback.answer(f"Ранг → {rank_name(rank)}", show_alert=True)
    text, kb = await _worker_card(user_id)
    await callback.message.edit_text(text, reply_markup=kb)


# --- Роли (множественные) ---

@router.callback_query(F.data.startswith("chrole_"))
async def cb_change_roles(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        current_roles = set(user.job_roles)

    buttons = []
    for role in JOB_ROLES:
        mark = "✅ " if role in current_roles else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{role}", callback_data=f"togglerole_{user_id}_{role}")])
    buttons.append([InlineKeyboardButton(text="◀️ Готово", callback_data=f"worker_{user_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Нажмите на роль чтобы добавить/убрать:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("togglerole_"))
async def cb_toggle_role(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_", 2)
    user_id, role = int(parts[1]), parts[2]

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        roles = set(user.job_roles)
        if role in roles:
            roles.discard(role)
        else:
            roles.add(role)
        user.job_roles = list(roles)
        user.job_role = ", ".join(roles) if roles else "Без роли"
        await session.commit()
        current_roles = set(user.job_roles)

    buttons = []
    for r in JOB_ROLES:
        mark = "✅ " if r in current_roles else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{r}", callback_data=f"togglerole_{user_id}_{r}")])
    buttons.append([InlineKeyboardButton(text="◀️ Готово", callback_data=f"worker_{user_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Нажмите на роль чтобы добавить/убрать:", reply_markup=kb)
    await callback.answer()


# --- Удаление ---

@router.callback_query(F.data.startswith("delworker_"))
async def cb_delete_worker_confirm(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Да, удалить", callback_data=f"confirmdelete_{user_id}"),
        InlineKeyboardButton(text="◀️ Отмена", callback_data=f"worker_{user_id}"),
    ]])
    await callback.message.edit_text(f"Удалить работника {user.full_name}?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("confirmdelete_"))
async def cb_confirm_delete(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split("_")[1])
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user:
            await session.execute(TaskApplication.__table__.delete().where(TaskApplication.user_id == user_id))
            await session.delete(user)
            await session.commit()
            name = user.full_name
    await callback.answer(f"{name} удалён", show_alert=True)
    await callback.message.edit_text(f"✅ Работник {name} удалён.")


# ====================================================================
# Задачи с фильтром
# ====================================================================

STATUS_LABELS = {
    TaskStatus.OPEN.value: "📂 Открытые",
    TaskStatus.IN_PROGRESS.value: "🔄 В работе",
    TaskStatus.COMPLETED.value: "✅ Завершённые",
    TaskStatus.CANCELLED.value: "🚫 Отменённые",
}


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    if not await is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Открытые", callback_data="taskfilter_open")],
        [InlineKeyboardButton(text="✅ Завершённые", callback_data="taskfilter_completed")],
        [InlineKeyboardButton(text="🚫 Отменённые", callback_data="taskfilter_cancelled")],
        [InlineKeyboardButton(text="📋 Все задачи", callback_data="taskfilter_all")],
    ])
    await message.answer("📋 Задачи — выберите фильтр:", reply_markup=kb)


@router.callback_query(F.data.startswith("taskfilter_"))
async def cb_task_filter(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    filter_key = callback.data.split("_", 1)[1]
    async with async_session() as session:
        if filter_key == "all":
            result = await session.execute(select(Task).where(Task.is_template == False).order_by(Task.id.desc()).limit(30))
            title = "📋 Все задачи"
        else:
            result = await session.execute(
                select(Task).where(Task.status == filter_key, Task.is_template == False).order_by(Task.id.desc()).limit(30)
            )
            title = STATUS_LABELS.get(filter_key, f"📋 {filter_key}")
        tasks = result.scalars().all()

    if not tasks:
        await callback.answer("Задач не найдено.", show_alert=True)
        return

    buttons = []
    for t in tasks:
        buttons.append([InlineKeyboardButton(text=f"#{t.id} {t.title} — {t.date}", callback_data=f"taskcard_{t.id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к фильтрам", callback_data="tasks_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"{title} ({len(tasks)}):", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "tasks_back")
async def cb_tasks_back(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Открытые", callback_data="taskfilter_open")],
        [InlineKeyboardButton(text="✅ Завершённые", callback_data="taskfilter_completed")],
        [InlineKeyboardButton(text="🚫 Отменённые", callback_data="taskfilter_cancelled")],
        [InlineKeyboardButton(text="📋 Все задачи", callback_data="taskfilter_all")],
    ])
    await callback.message.edit_text("📋 Задачи — выберите фильтр:", reply_markup=kb)
    await callback.answer()


# --- Карточка задачи ---

@router.callback_query(F.data.startswith("taskcard_"))
async def cb_task_card(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return
        apps = (await session.execute(
            select(TaskApplication).where(TaskApplication.task_id == task_id)
        )).scalars().all()

        div_name = ""
        if task.division_id:
            div = (await session.execute(select(Division).where(Division.id == task.division_id))).scalar_one_or_none()
            div_name = div.name if div else ""

    total = len(apps)
    confirmed = sum(1 for a in apps if a.status == ApplicationStatus.CONFIRMED.value)
    not_showed = sum(1 for a in apps if a.status == ApplicationStatus.NOT_SHOWED.value)
    penalty = sum(1 for a in apps if a.status == ApplicationStatus.PENALTY.value)
    in_progress = total - confirmed - not_showed - penalty

    text = f"📋 Задача #{task.id}\n\n"
    text += f"📌 {task.title}\n"
    if task.description:
        text += f"📝 {task.description}\n"
    text += f"📅 {task.date}\n"
    if task.time:
        text += f"🕐 {task.time}\n"
    if task.location:
        text += f"📍 {task.location}\n"
    if div_name:
        text += f"🏢 {div_name}\n"
    text += f"📊 Статус: {STATUS_LABELS.get(task.status, task.status)}\n"
    if task.max_workers:
        text += f"👥 Макс: {task.max_workers}\n"

    text += f"\n📈 Статистика:\n"
    text += f"  Записалось: {total}"
    if task.max_workers:
        text += f" / {task.max_workers}"
    text += f"\n  В процессе: {in_progress}\n"
    text += f"  Подтверждено: {confirmed}\n"
    text += f"  Не пришли: {not_showed}\n"
    text += f"  Штрафы: {penalty}\n"

    buttons = []
    if task.status == TaskStatus.OPEN.value:
        buttons.append([InlineKeyboardButton(text="📢 Разослать работникам", callback_data=f"broadcast_{task_id}")])
    if task.status in (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value):
        buttons.append([InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edittask_{task_id}")])
        buttons.append([InlineKeyboardButton(text="🔒 Закрыть задачу", callback_data=f"closetask_{task_id}")])
        buttons.append([InlineKeyboardButton(text="🚫 Отменить задачу", callback_data=f"canceltask_{task_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="tasks_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("closetask_"))
async def cb_close_task(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task or task.status == TaskStatus.COMPLETED.value:
            await callback.answer("Задача уже закрыта.", show_alert=True)
            return
        task.status = TaskStatus.COMPLETED.value
        apps = (await session.execute(
            select(TaskApplication).where(TaskApplication.task_id == task_id)
        )).scalars().all()
        no_show = 0
        for app in apps:
            if app.status in (ApplicationStatus.APPLIED.value, ApplicationStatus.PHOTO_START.value):
                app.status = ApplicationStatus.NOT_SHOWED.value
                no_show += 1
        await session.commit()
        for app in apps:
            if app.status == ApplicationStatus.NOT_SHOWED.value:
                user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()
                if user:
                    try:
                        await callback.bot.send_message(user.tg_id,
                            f"❌ Задача #{task_id} — {task.title} закрыта.\nСтатус: не пришёл.")
                    except Exception:
                        pass

    await callback.answer(f"Закрыта. Не пришли: {no_show}", show_alert=True)
    await cb_task_card(callback)


@router.callback_query(F.data.startswith("canceltask_"))
async def cb_cancel_task(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])
    await _cancel_task(callback.bot, task_id, callback.message.answer)
    await callback.answer("Задача отменена.", show_alert=True)


@router.message(Command("close_task"))
async def cmd_close_task(message: Message):
    if not await is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /close_task <ID задачи>")
        return
    task_id = int(args[1])
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await message.answer("Задача не найдена.")
            return
        if task.status == TaskStatus.COMPLETED.value:
            await message.answer("Задача уже закрыта.")
            return
        task.status = TaskStatus.COMPLETED.value
        apps = (await session.execute(
            select(TaskApplication).where(TaskApplication.task_id == task_id)
        )).scalars().all()
        no_show = 0
        for app in apps:
            if app.status in (ApplicationStatus.APPLIED.value, ApplicationStatus.PHOTO_START.value):
                app.status = ApplicationStatus.NOT_SHOWED.value
                no_show += 1
        await session.commit()
        notified = 0
        for app in apps:
            if app.status == ApplicationStatus.NOT_SHOWED.value:
                user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()
                if user:
                    try:
                        await message.bot.send_message(user.tg_id,
                            f"❌ Задача #{task_id} — {task.title} закрыта.\nСтатус: не пришёл.")
                        notified += 1
                    except Exception:
                        pass
    await message.answer(f"✅ Задача #{task_id} закрыта.\nНе пришли: {no_show}\nУведомлено: {notified}")


# ====================================================================
# Редактирование задачи
# ====================================================================

async def _show_edit_menu(callback_or_msg, task_id: int):
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            return

        div_name = "—"
        if task.division_id:
            div = (await session.execute(select(Division).where(Division.id == task.division_id))).scalar_one_or_none()
            div_name = div.name if div else "—"

    text = f"✏️ Редактирование задачи #{task.id}\n\n"
    text += f"📌 Название: {task.title}\n"
    text += f"📝 Описание: {task.description or '—'}\n"
    text += f"📅 Дата: {task.date}\n"
    text += f"🕐 Время: {task.time or '—'}\n"
    text += f"📍 Место: {task.location or '—'}\n"
    text += f"🏢 Подразделение: {div_name}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Название", callback_data=f"tasked_t_{task_id}"),
         InlineKeyboardButton(text="📝 Описание", callback_data=f"tasked_d_{task_id}")],
        [InlineKeyboardButton(text="📅 Дата", callback_data=f"tasked_dt_{task_id}"),
         InlineKeyboardButton(text="🕐 Время", callback_data=f"tasked_tm_{task_id}")],
        [InlineKeyboardButton(text="📍 Место", callback_data=f"tasked_l_{task_id}"),
         InlineKeyboardButton(text="🏢 Подразд.", callback_data=f"tasked_dv_{task_id}")],
        [InlineKeyboardButton(text="📢 Уведомить работников", callback_data=f"tasknotify_{task_id}")],
        [InlineKeyboardButton(text="◀️ К задаче", callback_data=f"taskcard_{task_id}")],
    ])

    reply = getattr(callback_or_msg, "answer", None)
    if reply:
        await reply(text, reply_markup=kb)
    else:
        await callback_or_msg.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("edittask_"))
async def cb_edit_task(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])
    await _show_edit_menu(callback, task_id)
    await callback.answer()


@router.callback_query(F.data.startswith("tasked_t_"))
async def cb_edit_title(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(EditTask.title)
    await callback.message.answer("Введите новое название:")
    await callback.answer()


@router.message(EditTask.title)
async def edit_title_done(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["edit_task_id"]
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.title = message.text.strip()
            await session.commit()
    await state.set_state(None)
    await _show_edit_menu(message, task_id)


@router.callback_query(F.data.startswith("tasked_d_"))
async def cb_edit_desc(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(EditTask.description)
    await callback.message.answer("Введите новое описание (или '-' убрать):")
    await callback.answer()


@router.message(EditTask.description)
async def edit_desc_done(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["edit_task_id"]
    val = None if message.text.strip() == "-" else message.text.strip()
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.description = val
            await session.commit()
    await state.set_state(None)
    await _show_edit_menu(message, task_id)


@router.callback_query(F.data.startswith("tasked_dt_"))
async def cb_edit_date(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(EditTask.date)
    await callback.message.answer("Введите новую дату (например, 25.08.2026):")
    await callback.answer()


@router.message(EditTask.date)
async def edit_date_done(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["edit_task_id"]
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.date = message.text.strip()
            await session.commit()
    await state.set_state(None)
    await _show_edit_menu(message, task_id)


@router.callback_query(F.data.startswith("tasked_tm_"))
async def cb_edit_time(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(EditTask.time)
    await callback.message.answer("Введите новое время (например, 10:00) или '-' убрать:")
    await callback.answer()


@router.message(EditTask.time)
async def edit_time_done(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["edit_task_id"]
    val = None if message.text.strip() == "-" else message.text.strip()
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.time = val
            await session.commit()
    await state.set_state(None)
    await _show_edit_menu(message, task_id)


@router.callback_query(F.data.startswith("tasked_l_"))
async def cb_edit_location(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(EditTask.location)
    await callback.message.answer("Введите новое место (или '-' убрать):")
    await callback.answer()


@router.message(EditTask.location)
async def edit_location_done(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["edit_task_id"]
    val = None if message.text.strip() == "-" else message.text.strip()
    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.location = val
            await session.commit()
    await state.set_state(None)
    await _show_edit_menu(message, task_id)


@router.callback_query(F.data.startswith("tasked_dv_"))
async def cb_edit_division(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        divs = (await session.execute(select(Division).order_by(Division.id))).scalars().all()

    buttons = []
    for d in divs:
        buttons.append([InlineKeyboardButton(text=d.name, callback_data=f"tasksetdv_{task_id}_{d.id}")])
    buttons.append([InlineKeyboardButton(text="Без подразделения", callback_data=f"tasksetdv_{task_id}_0")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"edittask_{task_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("Выберите подразделение:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("tasksetdv_"))
async def cb_set_task_division(callback: CallbackQuery):
    parts = callback.data.split("_")
    task_id, div_id = int(parts[1]), int(parts[2])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if task:
            task.division_id = div_id if div_id else None
            await session.commit()

    await callback.answer("Подразделение обновлено.", show_alert=True)
    await _show_edit_menu(callback, task_id)


# --- Уведомление работников об изменениях ---

@router.callback_query(F.data.startswith("tasknotify_"))
async def cb_notify_workers(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return

        apps = (await session.execute(
            select(TaskApplication).where(TaskApplication.task_id == task_id)
        )).scalars().all()

        div_name = ""
        if task.division_id:
            div = (await session.execute(select(Division).where(Division.id == task.division_id))).scalar_one_or_none()
            div_name = div.name if div else ""

    text = f"📢 Изменения в задаче #{task.id}!\n\n"
    text += f"📌 {task.title}\n"
    if task.description:
        text += f"📝 {task.description}\n"
    text += f"📅 {task.date}\n"
    if task.time:
        text += f"🕐 {task.time}\n"
    if task.location:
        text += f"📍 {task.location}\n"
    if div_name:
        text += f"🏢 {div_name}\n"

    sent = 0
    for i, app in enumerate(apps):
        async with async_session() as session:
            user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()
        if user:
            try:
                await callback.bot.send_message(user.tg_id, text)
                sent += 1
            except Exception:
                pass
        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_DELAY)

    await callback.answer(f"Уведомлено: {sent}", show_alert=True)
