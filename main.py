import asyncio
import logging
import sys
import sqlite3
import secrets
import os
import json
import time
import hashlib
import hmac
from urllib.parse import parse_qsl

from aiohttp import web

from aiogram import Bot, Dispatcher, types
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

# НЕ вставляй сюда токен, который уже публиковался.
# На Bothost создай переменную окружения BOT_TOKEN.
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

BOT_USERNAME = "White_Bear_ROBOT"

# HTML на GitHub
WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

# Публичный URL API на Bothost
SERVER_URL = "https://bot_1787862010_6746_jix44.bothost.tech"

# Порт.
# Bothost обычно передаёт его через переменную PORT.
PORT = int(os.getenv("PORT", "8080"))

# =====================================================
# ПРОВЕРКА КОНФИГУРАЦИИ
# =====================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден. Создай переменную окружения BOT_TOKEN "
        "на Bothost и вставь туда новый токен бота."
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


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward REAL DEFAULT 10.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referrer_id) REFERENCES users(user_id),
            FOREIGN KEY (referred_id) REFERENCES users(user_id)
        )
    """)

    # Таблица платежей.
    # Нужна, чтобы один и тот же successful_payment
    # нельзя было начислить повторно.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_payment_charge_id TEXT UNIQUE,
            provider_payment_charge_id TEXT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # Индекс для быстрого поиска платежей
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_payments_user_id
        ON payments(user_id)
    """)

    conn.commit()
    conn.close()

    logger.info("✅ База данных инициализирована")


# =====================================================
# ПОЛЬЗОВАТЕЛИ
# =====================================================

def get_user(user_id: int):
    conn = get_connection()
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
    conn = get_connection()
    cursor = conn.cursor()

    ref_code = secrets.token_hex(8)

    while True:
        cursor.execute(
            "SELECT ref_code FROM users WHERE ref_code = ?",
            (ref_code,)
        )

        if not cursor.fetchone():
            break

        ref_code = secrets.token_hex(8)

    cursor.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            ref_code,
            invited_by
        )
        VALUES (?, ?, ?, ?, ?)
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


def get_user_by_ref_code(ref_code: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return result[0] if result else None


def get_balance(user_id: int) -> float:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    if not result:
        return 0.0

    return float(result[0])


def update_balance(user_id: int, amount: float):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (amount, user_id)
    )

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


def add_balance(user_id: int, amount: float):
    conn = get_connection()
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

    conn = get_connection()
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
    conn = get_connection()
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

    return int(result[0]) if result else 0


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# =====================================================
# ПРОВЕРКА TELEGRAM WEB APP INIT DATA
# =====================================================

def validate_telegram_init_data(init_data: str):
    """
    Проверяет Telegram.WebApp.initData.

    Возвращает Telegram user_id, если данные настоящие.
    Возвращает None, если подпись неправильная.
    """

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

        # Проверяем срок действия initData.
        auth_date = parsed.get("auth_date")

        if not auth_date:
            return None

        try:
            auth_timestamp = int(auth_date)
        except ValueError:
            return None

        # 24 часа
        if int(time.time()) - auth_timestamp > 86400:
            logger.warning("⚠️ Telegram initData устарел")
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )

        # Для Telegram Web Apps:
        # secret_key = HMAC_SHA256("WebAppData", BOT_TOKEN)
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
            logger.warning("⚠️ Неверная подпись Telegram initData")
            return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        user_data = json.loads(user_json)

        telegram_id = user_data.get("id")

        if not telegram_id:
            return None

        return int(telegram_id)

    except Exception as e:
        logger.exception(
            f"Ошибка проверки Telegram initData: {e}"
        )
        return None


# =====================================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# =====================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =====================================================
# КЛАВИАТУРА
# =====================================================

def get_main_keyboard():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🎮 Открыть игры",
        web_app=WebAppInfo(url=WEBAPP_URL)
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
# START
# =====================================================

@dp.message(Command("start"))
async def start_command(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    args = message.text.split()

    invited_by = None

    user = get_user(user_id)

    if not user:

        if len(args) > 1 and args[1].startswith("ref_"):

            ref_code = args[1][4:]

            referrer_id = get_user_by_ref_code(ref_code)

            if referrer_id and referrer_id != user_id:
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
                        f"{first_name} перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили "
                        f"<b>+10 ⭐</b>\n\n"
                        f"📊 Всего приглашено: "
                        f"<b>{get_referrals_count(invited_by)}</b>"
                    )

                except Exception as e:
                    logger.error(
                        f"Не удалось уведомить реферера: {e}"
                    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

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

        f"💡 Приглашайте друзей и получайте "
        f"по 10 ⭐ за каждого!\n\n"

        f"🎮 Нажмите кнопку ниже, "
        f"чтобы открыть игры.",

        reply_markup=get_main_keyboard()
    )


# =====================================================
# GAME
# =====================================================

@dp.message(Command("game"))
async def game_command(message: Message):
    user_id = message.from_user.id

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

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    await message.answer(
        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n\n"

        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}"
    )


# =====================================================
# PROFILE
# =====================================================

@dp.message(Command("profile"))
async def profile_command(message: Message):
    user_id = message.from_user.id

    first_name = message.from_user.first_name

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await message.answer(
        f"👤 <b>Профиль</b>\n\n"

        f"Имя: {first_name}\n"

        f"🆔 ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>"
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

        "💰 Играйте и выигрывайте!"
    )


# =====================================================
# CALLBACK: BALANCE
# =====================================================

@dp.callback_query(
    lambda c: c.data == "balance"
)
async def balance_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

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

@dp.callback_query(
    lambda c: c.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    first_name = callback.from_user.first_name

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"

        f"Имя: {first_name}\n"

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
                        text="🔙 Назад",
                        callback_data="back_to_start"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# =====================================================
# CALLBACK: REFERRAL
# =====================================================

@dp.callback_query(
    lambda c: c.data == "referral"
)
async def referral_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(
        f"📎 <b>Ваша реферальная ссылка:</b>\n\n"

        f"<code>{ref_link}</code>\n\n"

        f"💡 Приглашайте друзей и получайте "
        f"по 10 ⭐ за каждого!",

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

@dp.callback_query(
    lambda c: c.data == "back_to_start"
)
async def back_to_start_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    first_name = callback.from_user.first_name

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(
        f"🐻‍❄️ <b>Добро пожаловать в DROP, "
        f"{first_name}!</b>\n\n"

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
# TELEGRAM STARS
# =====================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    pre_checkout_query: types.PreCheckoutQuery
):
    """
    Telegram вызывает этот обработчик перед завершением оплаты.

    Мы проверяем payload.
    """

    payload = pre_checkout_query.invoice_payload

    if not payload.startswith("deposit:"):
        await pre_checkout_query.answer(
            ok=False,
            error_message="Неверный платёж."
        )
        return

    try:
        user_id = int(
            payload.split(":")[1]
        )

        if user_id != pre_checkout_query.from_user.id:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Пользователь не совпадает."
            )
            return

    except Exception:
        await pre_checkout_query.answer(
            ok=False,
            error_message="Ошибка платежа."
        )
        return

    await pre_checkout_query.answer(
        ok=True
    )


# =====================================================
# УСПЕШНАЯ ОПЛАТА STARS
# =====================================================

@dp.message(
    lambda message: message.successful_payment is not None
)
async def successful_payment_handler(
    message: Message
):

    payment = message.successful_payment

    user_id = message.from_user.id

    amount = float(payment.total_amount)

    currency = payment.currency

    telegram_charge_id = (
        payment.telegram_payment_charge_id
    )

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    payload = payment.invoice_payload

    logger.info(
        f"💰 Успешная оплата: "
        f"user={user_id}, "
        f"amount={amount}, "
        f"currency={currency}, "
        f"charge={telegram_charge_id}"
    )

    # Stars должны иметь валюту XTR
    if currency != "XTR":
        logger.error(
            f"❌ Неожиданная валюта платежа: {currency}"
        )
        return

    # Проверяем payload
    try:
        if not payload.startswith("deposit:"):
            logger.error(
                f"❌ Неверный payload: {payload}"
            )
            return

        payload_user_id = int(
            payload.split(":")[1]
        )

        if payload_user_id != user_id:
            logger.error(
                "❌ ID пользователя в payload "
                "не совпадает с отправителем"
            )
            return

    except Exception as e:
        logger.error(
            f"❌ Ошибка payload: {e}"
        )
        return

    # -------------------------------------------------
    # Защита от повторного начисления
    # -------------------------------------------------

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
    """, (
        telegram_charge_id,
    ))

    already_exists = cursor.fetchone()

    if already_exists:
        conn.close()

        logger.warning(
            f"⚠️ Платёж уже был обработан: "
            f"{telegram_charge_id}"
        )

        return

    # -------------------------------------------------
    # Сохраняем платёж
    # -------------------------------------------------

    cursor.execute("""
        INSERT INTO payments (
            telegram_payment_charge_id,
            provider_payment_charge_id,
            user_id,
            amount,
            currency,
            payload
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        telegram_charge_id,
        provider_charge_id,
        user_id,
        amount,
        currency,
        payload
    ))

    # -------------------------------------------------
    # Начисляем баланс
    # -------------------------------------------------

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

    # Получаем новый баланс
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    new_balance = (
        float(result[0])
        if result
        else amount
    )

    conn.close()

    logger.info(
        f"✅ Начислено {amount} ⭐ "
        f"пользователю {user_id}. "
        f"Новый баланс: {new_balance}"
    )

    await message.answer(
        f"✅ <b>Оплата успешно получена!</b>\n\n"
        f"💰 Зачислено: <b>+{amount:.0f} ⭐</b>\n"
        f"💳 Баланс: <b>{new_balance:.2f} ⭐</b>"
    )


# =====================================================
# HTTP API
# =====================================================

async def api_health(request: web.Request):
    """
    Проверка сервера.
    """

    return web.json_response({
        "ok": True,
        "service": "White Bear API"
    })


# =====================================================
# API: ПОЛУЧИТЬ БАЛАНС
# =====================================================

async def api_balance(request: web.Request):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user_id = validate_telegram_init_data(
        init_data
    )

    if not user_id:
        return web.json_response(
            {
                "ok": False,
                "error": "INVALID_TELEGRAM_DATA"
            },
            status=401
        )

    # Если пользователя ещё нет
    if not get_user(user_id):
        return web.json_response({
            "ok": True,
            "user_id": user_id,
            "balance": 0.0
        })

    balance = get_balance(user_id)

    return web.json_response({
        "ok": True,
        "user_id": user_id,
        "balance": round(balance, 2)
    })


# =====================================================
# API: СОЗДАТЬ INVOICE
# =====================================================

async def api_create_invoice(
    request: web.Request
):

    # Telegram initData
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user_id = validate_telegram_init_data(
        init_data
    )

    if not user_id:
        return web.json_response(
            {
                "ok": False,
                "error": "INVALID_TELEGRAM_DATA"
            },
            status=401
        )

    # -------------------------------------------------
    # Читаем JSON
    # -------------------------------------------------

    try:
        data = await request.json()

    except Exception:
        return web.json_response(
            {
                "ok": False,
                "error": "INVALID_JSON"
            },
            status=400
        )

    # -------------------------------------------------
    # Сумма
    # -------------------------------------------------

    try:
        amount = int(data.get("amount", 0))

    except Exception:
        amount = 0

    # Минимум
    if amount < 1:
        return web.json_response(
            {
                "ok": False,
                "error": "INVALID_AMOUNT"
            },
            status=400
        )

    # Максимум за одну оплату.
    # При необходимости можешь изменить.
    if amount > 10000:
        return web.json_response(
            {
                "ok": False,
                "error": "MAX_AMOUNT_10000"
            },
            status=400
        )

    # -------------------------------------------------
    # Убеждаемся, что пользователь существует
    # -------------------------------------------------

    user = get_user(user_id)

    if not user:

        create_user(
            user_id=user_id
        )

    # -------------------------------------------------
    # Создаём payload
    # -------------------------------------------------

    payload = f"deposit:{user_id}:{secrets.token_hex(8)}"

    # -------------------------------------------------
    # Создаём Telegram Stars invoice
    # -------------------------------------------------

    try:

        invoice_link = await bot.create_invoice_link(
            title="Пополнение баланса",
            description=(
                f"Пополнение баланса White Bear "
                f"на {amount} ⭐"
            ),
            payload=payload,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label="White Bear ⭐",
                    amount=amount
                )
            ]
        )

    except Exception as e:

        logger.exception(
            f"❌ Ошибка создания invoice: {e}"
        )

        return web.json_response(
            {
                "ok": False,
                "error": "INVOICE_CREATE_FAILED"
            },
            status=500
        )

    logger.info(
        f"🧾 Создан invoice: "
        f"user={user_id}, "
        f"amount={amount}"
    )

    return web.json_response({
        "ok": True,
        "invoice_url": invoice_link,
        "amount": amount
    })


# =====================================================
# CORS
# =====================================================

@web.middleware
async def cors_middleware(
    request: web.Request,
    handler
):

    # OPTIONS
    if request.method == "OPTIONS":

        response = web.Response(
            status=204
        )

    else:

        try:
            response = await handler(request)

        except web.HTTPException as e:
            response = e

    response.headers["Access-Control-Allow-Origin"] = (
        "https://sevelevd86-lgtm.github.io"
    )

    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data"
    )

    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )

    response.headers["Access-Control-Allow-Credentials"] = (
        "true"
    )

    return response


# =====================================================
# HTTP APP
# =====================================================

def create_web_app():

    app = web.Application(
        middlewares=[
            cors_middleware
        ]
    )

    # Проверка сервера
    app.router.add_get(
        "/",
        api_health
    )

    app.router.add_get(
        "/api/health",
        api_health
    )

    # Баланс
    app.router.add_get(
        "/api/balance",
        api_balance
    )

    # Создание платежа
    app.router.add_post(
        "/api/create-invoice",
        api_create_invoice
    )

    return app


# =====================================================
# WEB SERVER
# =====================================================

async def start_web_server():

    app = create_web_app()

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT
    )

    await site.start()

    logger.info(
        f"🌐 HTTP API запущен на порту {PORT}"
    )

    logger.info(
        f"🌐 SERVER_URL: {SERVER_URL}"
    )

    logger.info(
        f"🔗 API: {SERVER_URL}/api/health"
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
        ),
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

    logger.info(
        f"🌐 WebApp: {WEBAPP_URL}"
    )

    logger.info(
        f"🌐 Server: {SERVER_URL}"
    )

    logger.info(
        f"🌐 Port: {PORT}"
    )

    init_db()

    await set_commands_and_menu()

    # Запускаем HTTP API
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

        logger.info(
            "🛑 Останавливаем HTTP сервер..."
        )

        await web_runner.cleanup()

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