from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from sqlalchemy import select, func
from database import async_session
from models import User, Task, TaskApplication, TaskPoint, ApplicationStatus, TaskStatus, Division
from config import rank_name, ADMIN_IDS, ADMIN_MIN_RANK

router = Router()


async def _get_admins():
    async with async_session() as session:
        by_rank = (await session.execute(
            select(User).where(User.rank >= ADMIN_MIN_RANK)
        )).scalars().all()
        by_id = (await session.execute(
            select(User).where(User.tg_id.in_(ADMIN_IDS))
        )).scalars().all()
    seen = set()
    admins = []
    for u in list(by_rank) + list(by_id):
        if u.tg_id not in seen:
            seen.add(u.tg_id)
            admins.append(u)
    return admins


@router.message(Command("available_tasks"))
async def cmd_available_tasks(message: Message):
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()

        if not user:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return

        tasks = (await session.execute(
            select(Task).where(
                Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
                Task.is_template == False,
            ).order_by(Task.id.desc())
        )).scalars().all()

        role_filtered = []
        for t in tasks:
            if t.job_role_filter and t.job_role_filter not in user.job_roles:
                continue
            role_filtered.append(t)

        if not role_filtered:
            await message.answer("Сейчас нет доступных задач.")
            return

        apps = (await session.execute(
            select(TaskApplication.task_id).where(TaskApplication.user_id == user.id)
        )).scalars().all()
        already_applied = set(apps)

        divs_map = {}
        div_ids = {t.division_id for t in role_filtered if t.division_id}
        if div_ids:
            divs = (await session.execute(
                select(Division).where(Division.id.in_(div_ids))
            )).scalars().all()
            divs_map = {d.id: d.name for d in divs}

        app_counts = {}
        task_ids = [t.id for t in role_filtered]
        if task_ids:
            rows = (await session.execute(
                select(TaskApplication.task_id, func.count())
                .where(TaskApplication.task_id.in_(task_ids))
                .group_by(TaskApplication.task_id)
            )).all()
            app_counts = dict(rows)

    for task in role_filtered:
        rate = task.rate_for_rank(user.rank)
        text = f"📋 Задача #{task.id}\n\n"
        text += f"{task.title}\n"
        if task.description:
            text += f"📝 {task.description}\n"
        text += f"📅 {task.date}\n"
        if task.time:
            text += f"🕐 {task.time}\n"
        if task.location:
            text += f"📍 {task.location}\n"
        if task.division_id and task.division_id in divs_map:
            text += f"🏢 {divs_map[task.division_id]}\n"
        text += f"\n💰 Ваша оплата ({rank_name(user.rank)}): {rate:.0f}₽"

        current_count = app_counts.get(task.id, 0)
        if task.max_workers:
            text += f"\n👥 Мест: {current_count}/{task.max_workers}"

        if task.id in already_applied:
            text += "\n\n✅ Вы уже записаны"
            await message.answer(text)
        else:
            if task.max_workers and current_count >= task.max_workers:
                text += "\n\n🚫 Все места заняты"
                await message.answer(text)
            else:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Записаться", callback_data=f"apply_{task.id}")]
                ])
                await message.answer(text, reply_markup=kb)


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
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if existing:
            await callback.answer("Вы уже записаны на эту задачу.", show_alert=True)
            return

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()

        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return

        if task.status not in (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value):
            await callback.answer("Задача уже закрыта.", show_alert=True)
            return

        if task.job_role_filter and task.job_role_filter not in user.job_roles:
            await callback.answer("Эта задача не для вашей роли.", show_alert=True)
            return

        if task.max_workers:
            count = (await session.execute(
                select(func.count()).where(TaskApplication.task_id == task_id)
            )).scalar()
            if count >= task.max_workers:
                await callback.answer("Все места заняты.", show_alert=True)
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

    admins = await _get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(
                admin.tg_id,
                f"📢 {user.full_name} ({rank_name(user.rank)}) записался на задачу #{task_id} — {task.title}"
            )
        except Exception:
            pass


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
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
        )).scalar_one_or_none()

        if existing:
            await callback.answer("Вы уже записаны на эту задачу.", show_alert=True)
            return

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        if not task:
            await callback.answer("Задача не найдена.", show_alert=True)
            return

        if task.status not in (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value):
            await callback.answer("Задача уже закрыта.", show_alert=True)
            return

        if task.job_role_filter and task.job_role_filter not in user.job_roles:
            await callback.answer("Эта задача не для вашей роли.", show_alert=True)
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

    admins = await _get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(
                admin.tg_id,
                f"📢 {user.full_name} ({rank_name(user.rank)}) записался на задачу #{task_id} — {task.title}, точка: {point.address}"
            )
        except Exception:
            pass


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
            .where(TaskApplication.task_id == task_id, TaskApplication.user_id == user.id)
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

        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
        await session.delete(app)
        await session.commit()

    await callback.answer("Вы отписались от задачи.", show_alert=True)
    await callback.message.edit_text(f"❌ Вы отписались от задачи #{task_id}.")

    admins = await _get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(
                admin.tg_id,
                f"📢 {user.full_name} отписался от задачи #{task_id}" + (f" — {task.title}" if task else "")
            )
        except Exception:
            pass


# --- Подтверждение выхода (attendance) ---

@router.callback_query(F.data.startswith("attend_yes_"))
async def cb_attend_yes(callback: CallbackQuery):
    app_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        app = (await session.execute(
            select(TaskApplication).where(TaskApplication.id == app_id)
        )).scalar_one_or_none()
        if not app:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return
        app.attendance_confirmed = True
        await session.commit()
    await callback.answer("Вы подтвердили выход!", show_alert=True)
    await callback.message.edit_text("✅ Вы подтвердили выход на задачу. Спасибо!")


@router.callback_query(F.data.startswith("attend_no_"))
async def cb_attend_no(callback: CallbackQuery):
    app_id = int(callback.data.split("_")[2])
    async with async_session() as session:
        app = (await session.execute(
            select(TaskApplication).where(TaskApplication.id == app_id)
        )).scalar_one_or_none()
        if not app:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        task = (await session.execute(select(Task).where(Task.id == app.task_id))).scalar_one_or_none()
        user = (await session.execute(select(User).where(User.id == app.user_id))).scalar_one_or_none()

        app.attendance_confirmed = False
        await session.delete(app)
        await session.commit()

    await callback.answer("Вы отказались от задачи.", show_alert=True)
    await callback.message.edit_text("❌ Вы отказались от выхода на задачу.")

    admins = await _get_admins()
    for admin in admins:
        try:
            await callback.bot.send_message(
                admin.tg_id,
                f"⚠️ {user.full_name} отказался от выхода на задачу #{app.task_id}" + (f" — {task.title}" if task else "")
            )
        except Exception:
            pass
