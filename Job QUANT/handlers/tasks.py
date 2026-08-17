from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from database import async_session
from models import User, Task, TaskApplication, ApplicationStatus

router = Router()


@router.callback_query(F.data.startswith("apply_"))
async def apply_for_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.tg_id == callback.from_user.id)
        )
        user = user_result.scalar_one_or_none()

        if not user:
            await callback.answer("Сначала зарегистрируйтесь: /start", show_alert=True)
            return

        existing = await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )
        if existing.scalar_one_or_none():
            await callback.answer("Вы уже записаны на эту задачу.", show_alert=True)
            return

        task_result = await session.execute(select(Task).where(Task.id == task_id))
        task = task_result.scalar_one_or_none()
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


@router.callback_query(F.data.startswith("cancel_"))
async def cancel_task(callback: CallbackQuery):
    task_id = int(callback.data.split("_")[1])

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.tg_id == callback.from_user.id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            await callback.answer("Вы не зарегистрированы.", show_alert=True)
            return

        existing = await session.execute(
            select(TaskApplication)
            .where(TaskApplication.task_id == task_id)
            .where(TaskApplication.user_id == user.id)
        )
        app = existing.scalar_one_or_none()

        if not app:
            await callback.answer("Вы не записаны на эту задачу.", show_alert=True)
            return

        # Нельзя отписаться, если уже отправил фото или подтверждён
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
