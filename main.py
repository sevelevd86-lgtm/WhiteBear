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
from pathlib import Path
from urllib.parse import parse_qsl
import aiohttp
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
    WebAppInfo,
    Update,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
# ============================================================
# НАСТРОЙКИ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = "White_Bear_ROBOT"
PORT = int(os.getenv("PORT", "8080"))
DB_NAME = "users.db"
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"
# ============================================================
# ПУБЛИЧНЫЕ URL
# ============================================================
WEBAPP_URL = (
    "https://frabjous-gnome-c18254.netlify.app"
)
BOTHOST_URL = (
    "https://bot-1787862010-6746-jix44.bothost.tech"
)
WEBHOOK_URL = (
    BOTHOST_URL +
    "/webhook"
)
HEALTH_URL = (
    BOTHOST_URL +
    "/health"
)
BALANCE_URL = (
    BOTHOST_URL +
    "/api/balance"
)
USER_URL = (
    BOTHOST_URL +
    "/api/user"
)
# Telegram initData разрешаем использовать 24 часа
INIT_DATA_MAX_AGE = 86400
# ============================================================
# ЛОГИ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format=(
        "%(asctime)s - "
        "%(levelname)s - "
        "%(name)s - "
        "%(message)s"
    ),
)
logger = logging.getLogger("white_bear")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не установлен.\n"
        "Добавь BOT_TOKEN в переменные окружения Bothost."
    )
# ============================================================
# DATABASE
# ============================================================
def db():
    connection = sqlite3.connect(
        DB_NAME,
        timeout=30
    )
    connection.row_factory = sqlite3.Row
    return connection
def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0,
            username TEXT,
            first_name TEXT,
            ref_code TEXT UNIQUE,
            invited_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward REAL DEFAULT 10,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referrer_id, referred_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE NOT NULL,
            provider_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logger.info(
        "✅ База данных инициализирована"
    )
# ============================================================
# USERS
# ============================================================
def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    return result
def create_user(
    user_id: int,
    username=None,
    first_name=None,
    invited_by=None
):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    exists = cur.fetchone()
    if exists:
        cur.execute(
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
        return
    while True:
        ref_code = secrets.token_hex(8)
        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE ref_code = ?
            """,
            (ref_code,)
        )
        if not cur.fetchone():
            break
    cur.execute(
        """
        INSERT INTO users (
            user_id,
            balance,
            username,
            first_name,
            ref_code,
            invited_by
        )
        VALUES (
            ?,
            0,
            ?,
            ?,
            ?,
            ?
        )
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
    logger.info(
        f"👤 Создан пользователь {user_id}"
    )
def get_balance(user_id: int) -> float:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return 0.0
    return float(row["balance"])
def add_balance(
    user_id: int,
    amount: float
):
    conn = db()
    cur = conn.cursor()
    cur.execute(
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
    conn.commit()
    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return (
        float(row["balance"])
        if row
        else 0.0
    )
# ============================================================
# REFERRALS
# ============================================================
def get_referrals_count(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (user_id,)
    )
    result = cur.fetchone()
    conn.close()
    return int(result[0])
def get_referral_link(user_id: int):
    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )
def get_user_by_ref_code(ref_code: str):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE ref_code = ?
        """,
        (ref_code,)
    )
    row = cur.fetchone()
    conn.close()
    return (
        row["user_id"]
        if row
        else None
    )
def add_referral(
    referrer_id: int,
    referred_id: int
):
    if referrer_id == referred_id:
        return False
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id
        FROM referrals
        WHERE referrer_id = ?
          AND referred_id = ?
        """,
        (
            referrer_id,
            referred_id
        )
    )
    if cur.fetchone():
        conn.close()
        return False
    cur.execute(
        """
        INSERT INTO referrals (
            referrer_id,
            referred_id,
            reward
        )
        VALUES (
            ?,
            ?,
            10
        )
        """,
        (
            referrer_id,
            referred_id
        )
    )
    cur.execute(
        """
        UPDATE users
        SET balance = balance + 10
        WHERE user_id IN (?, ?)
        """,
        (
            referrer_id,
            referred_id
        )
    )
    conn.commit()
    conn.close()
    return True
# ============================================================
# TELEGRAM INIT DATA
# ============================================================
def validate_init_data(init_data: str):
    if not init_data:
        return None
    try:
        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )
        received_hash = data.pop(
            "hash",
            None
        )
        if not received_hash:
            return None
        auth_date = data.get(
            "auth_date"
        )
        if not auth_date:
            return None
        auth_timestamp = int(
            auth_date
        )
        now = int(
            datetime.now().timestamp()
        )
        if (
            now -
            auth_timestamp
            >
            INIT_DATA_MAX_AGE
        ):
            logger.warning(
                "⚠️ Telegram initData устарел"
            )
            return None
        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
        )
        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
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
            logger.warning(
                "⚠️ Неверная подпись Telegram initData"
            )
            return None
        user_string = data.get(
            "user"
        )
        if not user_string:
            return None
        return json.loads(
            user_string
        )
    except Exception as e:
        logger.exception(
            f"❌ Ошибка проверки initData: {e}"
        )
        return None
# ============================================================
# BOT
# ============================================================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)
dp = Dispatcher()
# ============================================================
# MAIN KEYBOARD
# ============================================================
def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎮 Открыть игру",
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
        text="⭐ Пополнить",
        callback_data="deposit"
    )
    builder.button(
        text="📎 Реферал",
        callback_data="referral"
    )
    builder.adjust(
        1,
        2,
        2
    )
    return builder.as_markup()
def deposit_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1",
                    callback_data="buy_1"
                ),
                InlineKeyboardButton(
                    text="⭐ 10",
                    callback_data="buy_10"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 50",
                    callback_data="buy_50"
                ),
                InlineKeyboardButton(
                    text="⭐ 100",
                    callback_data="buy_100"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="back"
                )
            ]
        ]
    )
# ============================================================
# START
# ============================================================
@dp.message(Command("start"))
async def start(
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
    if not get_user(user_id):
        if (
            len(args) > 1
            and args[1].startswith("ref_")
        ):
            ref_code = args[1][4:]
            invited_by = (
                get_user_by_ref_code(
                    ref_code
                )
            )
            if invited_by == user_id:
                invited_by = None
        create_user(
            user_id,
            username,
            first_name,
            invited_by
        )
        if invited_by:
            if add_referral(
                invited_by,
                user_id
            ):
                try:
                    await bot.send_message(
                        invited_by,
                        (
                            "🎉 <b>Новый реферал!</b>\n\n"
                            "Вы получили <b>+10 ⭐</b>."
                        )
                    )
                except Exception:
                    pass
    else:
        create_user(
            user_id,
            username,
            first_name
        )
    balance = get_balance(
        user_id
    )
    await message.answer(
        (
            f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n\n"
            f"🎮 Открывайте игру кнопкой ниже."
        ),
        reply_markup=main_keyboard()
    )
# ============================================================
# GAME COMMAND
# ============================================================
@dp.message(Command("game"))
async def game_command(
    message: Message
):
    await message.answer(
        "🎮 <b>White Bear Drop</b>\n\n"
        "Нажмите кнопку ниже:",
        reply_markup=main_keyboard()
    )
# ============================================================
# BALANCE COMMAND
# ============================================================
@dp.message(Command("balance"))
async def balance_command(
    message: Message
):
    balance = get_balance(
        message.from_user.id
    )
    await message.answer(
        f"💰 Ваш баланс: "
        f"<b>{balance:.2f} ⭐</b>"
    )
# ============================================================
# PROFILE COMMAND
# ============================================================
@dp.message(Command("profile"))
async def profile_command(
    message: Message
):
    user_id = message.from_user.id
    balance = get_balance(
        user_id
    )
    await message.answer(
        (
            f"👤 <b>Профиль</b>\n\n"
            f"🆔 Telegram ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>"
        ),
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
                        text="🎮 Открыть игру",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ]
            ]
        )
    )
# ============================================================
# DEPOSIT
# ============================================================
@dp.callback_query(
    F.data == "deposit"
)
async def deposit(
    callback: types.CallbackQuery
):
    await callback.message.edit_text(
        (
            "⭐ <b>Пополнение баланса</b>\n\n"
            "Выберите количество Stars.\n\n"
            "После успешной оплаты сумма "
            "автоматически зачислится "
            "на ваш Telegram ID."
        ),
        reply_markup=deposit_keyboard()
    )
    await callback.answer()
# ============================================================
# CREATE INVOICE
# ============================================================
async def create_invoice(
    user_id: int,
    amount: int
):
    await bot.send_invoice(
        chat_id=user_id,
        title=(
            f"Пополнение "
            f"{amount} ⭐"
        ),
        description=(
            f"Пополнение игрового баланса "
            f"на {amount} Telegram Stars."
        ),
        payload=(
            f"deposit:"
            f"{user_id}:"
            f"{amount}:"
            f"{secrets.token_hex(8)}"
        ),
        currency="XTR",
        prices=[
            LabeledPrice(
                label=(
                    f"{amount} Stars"
                ),
                amount=amount
            )
        ],
        provider_token=""
    )
@dp.callback_query(
    F.data.startswith("buy_")
)
async def buy_stars(
    callback: types.CallbackQuery
):
    try:
        amount = int(
            callback.data.replace(
                "buy_",
                ""
            )
        )
        if amount < 1:
            raise ValueError
        await create_invoice(
            callback.from_user.id,
            amount
        )
        await callback.answer()
    except Exception as e:
        logger.exception(
            f"❌ Ошибка invoice: {e}"
        )
        await callback.answer(
            "❌ Не удалось создать оплату",
            show_alert=True
        )
# ============================================================
# PRE CHECKOUT
# ============================================================
@dp.pre_checkout_query()
async def pre_checkout(
    query: types.PreCheckoutQuery
):
    if query.currency != "XTR":
        await query.answer(
            ok=False,
            error_message=(
                "Неверная валюта."
            )
        )
        return
    if query.total_amount < 1:
        await query.answer(
            ok=False,
            error_message=(
                "Неверная сумма."
            )
        )
        return
    await query.answer(
        ok=True
    )
# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================
@dp.message(
    F.successful_payment
)
async def successful_payment(
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
    charge_id = (
        payment.telegram_payment_charge_id
    )
    provider_charge_id = (
        payment.provider_payment_charge_id
    )
    if payment.currency != "XTR":
        return
    conn = db()
    cur = conn.cursor()
    # Защита от повторного начисления
    cur.execute(
        """
        SELECT id
        FROM payments
        WHERE telegram_charge_id = ?
        """,
        (charge_id,)
    )
    if cur.fetchone():
        conn.close()
        await message.answer(
            "ℹ️ Этот платеж уже был зачислен."
        )
        return
    # Проверяем пользователя
    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    if not cur.fetchone():
        while True:
            ref_code = secrets.token_hex(8)
            cur.execute(
                """
                SELECT user_id
                FROM users
                WHERE ref_code = ?
                """,
                (ref_code,)
            )
            if not cur.fetchone():
                break
        cur.execute(
            """
            INSERT INTO users (
                user_id,
                balance,
                username,
                first_name,
                ref_code
            )
            VALUES (
                ?,
                0,
                ?,
                ?,
                ?
            )
            """,
            (
                user_id,
                message.from_user.username,
                message.from_user.first_name,
                ref_code
            )
        )
    # Начисляем Stars
    cur.execute(
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
    # Сохраняем платеж
    cur.execute(
        """
        INSERT INTO payments (
            user_id,
            telegram_charge_id,
            provider_charge_id,
            amount,
            currency
        )
        VALUES (
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            user_id,
            charge_id,
            provider_charge_id,
            amount,
            "XTR"
        )
    )
    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )
    row = cur.fetchone()
    new_balance = float(
        row["balance"]
    )
    conn.commit()
    conn.close()
    logger.info(
        (
            f"💰 PAYMENT: "
            f"user={user_id} "
            f"amount={amount} XTR "
            f"balance={new_balance}"
        )
    )
    await message.answer(
        (
            f"✅ <b>Оплата получена!</b>\n\n"
            f"⭐ Оплачено: <b>{amount}</b>\n"
            f"💰 Начислено: <b>{amount} ⭐</b>\n"
            f"💳 Баланс: "
            f"<b>{new_balance:.2f} ⭐</b>"
        )
    )
# ============================================================
# CALLBACK BALANCE
# ============================================================
@dp.callback_query(
    F.data == "balance"
)
async def balance_callback(
    callback: types.CallbackQuery
):
    balance = get_balance(
        callback.from_user.id
    )
    await callback.message.edit_text(
        (
            f"💰 <b>Ваш баланс</b>\n\n"
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
                        callback_data="back"
                    )
                ]
            ]
        )
    )
    await callback.answer()
# ============================================================
# CALLBACK PROFILE
# ============================================================
@dp.callback_query(
    F.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery
):
    user_id = (
        callback.from_user.id
    )
    balance = get_balance(
        user_id
    )
    await callback.message.edit_text(
        (
            f"👤 <b>Профиль</b>\n\n"
            f"🆔 Telegram ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>"
        ),
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
                        text="🎮 Открыть игру",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back"
                    )
                ]
            ]
        )
    )
    await callback.answer()
# ============================================================
# REFERRAL
# ============================================================
@dp.callback_query(
    F.data == "referral"
)
async def referral_callback(
    callback: types.CallbackQuery
):
    user_id = (
        callback.from_user.id
    )
    link = get_referral_link(
        user_id
    )
    count = get_referrals_count(
        user_id
    )
    await callback.message.edit_text(
        (
            f"📎 <b>Реферальная система</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"👥 Приглашено: <b>{count}</b>\n"
            f"💰 Награда: <b>10 ⭐</b>"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back"
                    )
                ]
            ]
        )
    )
    await callback.answer()
# ============================================================
# BACK
# ============================================================
@dp.callback_query(
    F.data == "back"
)
async def back_callback(
    callback: types.CallbackQuery
):
    user_id = (
        callback.from_user.id
    )
    balance = get_balance(
        user_id
    )
    await callback.message.edit_text(
        (
            f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>"
        ),
        reply_markup=main_keyboard()
    )
    await callback.answer()
# ============================================================
# HTTP API
# ============================================================
async def health(
    request: web.Request
):
    logger.info(
        f"❤️ HEALTH REQUEST "
        f"from={request.remote}"
    )
    return web.json_response(
        {
            "ok": True,
            "status": "online",
            "service": "White Bear",
            "port": PORT,
            "webapp": WEBAPP_URL
        }
    )
# ============================================================
# API BALANCE
# ============================================================
async def api_balance(
    request: web.Request
):
    logger.info(
        f"💰 BALANCE REQUEST "
        f"from={request.remote}"
    )
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )
    if not init_data:
        logger.warning(
            "❌ X-Telegram-Init-Data отсутствует"
        )
        return web.json_response(
            {
                "ok": False,
                "error": "missing_init_data"
            },
            status=401
        )
    user = validate_init_data(
        init_data
    )
    if not user:
        logger.warning(
            "❌ Telegram initData не прошел проверку"
        )
        return web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data"
            },
            status=401
        )
    user_id = int(
        user["id"]
    )
    create_user(
        user_id,
        user.get("username"),
        user.get("first_name")
    )
    balance = get_balance(
        user_id
    )
    logger.info(
        (
            f"💰 BALANCE OK "
            f"user={user_id} "
            f"balance={balance}"
        )
    )
    response = web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "balance": balance
        }
    )
    # CORS
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
    ] = "GET, OPTIONS"
    response.headers[
        "Cache-Control"
    ] = "no-store"
    return response
# ============================================================
# API USER
# ============================================================
async def api_user(
    request: web.Request
):
    logger.info(
        f"👤 USER REQUEST "
        f"from={request.remote}"
    )
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )
    user = validate_init_data(
        init_data
    )
    if not user:
        return web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data"
            },
            status=401
        )
    user_id = int(
        user["id"]
    )
    create_user(
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
                "balance": balance
            }
        }
    )
# ============================================================
# CORS OPTIONS
# ============================================================
async def options(
    request: web.Request
):
    response = web.Response(
        status=204
    )
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
        "GET, OPTIONS"
    )
    return response
# ============================================================
# ROOT
# ============================================================
async def root(
    request: web.Request
):
    return web.json_response(
        {
            "ok": True,
            "service": "White Bear API",
            "status": "online",
            "endpoints": {
                "health": "/health",
                "balance": "/api/balance",
                "user": "/api/user",
                "webhook": "/webhook"
            }
        }
    )
# ============================================================
# WEBHOOK
# ============================================================
async def webhook(
    request: web.Request
):
    try:
        data = await request.json()
        logger.info(
            "📡 WEBHOOK UPDATE RECEIVED"
        )
        update = Update.model_validate(
            data
        )
        await dp.feed_update(
            bot,
            update
        )
        return web.json_response(
            {
                "ok": True
            }
        )
    except Exception as e:
        logger.exception(
            f"❌ Webhook error: {e}"
        )
        return web.json_response(
            {
                "ok": False,
                "error": str(e)
            },
            status=500
        )
# ============================================================
# WEB SERVER
# ============================================================
async def start_web_server():
    logger.info(
        "🚀 Запускаем Web API..."
    )
    app = web.Application()
    # Root
    app.router.add_get(
        "/",
        root
    )
    # Health
    app.router.add_get(
        "/health",
        health
    )
    # Balance
    app.router.add_get(
        "/api/balance",
        api_balance
    )
    app.router.add_options(
        "/api/balance",
        options
    )
    # User
    app.router.add_get(
        "/api/user",
        api_user
    )
    app.router.add_options(
        "/api/user",
        options
    )
    # Webhook
    app.router.add_post(
        "/webhook",
        webhook
    )
    runner = web.AppRunner(
        app,
        access_log=logger
    )
    await runner.setup()
    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )
    await site.start()
    logger.info(
        "=========================================="
    )
    logger.info(
        "🌐 WEB SERVER STARTED"
    )
    logger.info(
        f"🌐 LISTENING: 0.0.0.0:{PORT}"
    )
    logger.info(
        f"🌐 PUBLIC: {BOTHOST_URL}"
    )
    logger.info(
        f"❤️ HEALTH: {HEALTH_URL}"
    )
    logger.info(
        f"💰 API: {BALANCE_URL}"
    )
    logger.info(
        f"👤 API: {USER_URL}"
    )
    logger.info(
        f"📡 WEBHOOK: {WEBHOOK_URL}"
    )
    logger.info(
        "=========================================="
    )
    # Бесконечная работа сервера
    while True:
        await asyncio.sleep(
            3600
        )
# ============================================================
# SET WEBHOOK
# ============================================================
async def setup_webhook():
    logger.info(
        f"🔗 Webhook URL: "
        f"{WEBHOOK_URL}"
    )
    try:
        webhook_info = (
            await bot.get_webhook_info()
        )
        logger.info(
            (
                f"📡 Текущий webhook: "
                f"{webhook_info.url or 'не установлен'}"
            )
        )
        if webhook_info.url != WEBHOOK_URL:
            await bot.set_webhook(
                url=WEBHOOK_URL,
                drop_pending_updates=False
            )
            logger.info(
                (
                    f"✅ Webhook установлен: "
                    f"{WEBHOOK_URL}"
                )
            )
        else:
            logger.info(
                "✅ Webhook уже установлен"
            )
    except Exception as e:
        logger.exception(
            f"❌ Ошибка webhook: {e}"
        )
# ============================================================
# BOT COMMANDS
# ============================================================
async def setup_bot():
    commands = [
        BotCommand(
            command="start",
            description="Главное меню"
        ),
        BotCommand(
            command="game",
            description="Открыть игру"
        ),
        BotCommand(
            command="balance",
            description="Баланс"
        ),
        BotCommand(
            command="profile",
            description="Профиль"
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
        "✅ Команды и WebApp-кнопка установлены"
    )
# ============================================================
# LOCAL DIAGNOSTICS
# ============================================================
async def local_diagnostics():
    await asyncio.sleep(2)
    logger.info(
        "🔎 Выполняем локальную проверку HTTP..."
    )
    urls = [
        (
            "HEALTH",
            f"http://127.0.0.1:{PORT}/health"
        ),
        (
            "ROOT",
            f"http://127.0.0.1:{PORT}/"
        )
    ]
    timeout = aiohttp.ClientTimeout(
        total=5
    )
    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:
            for name, url in urls:
                try:
                    logger.info(
                        f"🔎 Проверяем: {url}"
                    )
                    async with session.get(
                        url
                    ) as response:
                        text = await response.text()
                        logger.info(
                            (
                                f"🔎 {name} STATUS: "
                                f"{response.status}"
                            )
                        )
                        logger.info(
                            (
                                f"🔎 {name} RESPONSE: "
                                f"{text[:500]}"
                            )
                        )
                except Exception as e:
                    logger.error(
                        (
                            f"❌ {name} LOCAL ERROR: "
                            f"{e}"
                        )
                    )
    except Exception as e:
        logger.exception(
            f"❌ Диагностика HTTP: {e}"
        )
# ============================================================
# MAIN
# ============================================================
async def main():
    logger.info(
        "=========================================="
    )
    logger.info(
        "🚀 Запуск White Bear..."
    )
    logger.info(
        f"🐍 Python: {sys.version}"
    )
    logger.info(
        f"📁 BASE_DIR: {BASE_DIR}"
    )
    logger.info(
        f"📁 DB: {DB_NAME}"
    )
    logger.info(
        f"🌐 PORT: {PORT}"
    )
    logger.info(
        f"🌐 WEBAPP: {WEBAPP_URL}"
    )
    logger.info(
        f"🌐 BOTHOST: {BOTHOST_URL}"
    )
    logger.info(
        f"📡 WEBHOOK: {WEBHOOK_URL}"
    )
    logger.info(
        "=========================================="
    )
    init_db()
    await setup_bot()
    await setup_webhook()
    # HTTP API
    web_task = asyncio.create_task(
        start_web_server()
    )
    # Диагностика
    diagnostic_task = asyncio.create_task(
        local_diagnostics()
    )
    logger.info(
        "🐻‍❄️ White Bear полностью запущен!"
    )
    try:
        await asyncio.gather(
            web_task,
            diagnostic_task
        )
    finally:
        await bot.session.close()
# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )
    except KeyboardInterrupt:
        logger.info(
            "👋 Бот остановлен"
        )
    except Exception as e:
        logger.exception(
            f"❌ Критическая ошибка: {e}"
        )