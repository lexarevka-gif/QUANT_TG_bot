import os
from datetime import timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost/quant_bot")

WORKERS_PER_PAGE = 50
BROADCAST_BATCH_SIZE = 25
BROADCAST_DELAY = 1.0

ADMIN_MIN_RANK = 7
TZ_MOSCOW = timezone(timedelta(hours=3))

RANKS = {
    0: "Без ранга",
    1: "Новичок",
    2: "Стажер",
    3: "Активист",
    4: "Помощник Специалиста",
    5: "Специалист",
    6: "Ведущий специалист",
    7: "Менеджер",
    8: "Заместитель Главы",
    9: "Глава",
}

RANK_NAMES_TO_ID = {v: k for k, v in RANKS.items()}

PAYMENT_TIERS = {
    0: "Без ранга",
    1: "Новичок",
    2: "Стажер",
    3: "Активист",
    4: "Помощник Специалиста и выше",
}


def payment_tier_for_rank(rank: int) -> int:
    return min(rank, 4)


JOB_ROLES = [
    "Без роли",
    "Диджей",
    "СММ",
    "Общие",
]

DEFAULT_DIVISIONS = [
    ("Мгер", True),
    ("Вол Рота", True),
    ("Пикеты", False),
    ("Спринт", True),
    ("Коммерция", False),
    ("МосЭкоПатруль", True),
]

MONTHLY_COEFFICIENTS = [
    (3, 1.0),
    (6, 1.2),
    (15, 1.4),
    (999999, 2.0),
]


def get_monthly_coefficient(task_count: int) -> float:
    for threshold, coeff in MONTHLY_COEFFICIENTS:
        if task_count <= threshold:
            return coeff
    return 1.0


def rank_name(rank_id: int) -> str:
    return RANKS.get(rank_id, f"Ранг {rank_id}")
