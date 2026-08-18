import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost/quant_bot")

WORKERS_PER_PAGE = 50
BROADCAST_BATCH_SIZE = 25
BROADCAST_DELAY = 1.0

RANKS = {
    0: "Без ранга",
    1: "Новичок",
    2: "Стажёр",
    3: "Активист",
    4: "Помощник бригадира и выше",
}

RANK_NAMES_TO_ID = {v: k for k, v in RANKS.items()}

JOB_ROLES = [
    "Без роли",
    "Диджей",
    "СММ",
]

TASK_COUNT_COEFFICIENTS = [
    (5, 1.0),
    (10, 1.1),
    (20, 1.2),
    (999999, 1.3),
]


def get_coefficient(task_count: int) -> float:
    for threshold, coeff in TASK_COUNT_COEFFICIENTS:
        if task_count <= threshold:
            return coeff
    return 1.0


def rank_name(rank_id: int) -> str:
    return RANKS.get(rank_id, f"Ранг {rank_id}")
