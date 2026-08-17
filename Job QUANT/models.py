from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base
import enum
import json


class UserRole(str, enum.Enum):
    WORKER = "worker"
    ADMIN = "admin"


class ApplicationStatus(str, enum.Enum):
    APPLIED = "applied"
    PHOTO_START = "photo_start"
    PHOTO_END = "photo_end"
    CONFIRMED = "confirmed"
    NOT_SHOWED = "not_showed"
    PENALTY = "penalty"


class TaskStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Division(Base):
    __tablename__ = "divisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    counts_for_coeff: Mapped[bool] = mapped_column(Boolean, default=False)

    tasks: Mapped[list["Task"]] = relationship(back_populates="division")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    job_role: Mapped[str] = mapped_column(String(50), default="Без роли")
    job_roles_json: Mapped[str] = mapped_column(Text, default="[]")
    role: Mapped[str] = mapped_column(String(10), default=UserRole.WORKER.value)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    applications: Mapped[list["TaskApplication"]] = relationship(back_populates="user")

    @property
    def job_roles(self) -> list[str]:
        try:
            return json.loads(self.job_roles_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @job_roles.setter
    def job_roles(self, value: list[str]):
        self.job_roles_json = json.dumps(value, ensure_ascii=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[str] = mapped_column(String(20))
    time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_rates_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[int] = mapped_column(BigInteger)
    division_id: Mapped[int | None] = mapped_column(ForeignKey("divisions.id"), nullable=True)
    max_workers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    job_role_filter: Mapped[str | None] = mapped_column(String(50), nullable=True)

    division: Mapped["Division | None"] = relationship(back_populates="tasks")
    applications: Mapped[list["TaskApplication"]] = relationship(back_populates="task")

    @property
    def payment_rates(self) -> dict[int, float]:
        return {int(k): v for k, v in json.loads(self.payment_rates_json).items()}

    @payment_rates.setter
    def payment_rates(self, value: dict[int, float]):
        self.payment_rates_json = json.dumps(value)

    def rate_for_rank(self, rank: int) -> float:
        from config import payment_tier_for_rank
        rates = self.payment_rates
        tier = payment_tier_for_rank(rank)
        return rates.get(tier, 0)


class TaskApplication(Base):
    __tablename__ = "task_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default=ApplicationStatus.APPLIED.value)
    photo_start_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    photo_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_end_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    photo_end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    penalty_amount: Mapped[float] = mapped_column(Float, default=0.0)
    attendance_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="applications")
    task: Mapped["Task"] = relationship(back_populates="applications")
