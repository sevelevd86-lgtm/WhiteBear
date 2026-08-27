import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import sys
from datetime import datetime
from urllib.parse import parse_qsl

from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonWebApp,
    WebAppInfo,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =====================================================
# КОНФИГУРАЦИЯ
# =====================================================

# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН, КОТОРЫЙ ПОЛУЧИШЬ В @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "8918284594:AAFLxOg1eEx4JS6z6V9wHr-t8T3Q9Qwiepg")

BOT_USERNAME = "White_Bear_ROBOT"

# Твой GitHub Pages сайт
WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

# SQLite база
DB_NAME = "users.db"

# Порт берём от хостинга.
# Если переменной PORT нет, используется 8080.
PORT = int(os.getenv("PORT", "8080"))

HOST = "0.0.0.0"


# =====================================================
# ЛОГИРОВАНИЕ
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

logger = logging.getLogger("white_bear")


# =====================================================
# БАЗА ДАННЫХ
# =====================================================

def db_connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    cursor = conn.cursor()

    # Пользователи
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            username TEXT,
            first_name TEXT,
            ref_code TEXT UNIQUE,
            invited_by INTEGER DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Рефералы
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward REAL DEFAULT 10.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referrer_id, referred_id)
        )
    """)

    # Платежи Stars
    #
    # charge_id является уникальным идентификатором платежа Telegram.
    # Это защищает от повторного начисления одной и той же оплаты.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS star_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE NOT NULL,
            provider_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    logger.info("✅ База данных инициализирована")


# =====================================================
# ПОЛЬЗОВАТЕЛИ
# =====================================================

def get_user(user_id: int):
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result


def create_user(
    user_id: int,
    username: str = None,
    first_name: str = None,
    invited_by: int = None
):
    conn = db_connect()
    cursor = conn.cursor()

    # Проверяем, существует ли пользователь
    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    if cursor.fetchone():
        conn.close()
        return None

    # Создаём уникальный referral code
    while True:
        ref_code = secrets.token_hex(8)

        cursor.execute(
            "SELECT user_id FROM users WHERE ref_code = ?",
            (ref_code,)
        )

        if not cursor.fetchone():
            break

    cursor.execute("""
        INSERT INTO users (
            user_id,
            balance,
            username,
            first_name,
            ref_code,
            invited_by
        )
        VALUES (?, 0.0, ?, ?, ?, ?)
    """, (
        user_id,
        username,
        first_name,
        ref_code,
        invited_by
    ))

    conn.commit()
    conn.close()

    return ref_code


def ensure_user(
    user_id: int,
    username: str = None,
    first_name: str = None
):
    user = get_user(user_id)

    if user:
        # Обновляем актуальные данные Telegram
        conn = db_connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
        """, (
            username,
            first_name,
            user_id
        ))

        conn.commit()
        conn.close()

        return get_user(user_id)

    create_user(
        user_id=user_id,
        username=username,
        first_name=first_name
    )

    return get_user(user_id)


def get_user_by_ref_code(ref_code: str):
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return result["user_id"] if result else None


# =====================================================
# БАЛАНС
# =====================================================

def get_balance(user_id: int) -> float:
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    if not result:
        return 0.0

    return float(result["balance"])


def update_balance(user_id: int, new_balance: float):
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET balance = ?
        WHERE user_id = ?
    """, (
        new_balance,
        user_id
    ))

    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO users (
                user_id,
                balance
            )
            VALUES (?, ?)
        """, (
            user_id,
            new_balance
        ))

    conn.commit()
    conn.close()


def add_balance(user_id: int, amount: float):
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO users (
                user_id,
                balance
            )
            VALUES (?, ?)
        """, (
            user_id,
            amount
        ))

    conn.commit()
    conn.close()


# =====================================================
# РЕФЕРАЛЫ
# =====================================================

def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):
    if referrer_id == referred_id:
        return False

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM referrals
        WHERE referrer_id = ?
        AND referred_id = ?
    """, (
        referrer_id,
        referred_id
    ))

    if cursor.fetchone():
        conn.close()
        return False

    cursor.execute("""
        INSERT INTO referrals (
            referrer_id,
            referred_id,
            reward
        )
        VALUES (?, ?, ?)
    """, (
        referrer_id,
        referred_id,
        reward
    ))

    # Начисляем рефереру
    cursor.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        reward,
        referrer_id
    ))

    # Начисляем приглашённому
    cursor.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        reward,
        referred_id
    ))

    conn.commit()
    conn.close()

    return True


def get_referrals_count(user_id: int) -> int:
    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
    """, (
        user_id,
    ))

    result = cursor.fetchone()

    conn.close()

    return int(result[0])


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# =====================================================
# TELEGRAM WEBAPP INIT DATA
# =====================================================
#
# Сайт передаёт Telegram.WebApp.initData.
# Мы проверяем подпись Telegram.
#
# Это важно:
# нельзя просто отправить на API чужой user_id
# и получить чужой баланс.
# =====================================================

def validate_telegram_webapp_init_data(init_data: str):
    if not init_data:
        return None

    try:
        parsed = dict(parse_qsl(
            init_data,
            keep_blank_values=True
        ))

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        # В Telegram WebApp secret key:
        # HMAC-SHA256(bot_token, "WebAppData")
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
        )

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            logger.warning("❌ Неверная подпись Telegram WebApp")
            return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        user = json.loads(user_json)

        return user

    except Exception as e:
        logger.error(
            f"Ошибка проверки Telegram WebApp: {e}"
        )
        return None


# =====================================================
# TELEGRAM BOT
# =====================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =====================================================
# ГЛАВНАЯ КЛАВИАТУРА
# =====================================================

def get_main_keyboard():

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🎮 Открыть игры",
        web_app=WebAppInfo(
            url=WEBAPP_URL
        )
    )

    builder.button(
        text="💰 Баланс",
        callback_data="balance"
    )

    builder.button(
        text="👤 Профиль",
        callback_data="profile"
    )

    builder.button(
        text="📎 Реферальная ссылка",
        callback_data="referral"
    )

    builder.adjust(1, 2, 1)

    return builder.as_markup()


# =====================================================
# КНОПКИ ПОПОЛНЕНИЯ
# =====================================================

def get_deposit_keyboard():

    builder = InlineKeyboardBuilder()

    builder.button(
        text="⭐ 1",
        callback_data="deposit_1"
    )

    builder.button(
        text="⭐ 10",
        callback_data="deposit_10"
    )

    builder.button(
        text="⭐ 50",
        callback_data="deposit_50"
    )

    builder.button(
        text="⭐ 100",
        callback_data="deposit_100"
    )

    builder.button(
        text="⭐ 250",
        callback_data="deposit_250"
    )

    builder.button(
        text="⭐ 500",
        callback_data="deposit_500"
    )

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_start"
    )

    builder.adjust(2, 2, 2, 1)

    return builder.as_markup()


# =====================================================
# ОТПРАВКА INVOICE TELEGRAM STARS
# =====================================================

async def send_stars_invoice(
    message: Message,
    amount: int
):

    if amount <= 0:
        await message.answer(
            "❌ Некорректная сумма."
        )
        return

    payload = f"deposit:{message.from_user.id}:{secrets.token_hex(8)}"

    await bot.send_invoice(
        chat_id=message.chat.id,

        title=f"Пополнение на {amount} ⭐",

        description=(
            f"Пополнение баланса White Bear на "
            f"{amount} Telegram Stars."
        ),

        payload=payload,

        provider_token="",

        currency="XTR",

        prices=[
            LabeledPrice(
                label=f"{amount} ⭐",
                amount=amount
            )
        ],

        start_parameter=f"deposit_{amount}",

        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,

        is_flexible=False
    )


# =====================================================
# START
# =====================================================

@dp.message(Command("start"))
async def start_command(message: Message):

    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    args = message.text.split()

    invited_by = None

    existing_user = get_user(user_id)

    if not existing_user:

        if len(args) > 1:

            start_arg = args[1]

            if start_arg.startswith("ref_"):

                ref_code = start_arg[4:]

                referrer_id = get_user_by_ref_code(
                    ref_code
                )

                if (
                    referrer_id
                    and referrer_id != user_id
                ):
                    invited_by = referrer_id

        create_user(
            user_id,
            username,
            first_name,
            invited_by
        )

        if invited_by:

            success = add_referral(
                invited_by,
                user_id,
                10.0
            )

            if success:

                try:

                    await bot.send_message(
                        invited_by,

                        f"🎉 <b>Новый реферал!</b>\n\n"
                        f"Пользователь "
                        f"<b>{first_name}</b> "
                        f"перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили "
                        f"<b>+10 ⭐</b>\n"
                        f"📊 Всего приглашено: "
                        f"<b>{get_referrals_count(invited_by)}</b>"
                    )

                except Exception as e:

                    logger.error(
                        f"Ошибка уведомления реферера: {e}"
                    )

    else:

        # Если пользователь уже существует,
        # обновляем имя/username.
        ensure_user(
            user_id,
            username,
            first_name
        )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    # Если человек открыл бота с ?start=deposit
    if len(args) > 1 and args[1] == "deposit":

        await message.answer(
            "⭐ <b>Пополнение баланса</b>\n\n"
            "Выберите количество Telegram Stars:\n\n"
            "После успешной оплаты Stars "
            "автоматически зачислятся "
            "на ваш баланс.",
            reply_markup=get_deposit_keyboard()
        )

        return

    await message.answer(

        f"🐻‍❄️ <b>Добро пожаловать в DROP, "
        f"{first_name}!</b>\n\n"

        f"🆔 Ваш ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"

        f"💡 Приглашайте друзей и "
        f"получайте по 10 ⭐ за каждого!\n\n"

        f"🎮 Нажмите кнопку ниже, "
        f"чтобы открыть игру.",

        reply_markup=get_main_keyboard()
    )


# =====================================================
# GAME
# =====================================================

@dp.message(Command("game"))
async def game_command(message: Message):

    user_id = message.from_user.id

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(user_id)

    await message.answer(

        f"🎮 <b>Открываем игры...</b>\n\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игры",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить баланс",
                        callback_data="deposit"
                    )
                ]

            ]
        )
    )


# =====================================================
# BALANCE
# =====================================================

@dp.message(Command("balance"))
async def balance_command(message: Message):

    user_id = message.from_user.id

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    await message.answer(

        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n\n"

        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить",
                        callback_data="deposit"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start"
                    )
                ]

            ]
        )
    )


# =====================================================
# PROFILE
# =====================================================

@dp.message(Command("profile"))
async def profile_command(message: Message):

    user_id = message.from_user.id

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await message.answer(

        f"👤 <b>Профиль</b>\n\n"

        f"Имя: "
        f"<b>{message.from_user.first_name}</b>\n"

        f"🆔 ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить баланс",
                        callback_data="deposit"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start"
                    )
                ]

            ]
        )
    )


# =====================================================
# HELP
# =====================================================

@dp.message(Command("help"))
async def help_command(message: Message):

    await message.answer(

        "📖 <b>Помощь</b>\n\n"

        "/start — Главное меню\n"
        "/game — Открыть игры\n"
        "/balance — Показать баланс\n"
        "/profile — Профиль\n"
        "/help — Помощь\n\n"

        "⭐ Пополнение происходит "
        "через Telegram Stars.\n\n"

        "После успешной оплаты Stars "
        "автоматически начисляются "
        "на баланс вашего аккаунта."
    )


# =====================================================
# CALLBACK: BALANCE
# =====================================================

@dp.callback_query(F.data == "balance")
async def balance_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    await callback.message.edit_text(

        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n\n"

        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить",
                        callback_data="deposit"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start"
                    )
                ]

            ]
        )
    )

    await callback.answer()


# =====================================================
# CALLBACK: PROFILE
# =====================================================

@dp.callback_query(F.data == "profile")
async def profile_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(

        f"👤 <b>Профиль</b>\n\n"

        f"Имя: "
        f"<b>{callback.from_user.first_name}</b>\n"

        f"🆔 ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить баланс",
                        callback_data="deposit"
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start"
                    )
                ]

            ]
        )
    )

    await callback.answer()


# =====================================================
# CALLBACK: DEPOSIT MENU
# =====================================================

@dp.callback_query(F.data == "deposit")
async def deposit_callback(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(

        "⭐ <b>Пополнение баланса</b>\n\n"

        "Выберите количество "
        "Telegram Stars:\n\n"

        "После успешной оплаты "
        "сумма автоматически "
        "зачислится на ваш баланс.",

        reply_markup=get_deposit_keyboard()
    )

    await callback.answer()


# =====================================================
# CALLBACK: DEPOSIT AMOUNTS
# =====================================================

@dp.callback_query(F.data.startswith("deposit_"))
async def deposit_amount_callback(
    callback: types.CallbackQuery
):

    data = callback.data

    # Защита от callback "deposit"
    if data == "deposit":
        return

    try:

        amount = int(
            data.replace(
                "deposit_",
                ""
            )
        )

    except ValueError:

        await callback.answer(
            "❌ Некорректная сумма.",
            show_alert=True
        )

        return

    allowed_amounts = {
        1,
        10,
        50,
        100,
        250,
        500
    }

    if amount not in allowed_amounts:

        await callback.answer(
            "❌ Такая сумма недоступна.",
            show_alert=True
        )

        return

    await callback.answer(
        "⭐ Создаём оплату..."
    )

    await send_stars_invoice(
        callback.message,
        amount
    )


# =====================================================
# CALLBACK: REFERRAL
# =====================================================

@dp.callback_query(F.data == "referral")
async def referral_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(

        f"📎 <b>Ваша реферальная ссылка:</b>\n\n"

        f"<code>{ref_link}</code>\n\n"

        f"💡 Приглашайте друзей и "
        f"получайте по 10 ⭐ за каждого!",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start"
                    )
                ]

            ]
        )
    )

    await callback.answer()


# =====================================================
# CALLBACK: BACK
# =====================================================

@dp.callback_query(F.data == "back_to_start")
async def back_to_start_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(

        f"🐻‍❄️ <b>Добро пожаловать в DROP, "
        f"{callback.from_user.first_name}!</b>\n\n"

        f"🆔 Ваш ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",

        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):

    try:

        payload = query.invoice_payload

        if not payload.startswith("deposit:"):

            await query.answer(
                ok=False,
                error_message="❌ Некорректный платёж."
            )

            return

        parts = payload.split(":")

        if len(parts) < 3:

            await query.answer(
                ok=False,
                error_message="❌ Некорректные данные платежа."
            )

            return

        payment_user_id = int(parts[1])

        if payment_user_id != query.from_user.id:

            await query.answer(
                ok=False,
                error_message=(
                    "❌ Этот платёж принадлежит "
                    "другому пользователю."
                )
            )

            return

        await query.answer(ok=True)

        logger.info(
            f"✅ PreCheckout подтверждён: "
            f"user={query.from_user.id}, "
            f"amount={query.total_amount} XTR"
        )

    except Exception as e:

        logger.error(
            f"Ошибка PreCheckout: {e}"
        )

        try:

            await query.answer(
                ok=False,
                error_message="❌ Ошибка проверки платежа."
            )

        except Exception:
            pass


# =====================================================
# УСПЕШНАЯ ОПЛАТА STARS
# =====================================================

@dp.message(
    lambda message:
    message.successful_payment is not None
)
async def successful_payment_handler(
    message: Message
):

    payment = message.successful_payment

    user_id = message.from_user.id

    amount = int(payment.total_amount)

    currency = payment.currency

    telegram_charge_id = (
        payment.telegram_payment_charge_id
    )

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    payload = payment.invoice_payload

    logger.info(
        "💰 Получена успешная оплата: "
        f"user={user_id}, "
        f"amount={amount}, "
        f"currency={currency}, "
        f"charge_id={telegram_charge_id}"
    )

    # Проверяем валюту
    if currency != "XTR":

        logger.error(
            f"❌ Неожиданная валюта: {currency}"
        )

        await message.answer(
            "❌ Ошибка валюты платежа. "
            "Обратитесь в поддержку."
        )

        return

    # Проверяем payload
    try:

        parts = payload.split(":")

        if len(parts) < 3:
            raise ValueError("invalid payload")

        payload_user_id = int(parts[1])

        if payload_user_id != user_id:
            logger.error(
                "❌ ID пользователя в payload "
                "не совпадает с оплатившим."
            )

            await message.answer(
                "❌ Ошибка привязки платежа."
            )

            return

    except Exception as e:

        logger.error(
            f"Ошибка payload платежа: {e}"
        )

        await message.answer(
            "❌ Ошибка обработки платежа."
        )

        return

    # -------------------------------------------------
    # Идемпотентность:
    # проверяем, не начисляли ли уже этот charge_id
    # -------------------------------------------------

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM star_payments
        WHERE telegram_charge_id = ?
    """, (
        telegram_charge_id,
    ))

    existing_payment = cursor.fetchone()

    if existing_payment:

        conn.close()

        logger.warning(
            f"⚠️ Повторная обработка платежа: "
            f"{telegram_charge_id}"
        )

        await message.answer(
            "ℹ️ Этот платёж уже был зачислен."
        )

        return

    # -------------------------------------------------
    # Создаём пользователя, если его нет
    # -------------------------------------------------

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    if not cursor.fetchone():

        # Генерируем ref_code
        while True:

            ref_code = secrets.token_hex(8)

            cursor.execute(
                "SELECT user_id FROM users WHERE ref_code = ?",
                (ref_code,)
            )

            if not cursor.fetchone():
                break

        cursor.execute("""
            INSERT INTO users (
                user_id,
                balance,
                username,
                first_name,
                ref_code
            )
            VALUES (?, 0.0, ?, ?, ?)
        """, (
            user_id,
            message.from_user.username,
            message.from_user.first_name,
            ref_code
        ))

    # -------------------------------------------------
    # Начисляем Stars
    # -------------------------------------------------

    cursor.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))

    # -------------------------------------------------
    # Сохраняем платёж
    # -------------------------------------------------

    cursor.execute("""
        INSERT INTO star_payments (
            user_id,
            telegram_charge_id,
            provider_charge_id,
            amount,
            currency,
            payload
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        telegram_charge_id,
        provider_charge_id,
        amount,
        currency,
        payload
    ))

    conn.commit()

    # Получаем новый баланс
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    new_balance = (
        float(result["balance"])
        if result
        else float(amount)
    )

    conn.close()

    # -------------------------------------------------
    # Сообщаем пользователю
    # -------------------------------------------------

    await message.answer(

        "✅ <b>Оплата успешно получена!</b>\n\n"

        f"⭐ Зачислено: "
        f"<b>+{amount} ⭐</b>\n"

        f"💰 Новый баланс: "
        f"<b>{new_balance:.2f} ⭐</b>\n\n"

        "Теперь баланс доступен "
        "в вашем профиле на сайте."
    )


# =====================================================
# WEB APP API
# =====================================================

async def health(request):
    return web.json_response({
        "status": "ok",
        "service": "white_bear_bot"
    })


# =====================================================
# API: ПОЛУЧИТЬ БАЛАНС
# =====================================================

async def api_balance(request):

    try:

        # Разрешаем CORS
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": (
                "Content-Type, X-Telegram-Init-Data"
            ),
            "Access-Control-Allow-Methods": (
                "GET, POST, OPTIONS"
            )
        }

        # initData можно передавать:
        # POST JSON
        # или заголовком
        init_data = request.headers.get(
            "X-Telegram-Init-Data",
            ""
        )

        if request.method == "POST":

            try:
                body = await request.json()

                if body.get("initData"):
                    init_data = body["initData"]

            except Exception:
                pass

        # Проверяем Telegram WebApp
        telegram_user = (
            validate_telegram_webapp_init_data(
                init_data
            )
        )

        if not telegram_user:

            return web.json_response(
                {
                    "ok": False,
                    "error": "invalid_telegram_data"
                },
                status=401,
                headers=headers
            )

        user_id = int(
            telegram_user["id"]
        )

        username = telegram_user.get(
            "username"
        )

        first_name = telegram_user.get(
            "first_name"
        )

        # Создаём/обновляем пользователя
        ensure_user(
            user_id,
            username,
            first_name
        )

        balance = get_balance(
            user_id
        )

        return web.json_response(

            {
                "ok": True,

                "user": {
                    "id": user_id,
                    "username": username,
                    "first_name": first_name
                },

                "balance": round(
                    balance,
                    2
                )
            },

            headers=headers
        )

    except Exception as e:

        logger.error(
            f"Ошибка API balance: {e}"
        )

        return web.json_response(

            {
                "ok": False,
                "error": "server_error"
            },

            status=500
        )


# =====================================================
# OPTIONS
# =====================================================

async def api_options(request):

    return web.Response(
        status=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": (
                "Content-Type, X-Telegram-Init-Data"
            ),
            "Access-Control-Allow-Methods": (
                "GET, POST, OPTIONS"
            ),
            "Access-Control-Max-Age": "86400"
        }
    )


# =====================================================
# WEB SERVER
# =====================================================

async def start_web_server():

    app = web.Application()

    # Проверка сервера
    app.router.add_get(
        "/health",
        health
    )

    # API баланса
    app.router.add_get(
        "/api/balance",
        api_balance
    )

    app.router.add_post(
        "/api/balance",
        api_balance
    )

    # CORS OPTIONS
    app.router.add_options(
        "/api/balance",
        api_options
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )

    await site.start()

    logger.info(
        f"🌐 API сервер запущен "
        f"на {HOST}:{PORT}"
    )

    return runner


# =====================================================
# КОМАНДЫ И MENU BUTTON
# =====================================================

async def set_commands_and_menu():

    commands = [

        BotCommand(
            command="start",
            description="Главное меню"
        ),

        BotCommand(
            command="game",
            description="Открыть игры"
        ),

        BotCommand(
            command="balance",
            description="Показать баланс"
        ),

        BotCommand(
            command="profile",
            description="Профиль"
        ),

        BotCommand(
            command="help",
            description="Помощь"
        )

    ]

    await bot.set_my_commands(
        commands,
        scope=BotCommandScopeDefault()
    )

    await bot.set_chat_menu_button(

        menu_button=MenuButtonWebApp(

            text="🎮 Играть",

            web_app=WebAppInfo(
                url=WEBAPP_URL
            )
        )
    )

    logger.info(
        "✅ Команды и кнопка меню установлены"
    )


# =====================================================
# ЗАПУСК
# =====================================================

async def main():

    logger.info(
        "🚀 Запуск White Bear..."
    )

    # Проверяем токен
    if (
        not BOT_TOKEN
        or BOT_TOKEN == "ВСТАВЬ_НОВЫЙ_ТОКЕН_БОТА"
    ):

        logger.error(
            "❌ BOT_TOKEN не установлен!"
        )

        return

    # База
    init_db()

    # Команды
    await set_commands_and_menu()

    # HTTP API
    web_runner = await start_web_server()

    try:

        logger.info(
            "🤖 Telegram bot запускается..."
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )

    finally:

        await web_runner.cleanup()

        await bot.session.close()


# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "👋 Бот остановлен"
        )

    except Exception as e:

        logger.exception(
            f"❌ Критическая ошибка: {e}"
        )