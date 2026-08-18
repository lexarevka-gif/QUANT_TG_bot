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
from models import (
    User, Task, TaskApplication, TaskPoint, TaskTemplate, TaskTemplatePoint,
    TaskStatus, ApplicationStatus,
)
from config import (
    ADMIN_IDS, RANKS, JOB_ROLES, WORKERS_PER_PAGE,
    BROADCAST_BATCH_SIZE, BROADCAST_DELAY, rank_name,
)

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


# ──────────────── FSM States ────────────────


class CreateTask(StatesGroup):
    title = State()
    description = State()
    date = State()
    time = State()
    choose_type = State()
    location = State()
    points = State()
    payment = State()


class CreateFromTemplate(StatesGroup):
    date = State()
    time = State()


class SaveTemplate(StatesGroup):
    name = State()


class SendDetails(StatesGroup):
    task_select = State()
    details_text = State()


# ──────────────── Создание задачи ────────────────


@router.message(Command("new_task"))
async def cmd_new_task(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("У вас нет прав администратора.")
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать с нуля", callback_data="newtask_scratch")],
        [InlineKeyboardButton(text="📋 Из шаблона", callback_data="newtask_tpl")],
    ])
    await message.answer("Создание новой задачи:", reply_markup=kb)


@router.callback_query(F.data == "newtask_scratch")
async def cb_new_task_scratch(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(CreateTask.title)
    await callback.message.edit_text("Введите название задачи:")
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
    await state.set_state(CreateTask.choose_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Одна локация", callback_data="tasktype_single")],
        [InlineKeyboardButton(text="📍 Несколько точек", callback_data="tasktype_multi")],
    ])
    await message.answer("Тип задачи:", reply_markup=kb)


@router.message(CreateTask.choose_type)
async def task_choose_type_hint(message: Message, state: FSMContext):
    await message.answer("Нажмите одну из кнопок выше ⬆️")


@router.callback_query(F.data == "tasktype_single")
async def cb_tasktype_single(callback: CallbackQuery, state: FSMContext):
    await state.update_data(task_points=None)
    await state.set_state(CreateTask.location)
    await callback.message.edit_text("Введите адрес (или '-' чтобы пропустить):")
    await callback.answer()


@router.callback_query(F.data == "tasktype_multi")
async def cb_tasktype_multi(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CreateTask.points)
    await callback.message.edit_text(
        "Введите точки, каждую с новой строки:\n"
        "Формат: адрес | количество человек\n\n"
        "Пример:\n"
        "ул. Ленина 5 | 2\n"
        "пр. Мира 10 | 1\n"
        "ул. Гагарина 3 | 2"
    )
    await callback.answer()


@router.message(CreateTask.location)
async def task_location(message: Message, state: FSMContext):
    location = None if message.text.strip() == "-" else message.text.strip()
    await state.update_data(location=location)
    await _ask_payment(message, state)


@router.message(CreateTask.points)
async def task_points(message: Message, state: FSMContext):
    lines = [line.strip() for line in message.text.strip().splitlines() if line.strip()]
    points_data = []
    for line in lines:
        if "|" not in line:
            await message.answer(
                f"Неверный формат строки: {line}\n"
                "Используйте: адрес | количество\nПопробуйте ещё раз:"
            )
            return
        parts = line.rsplit("|", 1)
        address = parts[0].strip()
        try:
            capacity = int(parts[1].strip())
        except ValueError:
            await message.answer(
                f"Количество должно быть числом: {line}\nПопробуйте ещё раз:"
            )
            return
        if capacity < 1:
            await message.answer("Количество должно быть минимум 1. Попробуйте ещё раз:")
            return
        points_data.append({"address": address, "capacity": capacity})

    if not points_data:
        await message.answer("Введите хотя бы одну точку:")
        return

    await state.update_data(task_points=points_data, location=None)
    await _ask_payment(message, state)


async def _ask_payment(message: Message, state: FSMContext):
    await state.set_state(CreateTask.payment)
    rank_lines = "\n".join(f"  {name}" for _, name in sorted(RANKS.items()))
    await message.answer(
        f"💰 Укажите оплату за задачу.\n"
        f"Введите сумму для каждого ранга (каждую с новой строки):\n\n"
        f"{rank_lines}\n\n"
        f"Пример:\n500\n600\n700\n800\n1000\n\n"
        f"Если у всех одинаковая ставка — введите одно число"
    )


@router.message(CreateTask.payment)
async def task_payment(message: Message, state: FSMContext):
    rates = _parse_rates(message.text.strip())
    if rates is None:
        await message.answer("Неверный формат. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        task = Task(
            title=data["title"],
            description=data["description"],
            date=data["date"],
            time=data["time"],
            location=data.get("location"),
            created_by=message.from_user.id,
        )
        task.payment_rates = rates
        session.add(task)
        await session.flush()

        points_data = data.get("task_points")
        if points_data:
            for p in points_data:
                session.add(TaskPoint(task_id=task.id, address=p["address"], capacity=p["capacity"]))

        await session.commit()
        task_id = task.id

    summary = _task_summary(task_id, data, rates, points_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Отправить всем работникам", callback_data=f"broadcast_{task_id}")],
        [InlineKeyboardButton(text="💾 Сохранить как шаблон", callback_data=f"savetpl_{task_id}")],
    ])
    await message.answer(summary, reply_markup=kb)


# ──────────────── Создание из шаблона ────────────────


@router.callback_query(F.data == "newtask_tpl")
async def cb_new_task_template(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    async with async_session() as session:
        templates = (await session.execute(
            select(TaskTemplate).order_by(TaskTemplate.name)
        )).scalars().all()

    if not templates:
        await callback.answer("Шаблонов пока нет. Создайте задачу с нуля и сохраните как шаблон.", show_alert=True)
        return

    buttons = [[InlineKeyboardButton(text=t.name, callback_data=f"usetpl_{t.id}")] for t in templates]
    buttons.append([InlineKeyboardButton(text="🗑 Удалить шаблон", callback_data="deltpl_list")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="newtask_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Выберите шаблон:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "newtask_back")
async def cb_newtask_back(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать с нуля", callback_data="newtask_scratch")],
        [InlineKeyboardButton(text="📋 Из шаблона", callback_data="newtask_tpl")],
    ])
    await callback.message.edit_text("Создание новой задачи:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("usetpl_"))
async def cb_use_template(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    template_id = int(callback.data.split("_")[1])
    await state.update_data(template_id=template_id)
    await state.set_state(CreateFromTemplate.date)

    async with async_session() as session:
        tpl = (await session.execute(
            select(TaskTemplate).options(selectinload(TaskTemplate.template_points))
            .where(TaskTemplate.id == template_id)
        )).scalar_one_or_none()

    if not tpl:
        await callback.answer("Шаблон не найден.", show_alert=True)
        return

    info = f"📋 Шаблон: {tpl.name}\n"
    info += f"Задача: {tpl.title}\n"
    if tpl.template_points:
        info += f"Точек: {len(tpl.template_points)}\n"
    elif tpl.location:
        info += f"📍 {tpl.location}\n"
    info += "\nВведите дату задачи (например, 25.08.2026):"

    await callback.message.edit_text(info)
    await callback.answer()


@router.message(CreateFromTemplate.date)
async def template_date(message: Message, state: FSMContext):
    await state.update_data(date=message.text.strip())
    await state.set_state(CreateFromTemplate.time)
    await message.answer("Введите время задачи (например, 10:00) или '-' чтобы пропустить:")


@router.message(CreateFromTemplate.time)
async def template_time(message: Message, state: FSMContext):
    time_val = None if message.text.strip() == "-" else message.text.strip()
    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        tpl = (await session.execute(
            select(TaskTemplate).options(selectinload(TaskTemplate.template_points))
            .where(TaskTemplate.id == data["template_id"])
        )).scalar_one_or_none()

        if not tpl:
            await message.answer("Шаблон не найден.")
            return

        task = Task(
            title=tpl.title,
            description=tpl.description,
            date=data["date"],
            time=time_val,
            location=tpl.location,
            created_by=message.from_user.id,
        )
        task.payment_rates = tpl.payment_rates
        session.add(task)
        await session.flush()

        points_data = None
        if tpl.template_points:
            points_data = []
            for tp in tpl.template_points:
                session.add(TaskPoint(task_id=task.id, address=tp.address, capacity=tp.capacity))
                points_data.append({"address": tp.address, "capacity": tp.capacity})

        await session.commit()
        task_id = task.id
        rates = tpl.payment_rates

    tpl_data = {
        "title": tpl.title,
        "description": tpl.description,
        "date": data["date"],
        "time": time_val,
        "location": tpl.location,
    }
    summary = _task_summary(task_id, tpl_data, rates, points_data)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Отправить всем работникам", callback_data=f"broadcast_{task_id}")],
    ])
    await message.answer(summary, reply_markup=kb)


# ──────────────── Сохранение шаблона ────────────────


@router.callback_query(F.data.startswith("savetpl_"))
async def cb_save_template(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    task_id = int(callback.data.split("_")[1])
    await state.update_data(save_task_id=task_id)
    await state.set_state(SaveTemplate.name)
    await callback.message.answer("Введите название шаблона:")
    await callback.answer()


@router.message(SaveTemplate.name)
async def save_template_name(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["save_task_id"]
    tpl_name = message.text.strip()
    await state.clear()

    async with async_session() as session:
        task = (await session.execute(
            select(Task).options(selectinload(Task.points)).where(Task.id == task_id)
        )).scalar_one_or_none()

        if not task:
            await message.answer("Задача не найдена.")
            return

        tpl = TaskTemplate(
            name=tpl_name,
            title=task.title,
            description=task.description,
            location=task.location,
            created_by=message.from_user.id,
        )
        tpl.payment_rates = task.payment_rates
        session.add(tpl)
        await session.flush()

        for point in task.points:
            session.add(TaskTemplatePoint(
                template_id=tpl.id, address=point.address, capacity=point.capacity,
            ))

        await session.commit()

    await message.answer(f"✅ Шаблон «{tpl_name}» сохранён.")


# ──────────────── Удаление шаблона ────────────────


@router.callback_query(F.data == "deltpl_list")
async def cb_delete_template_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    async with async_session() as session:
        templates = (await session.execute(
            select(TaskTemplate).order_by(TaskTemplate.name)
        )).scalars().all()

    if not templates:
        await callback.answer("Шаблонов нет.", show_alert=True)
        return

    buttons = [[InlineKeyboardButton(text=f"🗑 {t.name}", callback_data=f"deltpl_{t.id}")] for t in templates]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="newtask_tpl")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Выберите шаблон для удаления:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("deltpl_"))
async def cb_delete_template(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    tpl_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        tpl = (await session.execute(
            select(TaskTemplate).where(TaskTemplate.id == tpl_id)
        )).scalar_one_or_none()
        if tpl:
            name = tpl.name
            await session.delete(tpl)
            await session.commit()

    await callback.answer(f"Шаблон «{name}» удалён.", show_alert=True)
    await cb_delete_template_list(callback)


# ──────────────── Рассылка задачи (с throttling) ────────────────


async def _broadcast_task(bot, task_id: int, reply_func):
    async with async_session() as session:
        task = (await session.execute(
            select(Task).options(selectinload(Task.points)).where(Task.id == task_id)
        )).scalar_one_or_none()
        if not task:
            await reply_func("Задача не найдена.")
            return

        workers = (await session.execute(
            select(User).where(User.role == "worker")
        )).scalars().all()

        point_counts = {}
        if task.points:
            for point in task.points:
                count = (await session.execute(
                    select(func.count()).where(TaskApplication.point_id == point.id)
                )).scalar() or 0
                point_counts[point.id] = count

    if not workers:
        await reply_func("Работников нет.")
        return

    sent, errors = 0, 0
    for i, worker in enumerate(workers):
        worker_rate = task.rate_for_rank(worker.rank)

        text = f"📢 Новая задача #{task.id}!\n\n"
        text += f"📋 {task.title}\n"
        if task.description:
            text += f"📝 {task.description}\n"
        text += f"📅 {task.date}\n"
        if task.time:
            text += f"🕐 {task.time}\n"

        if task.points:
            text += "\n📍 Точки:\n"
            for p in task.points:
                cnt = point_counts.get(p.id, 0)
                text += f"  • {p.address} ({cnt}/{p.capacity})\n"
            text += f"\n💰 Ваша оплата ({rank_name(worker.rank)}): {worker_rate:.0f}₽"
            buttons = []
            for p in task.points:
                cnt = point_counts.get(p.id, 0)
                if cnt < p.capacity:
                    buttons.append([InlineKeyboardButton(
                        text=f"📍 {p.address} ({cnt}/{p.capacity})",
                        callback_data=f"applypoint_{task.id}_{p.id}",
                    )])
                else:
                    buttons.append([InlineKeyboardButton(
                        text=f"🔒 {p.address} ({cnt}/{p.capacity})",
                        callback_data=f"pointfull_{p.id}",
                    )])
            kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        else:
            if task.location:
                text += f"📍 {task.location}\n"
            text += f"\n💰 Ваша оплата ({rank_name(worker.rank)}): {worker_rate:.0f}₽"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Записаться", callback_data=f"apply_{task.id}")]
            ])

        try:
            await bot.send_message(worker.tg_id, text, reply_markup=kb)
            sent += 1
        except Exception as e:
            errors += 1
            logging.warning(f"Broadcast to {worker.tg_id}: {e}")

        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_DELAY)

    error_text = f" (ошибок: {errors})" if errors else ""
    await reply_func(f"Задача отправлена {sent}/{sent + errors} работникам{error_text}.")


@router.callback_query(F.data.startswith("pointfull_"))
async def cb_point_full(callback: CallbackQuery):
    await callback.answer("Эта точка уже заполнена.", show_alert=True)


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


# ──────────────── Отправка подробностей ────────────────


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
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await message.answer("Задача не найдена.")
            await state.clear()
            return

        task.details = details
        await session.commit()

        apps = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.status != ApplicationStatus.NOT_SHOWED.value)
        )).scalars().all()

        user_ids = [app.user_id for app in apps]
        if not user_ids:
            await message.answer("На эту задачу никто не записался.")
            await state.clear()
            return

        users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()

    text = f"📋 Подробности по задаче #{task_id} — {task.title}\n\n{details}"
    sent = 0
    for i, user in enumerate(users):
        try:
            await message.bot.send_message(user.tg_id, text)
            sent += 1
        except Exception:
            pass
        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(BROADCAST_DELAY)

    await state.clear()
    await message.answer(f"Подробности отправлены {sent} работникам.")


# ──────────────── Список работников (пагинация) ────────────────


@router.message(Command("workers"))
async def cmd_workers(message: Message):
    if not is_admin(message.from_user.id):
        return
    await _show_workers_page(message, 0, edit=False)


@router.callback_query(F.data.startswith("wpage_"))
async def cb_workers_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    page = int(callback.data.split("_")[1])
    await _show_workers_page(callback.message, page, edit=True)
    await callback.answer()


@router.callback_query(F.data == "back_workers")
async def cb_back_workers(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await _show_workers_page(callback.message, 0, edit=True)
    await callback.answer()


async def _show_workers_page(target, page: int, edit: bool):
    async with async_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(User).where(User.role == "worker")
        )).scalar() or 0

        workers = (await session.execute(
            select(User).where(User.role == "worker")
            .order_by(User.full_name)
            .offset(page * WORKERS_PER_PAGE)
            .limit(WORKERS_PER_PAGE)
        )).scalars().all()

    if not workers and page == 0:
        text = "Работников пока нет."
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return

    total_pages = max(1, (total + WORKERS_PER_PAGE - 1) // WORKERS_PER_PAGE)
    start = page * WORKERS_PER_PAGE + 1
    end = min((page + 1) * WORKERS_PER_PAGE, total)
    header = f"👥 Работники ({start}–{end} из {total}):"

    buttons = []
    for w in workers:
        label = f"{w.full_name} | {rank_name(w.rank)} | {w.job_role}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"worker_{w.id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"wpage_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️ Далее", callback_data=f"wpage_{page + 1}"))
    if nav:
        buttons.append(nav)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await target.edit_text(header, reply_markup=kb)
    else:
        await target.answer(header, reply_markup=kb)


# ──────────────── Карточка работника ────────────────


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


# ──────────────── Изменение ранга ────────────────


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


# ──────────────── Изменение роли ────────────────


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


# ──────────────── Удаление работника ────────────────


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


# ──────────────── Список задач ────────────────


@router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        tasks = (await session.execute(
            select(Task).order_by(Task.id.desc()).limit(20)
        )).scalars().all()

        if not tasks:
            await message.answer("Задач пока нет.")
            return

        task_ids = [t.id for t in tasks]
        counts_result = (await session.execute(
            select(TaskApplication.task_id, func.count())
            .where(TaskApplication.task_id.in_(task_ids))
            .group_by(TaskApplication.task_id)
        )).all()
        counts = {row[0]: row[1] for row in counts_result}

    lines = []
    for t in tasks:
        app_count = counts.get(t.id, 0)
        lines.append(f"#{t.id} [{t.status}] {t.title} — {t.date} | записалось: {app_count}")

    await message.answer("📋 Задачи:\n\n" + "\n".join(lines))


# ──────────────── Вспомогательные функции ────────────────


def _parse_rates(text: str) -> dict[int, float] | None:
    rank_ids = sorted(RANKS.keys())
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    if len(lines) == 1:
        try:
            return {r: float(lines[0]) for r in rank_ids}
        except ValueError:
            return None

    if len(lines) != len(rank_ids):
        return None

    try:
        values = [float(line) for line in lines]
    except ValueError:
        return None

    return dict(zip(rank_ids, values))


def _task_summary(task_id, data, rates, points_data) -> str:
    summary = f"Задача #{task_id} создана!\n\n"
    summary += f"📋 {data['title']}\n"
    if data.get("description"):
        summary += f"📝 {data['description']}\n"
    summary += f"📅 {data['date']}\n"
    if data.get("time"):
        summary += f"🕐 {data['time']}\n"

    if points_data:
        summary += "\n📍 Точки:\n"
        for p in points_data:
            summary += f"  • {p['address']} (макс: {p['capacity']} чел.)\n"
    elif data.get("location"):
        summary += f"📍 {data['location']}\n"

    summary += "\n💰 Оплата:\n"
    for rank_id in sorted(rates.keys()):
        summary += f"  {rank_name(rank_id)}: {rates[rank_id]:.0f}₽\n"

    return summary
