from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from database import async_session
from models import User
from config import ADMIN_IDS, ADMIN_MIN_RANK
from keyboards import admin_keyboard, worker_keyboard

router = Router()


class Registration(StatesGroup):
    full_name = State()
    phone = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == message.from_user.id))
        user = result.scalar_one_or_none()

    if user:
        is_adm = user.tg_id in ADMIN_IDS or user.rank >= ADMIN_MIN_RANK
        kb = admin_keyboard() if is_adm else worker_keyboard()
        role_text = "Админ" if is_adm else "Работник"
        await message.answer(f"Вы уже зарегистрированы как {role_text}.", reply_markup=kb)
        return

    await state.set_state(Registration.full_name)
    await message.answer("Добро пожаловать! Для регистрации введите ваше ФИО:")


@router.message(Registration.full_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Отправьте ваш номер телефона:", reply_markup=kb)
    await state.set_state(Registration.phone)


@router.message(Registration.phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await _finish_registration(message, state, phone)


@router.message(Registration.phone)
async def process_phone_text(message: Message, state: FSMContext):
    phone = message.text.strip()
    await _finish_registration(message, state, phone)


async def _finish_registration(message: Message, state: FSMContext, phone: str):
    data = await state.get_data()
    is_adm = message.from_user.id in ADMIN_IDS

    async with async_session() as session:
        user = User(
            tg_id=message.from_user.id,
            full_name=data["full_name"],
            phone=phone,
            role="admin" if is_adm else "worker",
            rank=ADMIN_MIN_RANK if is_adm else 1,
        )
        session.add(user)
        await session.commit()

    await state.clear()
    kb = admin_keyboard() if is_adm else worker_keyboard()
    role_text = "администратор" if is_adm else "работник"
    await message.answer(
        f"Регистрация завершена!\n"
        f"Имя: {data['full_name']}\n"
        f"Роль: {role_text}",
        reply_markup=kb,
    )
