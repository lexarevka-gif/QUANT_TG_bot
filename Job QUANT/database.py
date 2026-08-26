from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL, DEFAULT_DIVISIONS

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        from models import User, Task, TaskApplication, TaskPoint, TaskTemplate, TaskTemplatePoint, Division
        await conn.run_sync(Base.metadata.create_all)

    await _migrate_columns()
    await _seed_divisions()


async def _migrate_columns():
    migrations = [
        "ALTER TABLE tasks ADD COLUMN division_id INTEGER REFERENCES divisions(id)",
        "ALTER TABLE tasks ADD COLUMN max_workers INTEGER",
        "ALTER TABLE tasks ADD COLUMN is_template BOOLEAN DEFAULT 0",
        "ALTER TABLE tasks ADD COLUMN job_role_filter TEXT",
        "ALTER TABLE users ADD COLUMN job_roles_json TEXT DEFAULT '[]'",
        "ALTER TABLE task_applications ADD COLUMN attendance_confirmed BOOLEAN",
        "ALTER TABLE task_applications ADD COLUMN confirm_sent_at DATETIME",
    ]
    async with engine.begin() as conn:
        for sql in migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass


async def _seed_divisions():
    from models import Division
    from sqlalchemy import select
    async with async_session() as session:
        existing = (await session.execute(select(Division))).scalars().all()
        existing_names = {d.name for d in existing}
        for name, counts in DEFAULT_DIVISIONS:
            if name not in existing_names:
                session.add(Division(name=name, counts_for_coeff=counts))
        await session.commit()
