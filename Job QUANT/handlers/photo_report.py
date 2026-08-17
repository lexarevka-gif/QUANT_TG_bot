from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database import async_session
from models import User, Task, TaskApplication, ApplicationStatus, TaskStatus
from handlers.admin import is_admin
from handlers.tasks import _get_admins
from datetime import datetime, timedelta
from config import TZ_MOSCOW

router = Router()


class PhotoStart(StatesGroup):
    waiting_photo = State()


class PhotoEnd(StatesGroup):
    waiting_photo = State()


class PenaltyAmount(StatesGroup):
    waiting_amount = State()


def _parse_task_datetime(task: Task) -> datetime | None:
    date_str = task.date.strip()
    time_str = task.time.strip() if task.time else "00:00"
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    return None


def _now_msk():
    return datetime.now(TZ_MOSCOW).replace(tzinfo=None)


def _can_submit_photo(task: Task) -> tuple[bool, str]:
    if task.status in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value):
        return False, "Задача уже завершена или отменена."
    task_dt = _parse_task_datetime(task)
    if not task_dt:
        return True, ""
    now = _now_msk()
    earliest = task_dt - timedelta(hours=1)
    if now < earliest:
        return False, f"Фотоотчёт можно отправить не ранее {earliest.strftime('%H:%M %d.%m.%Y')}"
    return True, ""


# ====================================================================
# Кнопка «📸 Фотоотчёт» — выбор задачи
# ====================================================================

@router.message(Command("photo_report"))
async def cmd_photo_report(message: Message):
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()
        if not user:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return

        apps = (await session.execute(
            select(TaskApplication)
            .options(selectinload(TaskApplication.task))
            .where(
                TaskApplication.user_id == user.id,
                TaskApplication.status.in_([
                    ApplicationStatus.APPLIED.value,
                    ApplicationStatus.PHOTO_START.value,
                    ApplicationStatus.PHOTO_END.value,
                ]),
            )
        )).scalars().all()

    active_apps = [
        a for a in apps
        if a.task.status not in (TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value)
    ]

    if not active_apps:
        await message.answer("У вас нет активных задач для фотоотчёта.")
        return

    buttons = []
    for a in active_apps:
        if not a.photo_start_file_id:
            label = f"📸 Начало — #{a.task.id} {a.task.title}"
            cb = f"photopick_start_{a.task_id}"
        elif not a.photo_end_file_id:
            label = f"📸 Конец — #{a.task.id} {a.task.title}"
            cb = f"photopick_end_{a.task_id}"
        else:
            label = f"✅ Отправлено — #{a.task.id} {a.task.title}"
            cb = f"photopick_done_{a.task_id}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📸 Выберите задачу для фотоотчёта:", reply_markup=kb)


@router.callback_query(F.data.startswith("photopick_done_"))
async def cb_photo_done(callback: CallbackQuery):
    await callback.answer("Фотоотчёт уже отправлен. Ожидайте подтверждения.", show_alert=True)


@router.callback_query(F.data.startswith("photopick_start_"))
async def cb_photo_pick_start(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return

    can, reason = _can_submit_photo(task)
    if not can:
        await callback.answer(reason, show_alert=True)
        return

    await state.update_data(task_id=task_id)
    await state.set_state(PhotoStart.waiting_photo)
    await callback.message.answer(f"📸 Отправьте фото начала работы для задачи #{task_id}:")
    await callback.answer()


@router.callback_query(F.data.startswith("photopick_end_"))
async def cb_photo_pick_end(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[2])

    async with async_session() as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return

    can, reason = _can_submit_photo(task)
    if not can:
        await callback.answer(reason, show_alert=True)
        return

    await state.update_data(task_id=task_id)
    await state.set_state(PhotoEnd.waiting_photo)
    await callback.message.answer(f"📸 Отправьте фото конца работы для задачи #{task_id}:")
    await callback.answer()


# ====================================================================
# Команды /photo_start и /photo_end (оставлены для совместимости)
# ====================================================================

@router.message(Command("photo_start"))
async def cmd_photo_start(message: Message, state: FSMContext):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте кнопку «📸 Фотоотчёт» или: /photo_start <ID задачи>")
        return

    task_id = int(args[1])

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()
        if not user:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return

        app = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
        )).scalar_one_or_none()
        if not app:
            await message.answer("Вы не записаны на эту задачу.")
            return
        if app.photo_start_file_id:
            await message.answer("Вы уже отправили фото начала.")
            return

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

    can, reason = _can_submit_photo(task)
    if not can:
        await message.answer(reason)
        return

    await state.update_data(task_id=task_id)
    await state.set_state(PhotoStart.waiting_photo)
    await message.answer("📸 Отправьте фото начала работы:")


@router.message(Command("photo_end"))
async def cmd_photo_end(message: Message, state: FSMContext):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте кнопку «📸 Фотоотчёт» или: /photo_end <ID задачи>")
        return

    task_id = int(args[1])

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()
        if not user:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return

        app = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
        )).scalar_one_or_none()
        if not app:
            await message.answer("Вы не записаны на эту задачу.")
            return
        if not app.photo_start_file_id:
            await message.answer("Сначала отправьте фото начала.")
            return
        if app.photo_end_file_id:
            await message.answer("Вы уже отправили фото конца.")
            return

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

    can, reason = _can_submit_photo(task)
    if not can:
        await message.answer(reason)
        return

    await state.update_data(task_id=task_id)
    await state.set_state(PhotoEnd.waiting_photo)
    await message.answer("📸 Отправьте фото конца работы:")


# ====================================================================
# Приём фото
# ====================================================================

@router.message(PhotoStart.waiting_photo, F.photo)
async def receive_photo_start(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    photo_file_id = message.photo[-1].file_id

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()

        app = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

        can, reason = _can_submit_photo(task)
        if not can:
            await state.clear()
            await message.answer(reason)
            return

        app.photo_start_file_id = photo_file_id
        app.photo_start_time = _now_msk()
        app.status = ApplicationStatus.PHOTO_START.value
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Фото начала принято для задачи #{task_id}.\n"
        f"По завершении нажмите «📸 Фотоотчёт» чтобы отправить фото конца."
    )

    admins = await _get_admins()
    for admin in admins:
        try:
            await message.bot.send_photo(
                admin.tg_id,
                photo_file_id,
                caption=f"📸 Фото НАЧАЛА\n"
                        f"Задача: #{task_id} — {task.title}\n"
                        f"Работник: {user.full_name}\n"
                        f"Время: {_now_msk().strftime('%H:%M %d.%m.%Y')}",
            )
        except Exception:
            pass


@router.message(PhotoEnd.waiting_photo, F.photo)
async def receive_photo_end(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    photo_file_id = message.photo[-1].file_id

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()

        app = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

        can, reason = _can_submit_photo(task)
        if not can:
            await state.clear()
            await message.answer(reason)
            return

        app.photo_end_file_id = photo_file_id
        app.photo_end_time = _now_msk()
        app.status = ApplicationStatus.PHOTO_END.value
        await session.commit()

    await state.clear()
    await message.answer(f"✅ Фото конца принято для задачи #{task_id}. Ожидайте подтверждения от админа.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{app.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{app.id}"),
        ],
        [
            InlineKeyboardButton(text="⚠️ Штраф", callback_data=f"penalize_{app.id}"),
        ],
    ])

    admins = await _get_admins()
    for admin in admins:
        try:
            await message.bot.send_photo(
                admin.tg_id,
                photo_file_id,
                caption=f"📸 Фото КОНЦА\n"
                        f"Задача: #{task_id} — {task.title}\n"
                        f"Работник: {user.full_name}\n"
                        f"Время: {_now_msk().strftime('%H:%M %d.%m.%Y')}",
                reply_markup=kb,
            )
        except Exception:
            pass


# ====================================================================
# Подтверждение / отклонение / штраф (админ)
# ====================================================================

@router.callback_query(F.data.startswith("confirm_"))
async def confirm_report(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        app = (await session.execute(
            select(TaskApplication).where(TaskApplication.id == app_id)
        )).scalar_one_or_none()
        if not app:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        app.status = ApplicationStatus.CONFIRMED.value
        app.confirmed_by = callback.from_user.id
        app.confirmed_at = _now_msk()
        await session.commit()

        user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()

    await callback.answer("Отчёт подтверждён!", show_alert=True)
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n✅ ПОДТВЕРЖДЕНО"
    )

    try:
        await callback.bot.send_message(
            user.tg_id,
            f"✅ Ваш отчёт по задаче #{app.task_id} подтверждён администратором!"
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("reject_"))
async def reject_report(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        app = (await session.execute(
            select(TaskApplication).where(TaskApplication.id == app_id)
        )).scalar_one_or_none()
        if not app:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        app.status = ApplicationStatus.NOT_SHOWED.value
        await session.commit()

        user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()

    await callback.answer("Отчёт отклонён.", show_alert=True)
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n❌ ОТКЛОНЕНО"
    )

    try:
        await callback.bot.send_message(
            user.tg_id,
            f"❌ Ваш отчёт по задаче #{app.task_id} отклонён администратором."
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("penalize_"))
async def penalize_worker(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    app_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        app = (await session.execute(
            select(TaskApplication).where(TaskApplication.id == app_id)
        )).scalar_one_or_none()
        if not app:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

    await state.update_data(penalty_app_id=app_id)
    await state.set_state(PenaltyAmount.waiting_amount)
    await callback.answer()
    await callback.message.answer(f"Введите сумму штрафа для заявки #{app_id} (в рублях):")


@router.message(PenaltyAmount.waiting_amount)
async def process_penalty_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("Введите число. Попробуйте ещё раз:")
        return

    if amount <= 0:
        await message.answer("Сумма штрафа должна быть больше 0. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    app_id = data["penalty_app_id"]

    async with async_session() as session:
        app = (await session.execute(
            select(TaskApplication).where(TaskApplication.id == app_id)
        )).scalar_one_or_none()
        if not app:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return

        app.status = ApplicationStatus.PENALTY.value
        app.penalty_amount = amount
        await session.commit()

        user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()

    await state.clear()
    await message.answer(f"⚠️ Штраф {amount:.0f}₽ назначен.")

    try:
        await message.bot.send_message(
            user.tg_id,
            f"⚠️ По задаче #{app.task_id} вам назначен штраф {amount:.0f}₽."
        )
    except Exception:
        pass
