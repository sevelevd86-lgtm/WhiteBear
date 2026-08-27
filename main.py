import asyncio
import logging
import sys
import sqlite3
import secrets
import json
import os
import time
import hashlib
import hmac
from urllib.parse import parse_qsl
from datetime import datetime

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
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
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties


# =====================================================
# КОНФИГУРАЦИЯ
# =====================================================

# НЕ ВСТАВЛЯЙ BOT TOKEN В ПУБЛИЧНЫЙ GITHUB.
# На Bothost создай переменную окружения:
#
# BOT_TOKEN = твой токен
#
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

SERVER_URL = (
    "https://bot_1787862010_6746_jix44.bothost.tech"
)

# Порт Bothost обычно передаёт через переменную PORT.
PORT = int(os.getenv("PORT", "8080"))

# Сколько времени считаем initData актуальным.
# 86400 = 24 часа.
WEBAPP_AUTH_MAX_AGE = 24 * 60 * 60


# =====================================================
# ПРОВЕРКА КОНФИГУРАЦИИ
# =====================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. "
        "Добавь переменную окружения BOT_TOKEN на Bothost."
    )


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

DB_NAME = "users.db"


def get_db():
    conn = sqlite3.connect(
        DB_NAME,
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
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
            FOREIGN KEY (referrer_id) REFERENCES users(user_id),
            FOREIGN KEY (referred_id) REFERENCES users(user_id)
        )
        """
    )

    # Таблица платежей.
    #
    # telegram_payment_charge_id делаем UNIQUE,
    # чтобы один успешный платёж невозможно было
    # начислить повторно.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            payload TEXT NOT NULL,
            telegram_payment_charge_id TEXT UNIQUE NOT NULL,
            provider_payment_charge_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()
    conn.close()

    logger.info("✅ База данных инициализирована")


# =====================================================
# ПОЛЬЗОВАТЕЛИ
# =====================================================

def get_user(user_id: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    )

    result = cursor.fetchone()

    conn.close()

    return result


def create_user(
    user_id: int,
    username: str = None,
    first_name: str = None,
    invited_by: int = None,
):
    conn = get_db()
    cursor = conn.cursor()

    # Проверяем, существует ли пользователь.
    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,),
    )

    existing = cursor.fetchone()

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
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        return None

    ref_code = secrets.token_hex(8)

    while True:
        cursor.execute(
            "SELECT ref_code FROM users WHERE ref_code = ?",
            (ref_code,),
        )

        if not cursor.fetchone():
            break

        ref_code = secrets.token_hex(8)

    cursor.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            first_name,
            ref_code,
            invited_by
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            first_name,
            ref_code,
            invited_by,
        ),
    )

    conn.commit()
    conn.close()

    return ref_code


def ensure_user(
    user_id: int,
    username: str = None,
    first_name: str = None,
):
    user = get_user(user_id)

    if user:
        create_user(
            user_id,
            username,
            first_name,
        )
        return

    create_user(
        user_id,
        username,
        first_name,
    )


def get_user_by_ref_code(ref_code: str):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,),
    )

    result = cursor.fetchone()

    conn.close()

    return result["user_id"] if result else None


def get_balance(user_id: int) -> float:
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,),
    )

    result = cursor.fetchone()

    conn.close()

    if result:
        return float(result["balance"])

    return 0.0


def update_balance(user_id: int, amount: float):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET balance = ?
        WHERE user_id = ?
        """,
        (
            amount,
            user_id,
        ),
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
                amount,
            ),
        )

    conn.commit()
    conn.close()


# =====================================================
# РЕФЕРАЛЫ
# =====================================================

def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0,
):
    if referrer_id == referred_id:
        return False

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM referrals
        WHERE referrer_id = ?
          AND referred_id = ?
        """,
        (
            referrer_id,
            referred_id,
        ),
    )

    if cursor.fetchone():
        conn.close()
        return False

    try:
        conn.execute("BEGIN IMMEDIATE")

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
                reward,
            ),
        )

        cursor.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                referrer_id,
            ),
        )

        cursor.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                referred_id,
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.close()

    return True


def get_referrals_count(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (user_id,),
    )

    result = cursor.fetchone()

    conn.close()

    return int(result[0]) if result else 0


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# =====================================================
# TELEGRAM WEB APP INIT DATA
# =====================================================

def validate_telegram_init_data(init_data: str):
    """
    Проверяет Telegram WebApp initData.

    HTML должен передавать:
        Telegram.WebApp.initData

    Возвращает данные пользователя или None.
    """

    if not init_data:
        return None

    try:
        parsed = dict(parse_qsl(
            init_data,
            keep_blank_values=True,
        ))

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        # Проверка подписи Telegram Web App.
        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            logger.warning(
                "❌ Неверная подпись Telegram WebApp"
            )
            return None

        # Проверяем возраст auth_date.
        auth_date = int(parsed.get("auth_date", "0"))

        if auth_date <= 0:
            return None

        if time.time() - auth_date > WEBAPP_AUTH_MAX_AGE:
            logger.warning(
                "❌ Telegram initData устарел"
            )
            return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        user_data = json.loads(user_json)

        telegram_id = int(user_data["id"])

        return {
            "id": telegram_id,
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name"),
            "last_name": user_data.get("last_name"),
        }

    except Exception as e:
        logger.error(
            f"Ошибка проверки initData: {e}"
        )
        return None


def get_webapp_user(request: web.Request):
    """
    Получаем пользователя из заголовка:
        X-Telegram-Init-Data

    Также поддерживаем:
        Authorization: tma <initData>
    """

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        "",
    ).strip()

    if not init_data:
        authorization = request.headers.get(
            "Authorization",
            "",
        ).strip()

        if authorization.startswith("tma "):
            init_data = authorization[4:].strip()

    return validate_telegram_init_data(init_data)


# =====================================================
# TELEGRAM BOT
# =====================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher()


# =====================================================
# КЛАВИАТУРА
# =====================================================

def get_main_keyboard():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🎮 Открыть игры",
        web_app=WebAppInfo(
            url=WEBAPP_URL
        ),
    )

    builder.button(
        text="💰 Баланс",
        callback_data="balance",
    )

    builder.button(
        text="👤 Профиль",
        callback_data="profile",
    )

    builder.button(
        text="📎 Реферальная ссылка",
        callback_data="referral",
    )

    builder.adjust(1, 2, 1)

    return builder.as_markup()


# =====================================================
# /START
# =====================================================

@dp.message(Command("start"))
async def start_command(
    message: Message,
):
    user_id = message.from_user.id

    username = message.from_user.username

    first_name = (
        message.from_user.first_name
        or "Пользователь"
    )

    args = message.text.split()

    invited_by = None

    user = get_user(user_id)

    if not user:

        if (
            len(args) > 1
            and args[1].startswith("ref_")
        ):
            ref_code = args[1][4:]

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
            invited_by,
        )

        if invited_by:

            success = add_referral(
                invited_by,
                user_id,
                10.0,
            )

            if success:
                try:
                    await bot.send_message(
                        invited_by,
                        (
                            f"🎉 <b>Новый реферал!</b>\n\n"
                            f"Пользователь "
                            f"{first_name} "
                            f"перешёл по вашей ссылке.\n\n"
                            f"💰 Вы получили "
                            f"<b>+10 ⭐</b>\n"
                            f"📊 Всего приглашено: "
                            f"<b>{get_referrals_count(invited_by)}</b>"
                        ),
                    )

                except Exception as e:
                    logger.error(
                        "Не удалось уведомить "
                        f"реферера: {e}"
                    )

    else:
        ensure_user(
            user_id,
            username,
            first_name,
        )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await message.answer(
        (
            f"🐻‍❄️ "
            f"<b>Добро пожаловать в DROP, "
            f"{first_name}!</b>\n\n"
            f"🆔 Ваш ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>\n"
            f"👥 Приглашено друзей: "
            f"<b>{ref_count}</b>\n\n"
            f"📎 <b>Реферальная ссылка:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"🎮 Нажмите кнопку ниже, "
            f"чтобы открыть игру."
        ),
        reply_markup=get_main_keyboard(),
    )


# =====================================================
# /GAME
# =====================================================

@dp.message(Command("game"))
async def game_command(
    message: Message,
):
    user_id = message.from_user.id

    balance = get_balance(user_id)

    await message.answer(
        (
            f"🎮 <b>Открываем игры...</b>\n"
            f"💰 Баланс: "
            f"{balance:.2f} ⭐"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игры",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        ),
                    )
                ]
            ]
        ),
    )


# =====================================================
# /BALANCE
# =====================================================

@dp.message(Command("balance"))
async def balance_command(
    message: Message,
):
    user_id = message.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    await message.answer(
        (
            f"💰 <b>Ваш баланс:</b> "
            f"{balance:.2f} ⭐\n"
            f"👥 <b>Приглашено друзей:</b> "
            f"{ref_count}"
        )
    )


# =====================================================
# /PROFILE
# =====================================================

@dp.message(Command("profile"))
async def profile_command(
    message: Message,
):
    user_id = message.from_user.id

    first_name = (
        message.from_user.first_name
        or "Пользователь"
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await message.answer(
        (
            f"👤 <b>Профиль</b>\n\n"
            f"Имя: {first_name}\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"{balance:.2f} ⭐\n"
            f"👥 Приглашено: "
            f"{ref_count}\n\n"
            f"📎 <b>Реферальная ссылка:</b>\n"
            f"<code>{ref_link}</code>"
        )
    )


# =====================================================
# /HELP
# =====================================================

@dp.message(Command("help"))
async def help_command(
    message: Message,
):
    await message.answer(
        (
            "📖 <b>Помощь</b>\n\n"
            "/start — Главное меню\n"
            "/game — Открыть игры\n"
            "/balance — Баланс\n"
            "/profile — Профиль\n"
            "/help — Помощь\n\n"
            "💰 Играйте и выигрывайте!"
        )
    )


# =====================================================
# CALLBACK: BALANCE
# =====================================================

@dp.callback_query(
    lambda c: c.data == "balance"
)
async def balance_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(
        (
            f"💰 <b>Ваш баланс:</b> "
            f"{balance:.2f} ⭐\n"
            f"👥 <b>Приглашено друзей:</b> "
            f"{ref_count}"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# =====================================================
# CALLBACK: PROFILE
# =====================================================

@dp.callback_query(
    lambda c: c.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    first_name = (
        callback.from_user.first_name
        or "Пользователь"
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await callback.message.edit_text(
        (
            f"👤 <b>Профиль</b>\n\n"
            f"Имя: {first_name}\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"{balance:.2f} ⭐\n"
            f"👥 Приглашено: "
            f"{ref_count}\n\n"
            f"📎 <b>Реферальная ссылка:</b>\n"
            f"<code>{ref_link}</code>"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# =====================================================
# CALLBACK: REFERRAL
# =====================================================

@dp.callback_query(
    lambda c: c.data == "referral"
)
async def referral_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    ref_link = get_referral_link(
        user_id
    )

    await callback.message.edit_text(
        (
            f"📎 <b>Ваша реферальная ссылка:</b>\n\n"
            f"<code>{ref_link}</code>\n\n"
            f"💡 Приглашайте друзей "
            f"и получайте по <b>10 ⭐</b> "
            f"за каждого."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back_to_start",
                    )
                ]
            ]
        ),
    )

    await callback.answer()


# =====================================================
# CALLBACK: BACK
# =====================================================

@dp.callback_query(
    lambda c: c.data == "back_to_start"
)
async def back_to_start_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    first_name = (
        callback.from_user.first_name
        or "Пользователь"
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await callback.message.edit_text(
        (
            f"🐻‍❄️ "
            f"<b>Добро пожаловать в DROP, "
            f"{first_name}!</b>\n\n"
            f"🆔 Ваш ID: "
            f"<code>{user_id}</code>\n"
            f"💰 Баланс: "
            f"<b>{balance:.2f} ⭐</b>\n"
            f"👥 Приглашено друзей: "
            f"<b>{ref_count}</b>\n\n"
            f"📎 <b>Реферальная ссылка:</b>\n"
            f"<code>{ref_link}</code>"
        ),
        reply_markup=get_main_keyboard(),
    )

    await callback.answer()


# =====================================================
# TELEGRAM STARS
# =====================================================

def create_payment_payload(
    user_id: int,
    amount: int,
) -> str:
    """
    Payload хранит:
        wb|user_id|amount|random

    Пример:
        wb|123456789|100|a8f91c
    """

    random_part = secrets.token_hex(4)

    return (
        f"wb|{user_id}|"
        f"{amount}|"
        f"{random_part}"
    )


def parse_payment_payload(payload: str):
    try:
        parts = payload.split("|")

        if len(parts) != 4:
            return None

        if parts[0] != "wb":
            return None

        user_id = int(parts[1])

        amount = int(parts[2])

        if user_id <= 0:
            return None

        if amount <= 0:
            return None

        return {
            "user_id": user_id,
            "amount": amount,
        }

    except Exception:
        return None


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: types.PreCheckoutQuery,
):
    logger.info(
        "💳 PRE-CHECKOUT: "
        f"user={query.from_user.id}, "
        f"currency={query.currency}, "
        f"amount={query.total_amount}, "
        f"payload={query.invoice_payload}"
    )

    payload_data = parse_payment_payload(
        query.invoice_payload
    )

    # Проверяем payload.
    if not payload_data:
        await query.answer(
            ok=False,
            error_message=(
                "Ошибка платежа. "
                "Попробуйте создать оплату заново."
            ),
        )
        return

    # Только Stars.
    if query.currency != "XTR":
        await query.answer(
            ok=False,
            error_message=(
                "Этот платёж должен "
                "быть в Telegram Stars."
            ),
        )
        return

    # Проверяем пользователя.
    if (
        payload_data["user_id"]
        != query.from_user.id
    ):
        await query.answer(
            ok=False,
            error_message=(
                "Пользователь платежа "
                "не совпадает."
            ),
        )
        return

    # Проверяем сумму.
    if (
        payload_data["amount"]
        != query.total_amount
    ):
        await query.answer(
            ok=False,
            error_message=(
                "Сумма платежа изменилась. "
                "Создайте новый платёж."
            ),
        )
        return

    # Ограничение.
    if query.total_amount < 10:
        await query.answer(
            ok=False,
            error_message=(
                "Минимальное пополнение — 10 ⭐."
            ),
        )
        return

    if query.total_amount > 1000:
        await query.answer(
            ok=False,
            error_message=(
                "Максимальное пополнение — 1000 ⭐."
            ),
        )
        return

    # Всё хорошо.
    await query.answer(
        ok=True
    )

    logger.info(
        "✅ PRE-CHECKOUT подтверждён"
    )


# =====================================================
# SUCCESSFUL PAYMENT
# =====================================================

@dp.message(
    F.successful_payment
)
async def successful_payment_handler(
    message: Message,
):
    payment = message.successful_payment

    if not payment:
        return

    user_id = message.from_user.id

    logger.info(
        "💰 SUCCESSFUL PAYMENT: "
        f"user={user_id}, "
        f"currency={payment.currency}, "
        f"amount={payment.total_amount}, "
        f"charge_id="
        f"{payment.telegram_payment_charge_id}"
    )

    # Только XTR.
    if payment.currency != "XTR":
        logger.error(
            "Получен платёж с другой валютой."
        )
        return

    payload_data = parse_payment_payload(
        payment.invoice_payload
    )

    if not payload_data:
        logger.error(
            "❌ Неверный payment payload."
        )
        return

    # Проверяем ID пользователя.
    if payload_data["user_id"] != user_id:
        logger.error(
            "❌ ID пользователя в payload "
            "не совпадает."
        )
        return

    # Проверяем сумму.
    if (
        payload_data["amount"]
        != payment.total_amount
    ):
        logger.error(
            "❌ Сумма в payload "
            "не совпадает."
        )
        return

    charge_id = (
        payment.telegram_payment_charge_id
    )

    conn = get_db()

    try:
        # Блокируем базу на время транзакции.
        conn.execute("BEGIN IMMEDIATE")

        cursor = conn.cursor()

        # Проверяем, не был ли платёж
        # уже начислен.
        cursor.execute(
            """
            SELECT id
            FROM payments
            WHERE telegram_payment_charge_id = ?
            """,
            (charge_id,),
        )

        existing_payment = cursor.fetchone()

        if existing_payment:
            conn.rollback()

            logger.warning(
                "⚠️ Повторный платёж "
                f"{charge_id}"
            )

            await message.answer(
                "ℹ️ Этот платёж уже был зачислен."
            )

            return

        amount = payment.total_amount

        # Проверяем пользователя.
        cursor.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        user_row = cursor.fetchone()

        if not user_row:
            cursor.execute(
                """
                INSERT INTO users (
                    user_id,
                    balance,
                    username,
                    first_name
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    float(amount),
                    message.from_user.username,
                    message.from_user.first_name,
                ),
            )

        else:
            old_balance = float(
                user_row["balance"]
            )

            new_balance = (
                old_balance + amount
            )

            cursor.execute(
                """
                UPDATE users
                SET balance = ?
                WHERE user_id = ?
                """,
                (
                    new_balance,
                    user_id,
                ),
            )

        # Записываем платёж.
        cursor.execute(
            """
            INSERT INTO payments (
                user_id,
                amount,
                currency,
                payload,
                telegram_payment_charge_id,
                provider_payment_charge_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                amount,
                payment.currency,
                payment.invoice_payload,
                charge_id,
                payment.provider_payment_charge_id,
            ),
        )

        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()

        logger.warning(
            "⚠️ Платёж уже существует: "
            f"{charge_id}"
        )

        await message.answer(
            "ℹ️ Этот платёж уже был зачислен."
        )

        return

    except Exception as e:
        conn.rollback()

        logger.exception(
            "❌ Ошибка начисления платежа: "
            f"{e}"
        )

        await message.answer(
            "❌ Платёж получен, но произошла "
            "ошибка начисления. "
            "Обратитесь в поддержку."
        )

        return

    finally:
        conn.close()

    new_balance = get_balance(user_id)

    logger.info(
        "✅ НАЧИСЛЕНО: "
        f"user={user_id}, "
        f"+{amount} ⭐, "
        f"balance={new_balance}"
    )

    await message.answer(
        (
            f"🎉 <b>Оплата прошла успешно!</b>\n\n"
            f"💰 Зачислено: "
            f"<b>+{amount} ⭐</b>\n"
            f"💳 Ваш баланс: "
            f"<b>{new_balance:.2f} ⭐</b>"
        )
    )


# =====================================================
# HTTP: CORS
# =====================================================

ALLOWED_ORIGIN = (
    "https://sevelevd86-lgtm.github.io"
)


def add_cors_headers(
    response: web.StreamResponse,
):
    response.headers["Access-Control-Allow-Origin"] = (
        ALLOWED_ORIGIN
    )

    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )

    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data, Authorization"
    )

    response.headers["Access-Control-Max-Age"] = "86400"

    return response


async def options_handler(
    request: web.Request,
):
    response = web.Response(
        status=204
    )

    return add_cors_headers(response)


# =====================================================
# HTTP: HEALTH
# =====================================================

async def health_handler(
    request: web.Request,
):
    response = web.json_response(
        {
            "status": "ok",
            "service": "white-bear-bot",
            "telegram": "connected",
            "payments": "telegram_stars_xtr",
            "server": SERVER_URL,
        }
    )

    return add_cors_headers(response)


# =====================================================
# HTTP: INFO
# =====================================================

async def info_handler(
    request: web.Request,
):
    response = web.json_response(
        {
            "ok": True,
            "bot": BOT_USERNAME,
            "webapp": WEBAPP_URL,
            "payment_currency": "XTR",
            "min_deposit": 10,
            "max_deposit": 1000,
        }
    )

    return add_cors_headers(response)


# =====================================================
# HTTP: GET BALANCE
# =====================================================

async def api_balance_handler(
    request: web.Request,
):
    user = get_webapp_user(request)

    if not user:
        response = web.json_response(
            {
                "ok": False,
                "error": (
                    "Telegram authorization required"
                ),
            },
            status=401,
        )

        return add_cors_headers(response)

    user_id = user["id"]

    ensure_user(
        user_id,
        user.get("username"),
        user.get("first_name"),
    )

    balance = get_balance(user_id)

    response = web.json_response(
        {
            "ok": True,
            "telegram_id": user_id,
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "balance": round(balance, 2),
        }
    )

    return add_cors_headers(response)


# =====================================================
# HTTP: CREATE INVOICE
# =====================================================

async def api_create_invoice_handler(
    request: web.Request,
):
    user = get_webapp_user(request)

    if not user:
        response = web.json_response(
            {
                "ok": False,
                "error": (
                    "Telegram authorization required"
                ),
            },
            status=401,
        )

        return add_cors_headers(response)

    user_id = user["id"]

    # Читаем JSON.
    try:
        data = await request.json()

    except Exception:
        response = web.json_response(
            {
                "ok": False,
                "error": "Invalid JSON",
            },
            status=400,
        )

        return add_cors_headers(response)

    try:
        amount = int(
            data.get("amount", 0)
        )

    except Exception:
        amount = 0

    # Проверяем сумму.
    if amount < 10:
        response = web.json_response(
            {
                "ok": False,
                "error": (
                    "Минимальное пополнение — 10 ⭐"
                ),
            },
            status=400,
        )

        return add_cors_headers(response)

    if amount > 1000:
        response = web.json_response(
            {
                "ok": False,
                "error": (
                    "Максимальное пополнение — 1000 ⭐"
                ),
            },
            status=400,
        )

        return add_cors_headers(response)

    ensure_user(
        user_id,
        user.get("username"),
        user.get("first_name"),
    )

    payload = create_payment_payload(
        user_id,
        amount,
    )

    try:
        # Для Telegram Stars:
        #
        # currency = XTR
        # provider_token не нужен.
        #
        invoice_link = (
            await bot.create_invoice_link(
                title="Пополнение баланса",
                description=(
                    f"Пополнение баланса "
                    f"White Bear на {amount} ⭐"
                ),
                payload=payload,
                currency="XTR",
                prices=[
                    LabeledPrice(
                        label=f"{amount} ⭐",
                        amount=amount,
                    )
                ],
            )
        )

    except Exception as e:
        logger.exception(
            "❌ Не удалось создать invoice: "
            f"{e}"
        )

        response = web.json_response(
            {
                "ok": False,
                "error": (
                    "Не удалось создать оплату "
                    "Telegram Stars."
                ),
            },
            status=500,
        )

        return add_cors_headers(response)

    logger.info(
        "🧾 Создан invoice: "
        f"user={user_id}, "
        f"amount={amount}"
    )

    response = web.json_response(
        {
            "ok": True,
            "invoice_url": invoice_link,
            "telegram_id": user_id,
            "amount": amount,
            "currency": "XTR",
        }
    )

    return add_cors_headers(response)


# =====================================================
# HTTP: USER
# =====================================================

async def api_user_handler(
    request: web.Request,
):
    user = get_webapp_user(request)

    if not user:
        response = web.json_response(
            {
                "ok": False,
                "error": (
                    "Telegram authorization required"
                ),
            },
            status=401,
        )

        return add_cors_headers(response)

    user_id = user["id"]

    ensure_user(
        user_id,
        user.get("username"),
        user.get("first_name"),
    )

    balance = get_balance(user_id)

    response = web.json_response(
        {
            "ok": True,
            "user": {
                "id": user_id,
                "username": user.get("username"),
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "balance": round(
                    balance,
                    2,
                ),
            },
        }
    )

    return add_cors_headers(response)


# =====================================================
# HTTP APP
# =====================================================

def create_http_app():
    app = web.Application()

    # OPTIONS.
    app.router.add_route(
        "OPTIONS",
        "/health",
        options_handler,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/info",
        options_handler,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance",
        options_handler,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/user",
        options_handler,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/create-invoice",
        options_handler,
    )

    # GET.
    app.router.add_get(
        "/health",
        health_handler,
    )

    app.router.add_get(
        "/api/info",
        info_handler,
    )

    app.router.add_get(
        "/api/balance",
        api_balance_handler,
    )

    app.router.add_get(
        "/api/user",
        api_user_handler,
    )

    # POST.
    app.router.add_post(
        "/api/create-invoice",
        api_create_invoice_handler,
    )

    return app


# =====================================================
# ЗАПУСК HTTP СЕРВЕРА
# =====================================================

async def start_http_server():
    app = create_http_app()

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT,
    )

    await site.start()

    logger.info(
        "🌐 HTTP API запущен"
    )

    logger.info(
        f"🌐 PORT: {PORT}"
    )

    logger.info(
        f"🌐 SERVER URL: {SERVER_URL}"
    )

    logger.info(
        "🌐 HEALTH: "
        f"{SERVER_URL}/health"
    )

    logger.info(
        "🌐 CREATE INVOICE: "
        f"{SERVER_URL}/api/create-invoice"
    )

    return runner


# =====================================================
# КОМАНДЫ И MENU BUTTON
# =====================================================

async def set_commands_and_menu():
    commands = [
        BotCommand(
            command="start",
            description="Главное меню",
        ),
        BotCommand(
            command="game",
            description="Открыть игры",
        ),
        BotCommand(
            command="balance",
            description="Показать баланс",
        ),
        BotCommand(
            command="profile",
            description="Профиль",
        ),
        BotCommand(
            command="help",
            description="Помощь",
        ),
    ]

    await bot.set_my_commands(
        commands,
        scope=BotCommandScopeDefault(),
    )

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🎮 Играть",
            web_app=WebAppInfo(
                url=WEBAPP_URL
            ),
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

    # База.
    init_db()

    # Команды.
    await set_commands_and_menu()

    # HTTP API.
    http_runner = await start_http_server()

    try:
        logger.info(
            "🤖 Telegram bot запускается..."
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )

    finally:
        logger.info(
            "🛑 Остановка HTTP сервера..."
        )

        await http_runner.cleanup()

        await bot.session.close()


# =====================================================
# ENTRY POINT
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