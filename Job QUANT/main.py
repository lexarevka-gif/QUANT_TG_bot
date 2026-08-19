import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from config import BOT_TOKEN, TZ_MOSCOW
from database import init_db, async_session
from models import Task, TaskApplication, User, TaskStatus, ApplicationStatus
from handlers import routers
from handlers.common import router as common_router

REMINDER_HOURS = 2
REMINDER_CHECK_INTERVAL = 300
ATTENDANCE_CHECK_INTERVAL = 300
ATTENDANCE_TIMEOUT_HOURS = 1


async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Регистрация"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="profile", description="Мой профиль"),
        BotCommand(command="my_tasks", description="Мои задачи"),
        BotCommand(command="my_stats", description="Моя статистика"),
        BotCommand(command="available_tasks", description="Доступные задачи"),
        BotCommand(command="new_task", description="Создать задачу (админ)"),
        BotCommand(command="templates", description="Шаблоны задач (админ)"),
        BotCommand(command="close_task", description="Закрыть задачу (админ)"),
        BotCommand(command="cancel_task", description="Отменить задачу (админ)"),
        BotCommand(command="tasks", description="Список задач (админ)"),
        BotCommand(command="workers", description="Работники (админ)"),
        BotCommand(command="divisions", description="Подразделения (админ)"),
        BotCommand(command="mass_send", description="Рассылка (админ)"),
        BotCommand(command="payroll", description="Расчёт оплаты (админ)"),
        BotCommand(command="payroll_xlsx", description="Экспорт в Excel (админ)"),
    ]
    await bot.set_my_commands(commands)


async def reminder_loop(bot: Bot):
    notified = set()
    while True:
        try:
            now = datetime.now(TZ_MOSCOW).replace(tzinfo=None)
            async with async_session() as session:
                tasks = (await session.execute(
                    select(Task).where(Task.status.in_([
                        TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value
                    ]))
                )).scalars().all()

                for task in tasks:
                    task_dt = _parse_task_datetime(task)
                    if not task_dt:
                        continue

                    delta = task_dt - now
                    if timedelta(0) < delta <= timedelta(hours=REMINDER_HOURS):
                        apps = (await session.execute(
                            select(TaskApplication).where(
                                TaskApplication.task_id == task.id,
                                TaskApplication.status == ApplicationStatus.APPLIED.value,
                            )
                        )).scalars().all()

                        for app in apps:
                            key = (task.id, app.user_id)
                            if key in notified:
                                continue

                            user = (await session.execute(
                                select(User).where(User.id == app.user_id)
                            )).scalar_one_or_none()
                            if not user:
                                continue

                            hours_left = delta.total_seconds() / 3600
                            time_text = f"{hours_left:.0f} ч" if hours_left >= 1 else f"{delta.total_seconds() / 60:.0f} мин"

                            try:
                                await bot.send_message(
                                    user.tg_id,
                                    f"⏰ Напоминание!\n\n"
                                    f"Задача #{task.id} — {task.title}\n"
                                    f"📅 {task.date}"
                                    + (f" 🕐 {task.time}" if task.time else "")
                                    + (f"\n📍 {task.location}" if task.location else "")
                                    + f"\n\nДо начала: ~{time_text}"
                                )
                                notified.add(key)
                            except Exception:
                                pass
        except Exception as e:
            logging.error(f"Reminder error: {e}")

        await asyncio.sleep(REMINDER_CHECK_INTERVAL)


async def attendance_confirmation_loop(bot: Bot):
    sent_evening = set()
    sent_morning = set()
    notified_timeout = set()

    while True:
        try:
            now = datetime.now(TZ_MOSCOW).replace(tzinfo=None)
            async with async_session() as session:
                tasks = (await session.execute(
                    select(Task).where(Task.status.in_([
                        TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value
                    ]))
                )).scalars().all()

                for task in tasks:
                    task_dt = _parse_task_datetime(task)
                    if not task_dt:
                        continue

                    apps = (await session.execute(
                        select(TaskApplication).where(
                            TaskApplication.task_id == task.id,
                            TaskApplication.status == ApplicationStatus.APPLIED.value,
                        )
                    )).scalars().all()

                    for app in apps:
                        user = (await session.execute(
                            select(User).where(User.id == app.user_id)
                        )).scalar_one_or_none()
                        if not user:
                            continue

                        evening_key = f"eve_{task.id}_{app.user_id}"
                        morning_key = f"morn_{task.id}_{app.user_id}"
                        timeout_key = f"timeout_{task.id}_{app.user_id}"

                        evening_time = task_dt - timedelta(days=1)
                        evening_time = evening_time.replace(hour=20, minute=0, second=0)
                        if now >= evening_time and evening_key not in sent_evening:
                            if task_dt - now > timedelta(hours=10):
                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [
                                        InlineKeyboardButton(text="✅ Подтверждаю", callback_data=f"attend_yes_{app.id}"),
                                        InlineKeyboardButton(text="❌ Не смогу", callback_data=f"attend_no_{app.id}"),
                                    ]
                                ])
                                try:
                                    await bot.send_message(
                                        user.tg_id,
                                        f"🔔 Подтверждение выхода\n\n"
                                        f"Завтра задача #{task.id} — {task.title}\n"
                                        f"📅 {task.date}"
                                        + (f" 🕐 {task.time}" if task.time else "")
                                        + f"\n\nПодтвердите выход:",
                                        reply_markup=kb,
                                    )
                                    app.confirm_sent_at = now
                                    await session.commit()
                                except Exception:
                                    pass
                                sent_evening.add(evening_key)

                        morning_time = task_dt.replace(hour=8, minute=0, second=0)
                        if now >= morning_time and morning_key not in sent_morning:
                            if task_dt - now > timedelta(hours=0) and app.attendance_confirmed is None:
                                kb = InlineKeyboardMarkup(inline_keyboard=[
                                    [
                                        InlineKeyboardButton(text="✅ Подтверждаю", callback_data=f"attend_yes_{app.id}"),
                                        InlineKeyboardButton(text="❌ Не смогу", callback_data=f"attend_no_{app.id}"),
                                    ]
                                ])
                                try:
                                    await bot.send_message(
                                        user.tg_id,
                                        f"🔔 Утреннее подтверждение\n\n"
                                        f"Сегодня задача #{task.id} — {task.title}\n"
                                        f"📅 {task.date}"
                                        + (f" 🕐 {task.time}" if task.time else "")
                                        + f"\n\nПодтвердите выход:",
                                        reply_markup=kb,
                                    )
                                    app.confirm_sent_at = now
                                    await session.commit()
                                except Exception:
                                    pass
                                sent_morning.add(morning_key)

                        if app.confirm_sent_at and app.attendance_confirmed is None and timeout_key not in notified_timeout:
                            elapsed = now - app.confirm_sent_at
                            if elapsed >= timedelta(hours=ATTENDANCE_TIMEOUT_HOURS):
                                from handlers.tasks import _get_admins
                                admins = await _get_admins()
                                for admin in admins:
                                    try:
                                        await bot.send_message(
                                            admin.tg_id,
                                            f"⚠️ {user.full_name} не подтвердил выход на задачу "
                                            f"#{task.id} — {task.title} в течение {ATTENDANCE_TIMEOUT_HOURS} ч."
                                        )
                                    except Exception:
                                        pass
                                notified_timeout.add(timeout_key)

        except Exception as e:
            logging.error(f"Attendance check error: {e}")

        await asyncio.sleep(ATTENDANCE_CHECK_INTERVAL)


def _parse_task_datetime(task: Task) -> datetime | None:
    date_str = task.date.strip()
    time_str = task.time.strip() if task.time else "00:00"
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    return None


async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(common_router)
    for r in routers:
        dp.include_router(r)

    await init_db()
    await set_commands(bot)
    logging.info("Бот запущен")

    asyncio.create_task(reminder_loop(bot))
    asyncio.create_task(attendance_confirmation_loop(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
