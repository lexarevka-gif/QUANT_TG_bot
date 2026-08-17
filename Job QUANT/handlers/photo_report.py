from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from database import async_session
from models import User, Task, TaskApplication, ApplicationStatus
from config import ADMIN_IDS
from datetime import datetime

router = Router()


class PhotoStart(StatesGroup):
    waiting_photo = State()


class PhotoEnd(StatesGroup):
    waiting_photo = State()


# --- Фото начала ---

@router.message(Command("photo_start"))
async def cmd_photo_start(message: Message, state: FSMContext):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /photo_start <ID задачи>")
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
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if not app:
            await message.answer("Вы не записаны на эту задачу.")
            return

        if app.photo_start_file_id:
            await message.answer("Вы уже отправили фото начала.")
            return

    await state.update_data(task_id=task_id)
    await state.set_state(PhotoStart.waiting_photo)
    await message.answer("📸 Отправьте фото начала работы:")


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
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        app.photo_start_file_id = photo_file_id
        app.photo_start_time = datetime.utcnow()
        app.status = ApplicationStatus.PHOTO_START.value
        await session.commit()

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

    await state.clear()
    await message.answer(
        f"✅ Фото начала принято для задачи #{task_id}.\n"
        f"По завершении отправьте фото конца: /photo_end {task_id}"
    )

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id,
                photo_file_id,
                caption=f"📸 Фото НАЧАЛА\n"
                        f"Задача: #{task_id} — {task.title}\n"
                        f"Работник: {user.full_name}\n"
                        f"Время: {datetime.utcnow().strftime('%H:%M %d.%m.%Y')}",
            )
        except Exception:
            pass


# --- Фото конца ---

@router.message(Command("photo_end"))
async def cmd_photo_end(message: Message, state: FSMContext):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /photo_end <ID задачи>")
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
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if not app:
            await message.answer("Вы не записаны на эту задачу.")
            return

        if not app.photo_start_file_id:
            await message.answer("Сначала отправьте фото начала: /photo_start " + str(task_id))
            return

        if app.photo_end_file_id:
            await message.answer("Вы уже отправили фото конца.")
            return

    await state.update_data(task_id=task_id)
    await state.set_state(PhotoEnd.waiting_photo)
    await message.answer("📸 Отправьте фото конца работы:")


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
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        app.photo_end_file_id = photo_file_id
        app.photo_end_time = datetime.utcnow()
        app.status = ApplicationStatus.PHOTO_END.value
        await session.commit()

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

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

    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_photo(
                admin_id,
                photo_file_id,
                caption=f"📸 Фото КОНЦА\n"
                        f"Задача: #{task_id} — {task.title}\n"
                        f"Работник: {user.full_name}\n"
                        f"Время: {datetime.utcnow().strftime('%H:%M %d.%m.%Y')}",
                reply_markup=kb,
            )
        except Exception:
            pass


# --- Подтверждение/отклонение админом ---

@router.callback_query(F.data.startswith("confirm_"))
async def confirm_report(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
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
        app.confirmed_at = datetime.utcnow()
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
    if callback.from_user.id not in ADMIN_IDS:
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
async def penalize_worker(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
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

        app.status = ApplicationStatus.PENALTY.value
        app.penalty_amount = 500.0
        await session.commit()

        user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()

    await callback.answer("Штраф назначен.", show_alert=True)
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n⚠️ ШТРАФ 500₽"
    )

    try:
        await callback.bot.send_message(
            user.tg_id,
            f"⚠️ По задаче #{app.task_id} вам назначен штраф 500₽."
        )
    except Exception:
        pass
