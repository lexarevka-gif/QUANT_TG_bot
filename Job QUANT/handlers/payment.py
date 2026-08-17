from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from database import async_session
from models import User, Task, TaskApplication, ApplicationStatus
from config import ADMIN_IDS, get_coefficient, rank_name

router = Router()


@router.message(Command("payroll"))
async def cmd_payroll(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав администратора.")
        return

    async with async_session() as session:
        workers = (await session.execute(
            select(User).where(User.role == "worker")
        )).scalars().all()

    if not workers:
        await message.answer("Работников нет.")
        return

    lines = []
    total = 0.0

    for worker in workers:
        earned, confirmed_count, penalties = await _calc_worker_pay(worker)
        coeff = get_coefficient(confirmed_count)

        lines.append(
            f"• {worker.full_name} | {rank_name(worker.rank)}\n"
            f"  Задач: {confirmed_count} | Коэфф: x{coeff}\n"
            f"  Штрафы: -{penalties:.0f}₽\n"
            f"  Итого: {earned:.0f}₽"
        )
        total += earned

    report = "💰 Расчёт оплаты:\n\n" + "\n\n".join(lines)
    report += f"\n\n{'='*30}\nОбщий итог: {total:.0f}₽"

    await message.answer(report)


@router.message(Command("my_stats"))
async def cmd_my_stats(message: Message):
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_id == message.from_user.id)
        )).scalar_one_or_none()

        if not user:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return

    earned, confirmed_count, penalties = await _calc_worker_pay(user)
    coeff = get_coefficient(confirmed_count)

    await message.answer(
        f"📊 Ваша статистика:\n\n"
        f"Ранг: {rank_name(user.rank)}\n"
        f"Подтверждённых задач: {confirmed_count}\n"
        f"Коэффициент: x{coeff}\n"
        f"Штрафы: -{penalties:.0f}₽\n"
        f"Заработано: {earned:.0f}₽"
    )


async def _calc_worker_pay(worker: User) -> tuple[float, int, float]:
    async with async_session() as session:
        confirmed_apps = (await session.execute(
            select(TaskApplication)
            .options(selectinload(TaskApplication.task))
            .where(TaskApplication.user_id == worker.id)
            .where(TaskApplication.status == ApplicationStatus.CONFIRMED.value)
        )).scalars().all()

        penalties = (await session.execute(
            select(func.coalesce(func.sum(TaskApplication.penalty_amount), 0))
            .where(TaskApplication.user_id == worker.id)
            .where(TaskApplication.status == ApplicationStatus.PENALTY.value)
        )).scalar() or 0

    confirmed_count = len(confirmed_apps)
    coeff = get_coefficient(confirmed_count)

    base_sum = sum(app.task.rate_for_rank(worker.rank) for app in confirmed_apps)
    earned = base_sum * coeff - penalties

    return earned, confirmed_count, penalties
