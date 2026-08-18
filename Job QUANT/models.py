from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, Float, DateTime, ForeignKey, Text
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


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    job_role: Mapped[str] = mapped_column(String(50), default="Без роли")
    role: Mapped[str] = mapped_column(String(10), default=UserRole.WORKER.value)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    applications: Mapped[list["TaskApplication"]] = relationship(back_populates="user")


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

    applications: Mapped[list["TaskApplication"]] = relationship(back_populates="task")
    points: Mapped[list["TaskPoint"]] = relationship(back_populates="task", cascade="all, delete-orphan")

    @property
    def payment_rates(self) -> dict[int, float]:
        return {int(k): v for k, v in json.loads(self.payment_rates_json).items()}

    @payment_rates.setter
    def payment_rates(self, value: dict[int, float]):
        self.payment_rates_json = json.dumps(value)

    def rate_for_rank(self, rank: int) -> float:
        rates = self.payment_rates
        return rates.get(rank, 0)


class TaskPoint(Base):
    __tablename__ = "task_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    address: Mapped[str] = mapped_column(String(300))
    capacity: Mapped[int] = mapped_column(Integer)

    task: Mapped["Task"] = relationship(back_populates="points")
    applications: Mapped[list["TaskApplication"]] = relationship(back_populates="point")


class TaskApplication(Base):
    __tablename__ = "task_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    point_id: Mapped[int | None] = mapped_column(ForeignKey("task_points.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ApplicationStatus.APPLIED.value)
    photo_start_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    photo_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_end_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    photo_end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    penalty_amount: Mapped[float] = mapped_column(Float, default=0.0)

    user: Mapped["User"] = relationship(back_populates="applications")
    task: Mapped["Task"] = relationship(back_populates="applications")
    point: Mapped["TaskPoint | None"] = relationship(back_populates="applications")


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payment_rates_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    template_points: Mapped[list["TaskTemplatePoint"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )

    @property
    def payment_rates(self) -> dict[int, float]:
        return {int(k): v for k, v in json.loads(self.payment_rates_json).items()}

    @payment_rates.setter
    def payment_rates(self, value: dict[int, float]):
        self.payment_rates_json = json.dumps(value)


class TaskTemplatePoint(Base):
    __tablename__ = "task_template_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("task_templates.id"))
    address: Mapped[str] = mapped_column(String(300))
    capacity: Mapped[int] = mapped_column(Integer)

    template: Mapped["TaskTemplate"] = relationship(back_populates="template_points")
