from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from database import async_session
from models import User, Task, TaskApplication, ApplicationStatus
from config import ADMIN_IDS, get_coefficient, rank_name

router = Router()

MAX_MESSAGE_LEN = 4000


@router.message(Command("payroll"))
async def cmd_payroll(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав администратора.")
        return

    async with async_session() as session:
        workers = (await session.execute(
            select(User)
            .where(User.role == "worker")
            .options(selectinload(User.applications).selectinload(TaskApplication.task))
            .order_by(User.full_name)
        )).scalars().all()

    if not workers:
        await message.answer("Работников нет.")
        return

    lines = []
    total = 0.0

    for worker in workers:
        confirmed_apps = [
            a for a in worker.applications
            if a.status == ApplicationStatus.CONFIRMED.value
        ]
        penalties = sum(
            a.penalty_amount for a in worker.applications
            if a.status == ApplicationStatus.PENALTY.value
        )

        confirmed_count = len(confirmed_apps)
        coeff = get_coefficient(confirmed_count)
        base_sum = sum(a.task.rate_for_rank(worker.rank) for a in confirmed_apps)
        earned = base_sum * coeff - penalties

        lines.append(
            f"• {worker.full_name} | {rank_name(worker.rank)}\n"
            f"  Задач: {confirmed_count} | Коэфф: x{coeff}\n"
            f"  Штрафы: -{penalties:.0f}₽\n"
            f"  Итого: {earned:.0f}₽"
        )
        total += earned

    footer = f"\n{'=' * 30}\nОбщий итог: {total:.0f}₽"
    header = "💰 Расчёт оплаты:\n\n"

    chunks = _split_report(header, lines, footer)
    for chunk in chunks:
        await message.answer(chunk)


@router.message(Command("my_stats"))
async def cmd_my_stats(message: Message):
    async with async_session() as session:
        user = (await session.execute(
            select(User)
            .where(User.tg_id == message.from_user.id)
            .options(selectinload(User.applications).selectinload(TaskApplication.task))
        )).scalar_one_or_none()

        if not user:
            await message.answer("Сначала зарегистрируйтесь: /start")
            return

    confirmed_apps = [
        a for a in user.applications
        if a.status == ApplicationStatus.CONFIRMED.value
    ]
    penalties = sum(
        a.penalty_amount for a in user.applications
        if a.status == ApplicationStatus.PENALTY.value
    )
    confirmed_count = len(confirmed_apps)
    coeff = get_coefficient(confirmed_count)
    base_sum = sum(a.task.rate_for_rank(user.rank) for a in confirmed_apps)
    earned = base_sum * coeff - penalties

    await message.answer(
        f"📊 Ваша статистика:\n\n"
        f"Ранг: {rank_name(user.rank)}\n"
        f"Подтверждённых задач: {confirmed_count}\n"
        f"Коэффициент: x{coeff}\n"
        f"Штрафы: -{penalties:.0f}₽\n"
        f"Заработано: {earned:.0f}₽"
    )


def _split_report(header: str, lines: list[str], footer: str) -> list[str]:
    chunks = []
    current = header

    for line in lines:
        entry = line + "\n\n"
        if len(current) + len(entry) + len(footer) > MAX_MESSAGE_LEN:
            chunks.append(current)
            current = ""
        current += entry

    current += footer
    chunks.append(current)
    return chunks
