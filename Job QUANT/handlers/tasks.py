from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from database import async_session
from models import User, Task, TaskApplication, TaskPoint, ApplicationStatus

router = Router()


@router.callback_query(F.data.startswith("apply_"))
async def apply_for_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == callback.from_user.id)
        )).scalar_one_or_none()

        if not user:
            await callback.answer("Сначала зарегистрируйтесь: /start", show_alert=True)
            return

        existing = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if existing:
            await callback.answer("Вы уже записаны на эту задачу.", show_alert=True)
            return

        task = (await session.execute(
            select(Task).where(Task.id == task_id)
        )).scalar_one_or_none()

        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return

        app = TaskApplication(
            task_id=task_id,
            user_id=user.id,
            status=ApplicationStatus.APPLIED.value,
        )
        session.add(app)
        await session.commit()

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отписаться от задачи", callback_data=f"cancel_{task_id}")]
    ])
    await callback.answer("Вы записались на задачу!", show_alert=True)
    await callback.message.answer(
        f"✅ Вы записались на задачу #{task_id}.\n"
        f"Когда прибудете — отправьте фото начала: /photo_start {task_id}",
        reply_markup=cancel_kb,
    )


@router.callback_query(F.data.startswith("applypoint_"))
async def apply_for_point(callback: CallbackQuery):
    parts = callback.data.split("_")
    task_id = int(parts[1])
    point_id = int(parts[2])

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == callback.from_user.id)
        )).scalar_one_or_none()

        if not user:
            await callback.answer("Сначала зарегистрируйтесь: /start", show_alert=True)
            return

        existing = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if existing:
            await callback.answer("Вы уже записаны на эту задачу.", show_alert=True)
            return

        point = (await session.execute(
            select(TaskPoint).where(TaskPoint.id == point_id)
        )).scalar_one_or_none()

        if not point:
            await callback.answer("Точка не найдена.", show_alert=True)
            return

        current_count = (await session.execute(
            select(func.count()).where(TaskApplication.point_id == point_id)
        )).scalar() or 0

        if current_count >= point.capacity:
            await callback.answer("Эта точка уже заполнена.", show_alert=True)
            return

        app = TaskApplication(
            task_id=task_id,
            user_id=user.id,
            point_id=point_id,
            status=ApplicationStatus.APPLIED.value,
        )
        session.add(app)
        await session.commit()

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отписаться от задачи", callback_data=f"cancel_{task_id}")]
    ])
    await callback.answer("Вы записались на точку!", show_alert=True)
    await callback.message.answer(
        f"✅ Вы записались на задачу #{task_id}, точка: {point.address}.\n"
        f"Когда прибудете — отправьте фото начала: /photo_start {task_id}",
        reply_markup=cancel_kb,
    )


@router.callback_query(F.data.startswith("cancel_"))
async def cancel_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == callback.from_user.id)
        )).scalar_one_or_none()

        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

        app = (await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if not app:
            await callback.answer("Вы не записаны на эту задачу.", show_alert=True)
            return

        if app.status not in (ApplicationStatus.APPLIED.value,):
            await callback.answer(
                "Отписка невозможна — вы уже начали выполнение задачи.",
                show_alert=True,
            )
            return

        await session.delete(app)
        await session.commit()

    await callback.answer("Вы отписались от задачи.", show_alert=True)
    await callback.message.edit_text(f"❌ Вы отписались от задачи #{task_id}.")
