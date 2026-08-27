import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import sys
import time
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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# =====================================================
# CONFIG
# =====================================================

# ВАЖНО:
# Вставь сюда НОВЫЙ токен бота после перевыпуска через BotFather.
BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "PASTE_NEW_BOT_TOKEN_HERE"
)

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = (
    "https://sevelevd86-lgtm.github.io/"
    "WhiteBear/"
)

# Адрес самого сервера, где работает этот main.py.
# HTML использует его для получения баланса.
SERVER_HOST = "0.0.0.0"
SERVER_PORT = int(
    os.getenv("PORT", "8080")
)

# Этот секрет используется только для проверки
# Telegram Mini App initData.
WEBAPP_BOT_TOKEN = BOT_TOKEN


# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format=(
        "%(asctime)s - %(levelname)s - "
        "%(name)s - %(message)s"
    ),
)

logger = logging.getLogger("whitebear")


# =====================================================
# DATABASE
# =====================================================

DB_NAME = "users.db"


def db_connect():
    conn = sqlite3.connect(
        DB_NAME,
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.0,
            username TEXT,
            first_name TEXT,
            ref_code TEXT UNIQUE,
            invited_by INTEGER DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward REAL DEFAULT 10.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referrer_id, referred_id)
        )
        """
    )

    # Таблица платежей.
    #
    # Она нужна, чтобы один successful_payment
    # никогда не начислялся повторно.

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            payload TEXT,
            telegram_charge_id TEXT UNIQUE,
            provider_charge_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Промокоды теперь тоже серверные.

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            promo TEXT NOT NULL,
            reward REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, promo)
        )
        """
    )

    conn.commit()
    conn.close()

    logger.info("Database initialized")


# =====================================================
# USER FUNCTIONS
# =====================================================

def get_user(user_id: int):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
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

    existing = get_user(user_id)

    if existing:
        cursor.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
            """,
            (
                username,
                first_name,
                user_id
            )
        )

        conn.commit()
        conn.close()

        return existing["ref_code"]

    ref_code = secrets.token_hex(8)

    while True:

        cursor.execute(
            """
            SELECT user_id
            FROM users
            WHERE ref_code = ?
            """,
            (ref_code,)
        )

        if not cursor.fetchone():
            break

        ref_code = secrets.token_hex(8)

    cursor.execute(
        """
        INSERT INTO users (
            user_id,
            balance,
            username,
            first_name,
            ref_code,
            invited_by
        )
        VALUES (?, 0, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            first_name,
            ref_code,
            invited_by
        )
    )

    conn.commit()
    conn.close()

    return ref_code


def ensure_user(
    user_id: int,
    username: str = None,
    first_name: str = None
):

    user = get_user(user_id)

    if not user:
        create_user(
            user_id,
            username,
            first_name
        )

    else:

        conn = db_connect()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
            """,
            (
                username,
                first_name,
                user_id
            )
        )

        conn.commit()
        conn.close()


def get_balance(user_id: int) -> float:

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    if not result:
        return 0.0

    return float(result["balance"])


def update_balance(
    user_id: int,
    amount: float
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET balance = ?
        WHERE user_id = ?
        """,
        (
            round(amount, 2),
            user_id
        )
    )

    if cursor.rowcount == 0:

        cursor.execute(
            """
            INSERT INTO users (
                user_id,
                balance
            )
            VALUES (?, ?)
            """,
            (
                user_id,
                round(amount, 2)
            )
        )

    conn.commit()
    conn.close()


def add_balance(
    user_id: int,
    amount: float
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (
            amount,
            user_id
        )
    )

    if cursor.rowcount == 0:

        cursor.execute(
            """
            INSERT INTO users (
                user_id,
                balance
            )
            VALUES (?, ?)
            """,
            (
                user_id,
                amount
            )
        )

    conn.commit()
    conn.close()


# =====================================================
# REFERRALS
# =====================================================

def get_user_by_ref_code(
    ref_code: str
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT user_id
        FROM users
        WHERE ref_code = ?
        """,
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return (
        result["user_id"]
        if result
        else None
    )


def get_referrals_count(
    user_id: int
):

    conn = db_connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return int(result[0])


def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):

    if referrer_id == referred_id:
        return False

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO referrals (
                referrer_id,
                referred_id,
                reward
            )
            VALUES (?, ?, ?)
            """,
            (
                referrer_id,
                referred_id,
                reward
            )
        )

        cursor.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                referrer_id
            )
        )

        cursor.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                referred_id
            )
        )

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        conn.rollback()

        return False

    finally:

        conn.close()


def get_referral_link(
    user_id: int
):

    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )


# =====================================================
# BOT
# =====================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =====================================================
# MAIN KEYBOARD
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
        text="⭐ Пополнить баланс",
        callback_data="deposit"
    )

    builder.button(
        text="📎 Реферальная ссылка",
        callback_data="referral"
    )

    builder.adjust(
        1,
        2,
        1,
        1
    )

    return builder.as_markup()


# =====================================================
# DEPOSIT KEYBOARD
# =====================================================

def get_deposit_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1",
                    callback_data="pay_1"
                ),
                InlineKeyboardButton(
                    text="⭐ 10",
                    callback_data="pay_10"
                ),
                InlineKeyboardButton(
                    text="⭐ 50",
                    callback_data="pay_50"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 100",
                    callback_data="pay_100"
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


# =====================================================
# START
# =====================================================

@dp.message(Command("start"))
async def start_command(
    message: Message
):

    user_id = message.from_user.id

    username = (
        message.from_user.username
    )

    first_name = (
        message.from_user.first_name
    )

    args = message.text.split()

    invited_by = None

    existing = get_user(user_id)

    if not existing:

        if (
            len(args) > 1
            and args[1].startswith("ref_")
        ):

            ref_code = args[1][4:]

            referrer_id = (
                get_user_by_ref_code(
                    ref_code
                )
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
                        (
                            "🎉 <b>Новый реферал!</b>\n\n"
                            f"Пользователь "
                            f"{first_name} "
                            "перешёл по вашей ссылке.\n"
                            "💰 Вы получили "
                            "+10 ⭐"
                        )
                    )

                except Exception as e:

                    logger.error(
                        "Referral notification error: %s",
                        e
                    )

    else:

        ensure_user(
            user_id,
            username,
            first_name
        )

    balance = get_balance(
        user_id
    )

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await message.answer(
        (
            f"🐻‍❄️ <b>Добро пожаловать "
            f"в DROP, {first_name}!</b>\n\n"
            f"🆔 Ваш ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>\n"
            f"👥 Приглашено друзей: "
            f"<b>{ref_count}</b>\n\n"
            f"📎 Реферальная ссылка:\n"
            f"<code>{ref_link}</code>\n\n"
            "🎮 Нажмите кнопку ниже."
        ),
        reply_markup=get_main_keyboard()
    )


# =====================================================
# DEPOSIT COMMAND
# =====================================================

@dp.message(Command("deposit"))
async def deposit_command(
    message: Message
):

    await message.answer(
        (
            "⭐ <b>Пополнение баланса</b>\n\n"
            "Выберите количество Stars:"
        ),
        reply_markup=get_deposit_keyboard()
    )


# =====================================================
# DEPOSIT CALLBACK
# =====================================================

@dp.callback_query(
    lambda c: c.data == "deposit"
)
async def deposit_callback(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(
        (
            "⭐ <b>Пополнение баланса</b>\n\n"
            "Выберите количество Telegram Stars:"
        ),
        reply_markup=get_deposit_keyboard()
    )

    await callback.answer()


# =====================================================
# CREATE STAR INVOICE
# =====================================================

async def create_star_invoice(
    callback: types.CallbackQuery,
    amount: int
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    payload = (
        f"deposit:{user_id}:{amount}:"
        f"{secrets.token_hex(8)}"
    )

    await bot.send_invoice(
        chat_id=user_id,
        title=f"Пополнение на {amount} ⭐",
        description=(
            f"Пополнение баланса "
            f"White Bear Drop на {amount} "
            f"Telegram Stars."
        ),
        payload=payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{amount} Stars",
                amount=amount
            )
        ]
    )

    await callback.answer()


@dp.callback_query(
    lambda c: (
        c.data
        and c.data.startswith("pay_")
    )
)
async def payment_callback(
    callback: types.CallbackQuery
):

    try:

        amount = int(
            callback.data.split("_")[1]
        )

    except Exception:

        await callback.answer(
            "Ошибка суммы",
            show_alert=True
        )

        return

    allowed_amounts = {
        1,
        10,
        50,
        100
    }

    if amount not in allowed_amounts:

        await callback.answer(
            "Недопустимая сумма",
            show_alert=True
        )

        return

    await create_star_invoice(
        callback,
        amount
    )


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message=(
                "Оплата должна "
                "проходить в Telegram Stars."
            )
        )

        return

    if query.total_amount <= 0:

        await query.answer(
            ok=False,
            error_message="Некорректная сумма."
        )

        return

    await query.answer(
        ok=True
    )


# =====================================================
# SUCCESSFUL PAYMENT
# =====================================================

@dp.message(
    F.successful_payment
)
async def successful_payment_handler(
    message: Message
):

    payment = (
        message.successful_payment
    )

    user_id = (
        message.from_user.id
    )

    amount = int(
        payment.total_amount
    )

    currency = (
        payment.currency
    )

    charge_id = (
        payment.telegram_payment_charge_id
    )

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    payload = payment.invoice_payload

    if currency != "XTR":

        logger.error(
            "Unexpected payment currency: %s",
            currency
        )

        return

    conn = db_connect()
    cursor = conn.cursor()

    # Проверяем, не обработан ли уже этот платеж.

    cursor.execute(
        """
        SELECT id
        FROM payments
        WHERE telegram_charge_id = ?
        """,
        (charge_id,)
    )

    existing_payment = (
        cursor.fetchone()
    )

    if existing_payment:

        conn.close()

        logger.warning(
            "Duplicate payment ignored: %s",
            charge_id
        )

        await message.answer(
            "⚠️ Этот платёж уже был зачислен."
        )

        return

    # Создаём пользователя,
    # если его ещё нет.

    cursor.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    if not cursor.fetchone():

        cursor.execute(
            """
            INSERT INTO users (
                user_id,
                balance,
                username,
                first_name
            )
            VALUES (?, 0, ?, ?)
            """,
            (
                user_id,
                message.from_user.username,
                message.from_user.first_name
            )
        )

    # Начисляем ровно столько Stars,
    # сколько реально оплатил пользователь.

    cursor.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (
            amount,
            user_id
        )
    )

    # Сохраняем платёж.

    cursor.execute(
        """
        INSERT INTO payments (
            user_id,
            amount,
            currency,
            payload,
            telegram_charge_id,
            provider_charge_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            amount,
            currency,
            payload,
            charge_id,
            provider_charge_id
        )
    )

    conn.commit()

    cursor.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    new_balance = (
        float(row["balance"])
        if row
        else float(amount)
    )

    conn.close()

    logger.info(
        "PAYMENT SUCCESS | user=%s | amount=%s | balance=%s | charge=%s",
        user_id,
        amount,
        new_balance,
        charge_id
    )

    await message.answer(
        (
            "✅ <b>Оплата успешно получена!</b>\n\n"
            f"⭐ Зачислено: <b>+{amount}</b>\n"
            f"💰 Ваш баланс: "
            f"<b>{new_balance:.2f} ⭐</b>\n\n"
            "Баланс сайта автоматически "
            "обновится."
        ),
        reply_markup=get_main_keyboard()
    )


# =====================================================
# BALANCE
# =====================================================

@dp.message(Command("balance"))
async def balance_command(
    message: Message
):

    user_id = message.from_user.id

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    await message.answer(
        (
            "💰 <b>Ваш баланс:</b>\n\n"
            f"<b>{balance:.2f} ⭐</b>"
        )
    )


# =====================================================
# PROFILE
# =====================================================

@dp.message(Command("profile"))
async def profile_command(
    message: Message
):

    user_id = message.from_user.id

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    ref_count = get_referrals_count(
        user_id
    )

    await message.answer(
        (
            "👤 <b>Профиль</b>\n\n"
            f"Имя: "
            f"{message.from_user.first_name}\n"
            f"🆔 ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>\n"
            f"👥 Приглашено: "
            f"<b>{ref_count}</b>"
        )
    )


# =====================================================
# CALLBACK BALANCE
# =====================================================

@dp.callback_query(
    lambda c: c.data == "balance"
)
async def balance_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    await callback.message.edit_text(
        (
            "💰 <b>Ваш баланс</b>\n\n"
            f"<b>{balance:.2f} ⭐</b>"
        ),
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
# PROFILE CALLBACK
# =====================================================

@dp.callback_query(
    lambda c: c.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(
        (
            "👤 <b>Профиль</b>\n\n"
            f"Имя: "
            f"{callback.from_user.first_name}\n"
            f"🆔 ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>\n"
            f"👥 Приглашено: "
            f"<b>{ref_count}</b>"
        ),
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
# REFERRAL
# =====================================================

@dp.callback_query(
    lambda c: c.data == "referral"
)
async def referral_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ref_link = get_referral_link(
        user_id
    )

    count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(
        (
            "📎 <b>Реферальная система</b>\n\n"
            f"Ваша ссылка:\n"
            f"<code>{ref_link}</code>\n\n"
            f"👥 Приглашено: <b>{count}</b>\n"
            "💰 За каждого друга: +10 ⭐"
        ),
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
# BACK
# =====================================================

@dp.callback_query(
    lambda c: c.data == "back_to_start"
)
async def back_to_start_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    balance = get_balance(
        user_id
    )

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(
        (
            f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
            f"🆔 ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>\n"
            f"👥 Рефералов: "
            f"<b>{ref_count}</b>"
        ),
        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# TELEGRAM MINI APP INIT DATA VALIDATION
# =====================================================

def validate_telegram_init_data(
    init_data: str,
    max_age: int = 86400
):

    if not init_data:
        return None

    try:

        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = pairs.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        auth_date = pairs.get(
            "auth_date"
        )

        if not auth_date:
            return None

        if (
            time.time()
            - int(auth_date)
            > max_age
        ):
            return None

        data_check_string = "\n".join(
            f"{key}={pairs[key]}"
            for key in sorted(pairs)
        )

        secret_key = hmac.new(
            b"WebAppData",
            WEBAPP_BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user_json = pairs.get(
            "user"
        )

        if not user_json:
            return None

        user = json.loads(
            user_json
        )

        return user

    except Exception as e:

        logger.error(
            "initData validation error: %s",
            e
        )

        return None


# =====================================================
# API: BALANCE
# =====================================================

async def api_balance(
    request: web.Request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_data"
            },
            status=401
        )

    user_id = int(
        user["id"]
    )

    ensure_user(
        user_id,
        user.get("username"),
        user.get("first_name")
    )

    balance = get_balance(
        user_id
    )

    return web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "balance": round(
                balance,
                2
            )
        }
    )


# =====================================================
# API: USER
# =====================================================

async def api_user(
    request: web.Request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_data"
            },
            status=401
        )

    user_id = int(
        user["id"]
    )

    ensure_user(
        user_id,
        user.get("username"),
        user.get("first_name")
    )

    balance = get_balance(
        user_id
    )

    return web.json_response(
        {
            "ok": True,
            "user": {
                "id": user_id,
                "username": user.get(
                    "username"
                ),
                "first_name": user.get(
                    "first_name"
                ),
                "balance": round(
                    balance,
                    2
                )
            }
        }
    )


# =====================================================
# API: PROMO
# =====================================================

async def api_promo(
    request: web.Request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user = validate_telegram_init_data(
        init_data
    )

    if not user:

        return web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_data"
            },
            status=401
        )

    try:

        body = await request.json()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error": "invalid_json"
            },
            status=400
        )

    promo = str(
        body.get("promo", "")
    ).strip().lower()

    rewards = {
        "200": 200000,
        "met200": 200
    }

    if promo not in rewards:

        return web.json_response(
            {
                "ok": False,
                "error": "invalid_promo"
            },
            status=400
        )

    user_id = int(
        user["id"]
    )

    reward = rewards[promo]

    conn = db_connect()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            INSERT INTO promo_uses (
                user_id,
                promo,
                reward
            )
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                promo,
                reward
            )
        )

        cursor.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                user_id
            )
        )

        conn.commit()

    except sqlite3.IntegrityError:

        conn.rollback()
        conn.close()

        return web.json_response(
            {
                "ok": False,
                "error": "promo_already_used"
            },
            status=400
        )

    cursor.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    new_balance = float(
        row["balance"]
    )

    conn.close()

    return web.json_response(
        {
            "ok": True,
            "reward": reward,
            "balance": round(
                new_balance,
                2
            )
        }
    )


# =====================================================
# HEALTH
# =====================================================

async def health(
    request: web.Request
):

    return web.Response(
        text="OK"
    )


# =====================================================
# CORS
# =====================================================

@web.middleware
async def cors_middleware(
    request,
    handler
):

    if request.method == "OPTIONS":

        response = web.Response(
            status=204
        )

    else:

        try:

            response = await handler(
                request
            )

        except web.HTTPException as e:

            response = e

    response.headers[
        "Access-Control-Allow-Origin"
    ] = "*"

    response.headers[
        "Access-Control-Allow-Headers"
    ] = (
        "Content-Type, "
        "X-Telegram-Init-Data"
    )

    response.headers[
        "Access-Control-Allow-Methods"
    ] = (
        "GET, POST, OPTIONS"
    )

    return response


# =====================================================
# WEB SERVER
# =====================================================

def create_web_app():

    app = web.Application(
        middlewares=[
            cors_middleware
        ]
    )

    app.router.add_get(
        "/health",
        health
    )

    app.router.add_get(
        "/api/balance",
        api_balance
    )

    app.router.add_get(
        "/api/user",
        api_user
    )

    app.router.add_post(
        "/api/promo",
        api_promo
    )

    return app


async def run_web_server():

    app = create_web_app()

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        SERVER_HOST,
        SERVER_PORT
    )

    await site.start()

    logger.info(
        "WEB SERVER STARTED: port %s",
        SERVER_PORT
    )

    while True:

        await asyncio.sleep(
            3600
        )


# =====================================================
# BOT COMMANDS
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
            command="deposit",
            description="Пополнить баланс"
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
        "Bot commands and menu configured"
    )


# =====================================================
# GAME
# =====================================================

@dp.message(Command("game"))
async def game_command(
    message: Message
):

    user_id = message.from_user.id

    balance = get_balance(
        user_id
    )

    await message.answer(
        (
            "🎮 <b>White Bear Drop</b>\n\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игры",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    ]
                ]
            ]
        )
    )


# =====================================================
# HELP
# =====================================================

@dp.message(Command("help"))
async def help_command(
    message: Message
):

    await message.answer(
        (
            "📖 <b>Помощь</b>\n\n"
            "/start — главное меню\n"
            "/game — открыть игры\n"
            "/deposit — пополнить баланс\n"
            "/balance — показать баланс\n"
            "/profile — профиль\n\n"
            "⭐ Пополнение производится "
            "через Telegram Stars."
        )
    )


# =====================================================
# STARTUP
# =====================================================

async def main():

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE"
    ):

        raise RuntimeError(
            "Укажи новый BOT_TOKEN "
            "через переменную окружения."
        )

    init_db()

    await set_commands_and_menu()

    web_task = asyncio.create_task(
        run_web_server()
    )

    try:

        logger.info(
            "BOT STARTING..."
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )

    finally:

        web_task.cancel()

        try:
            await web_task
        except asyncio.CancelledError:
            pass

        await bot.session.close()


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped"
        )

    except Exception as e:

        logger.exception(
            "Fatal error: %s",
            e
        )