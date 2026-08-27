# ============================================================
# WHITE BEAR DROP — BOT + TELEGRAM STARS + WEBAPP API
# ============================================================

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
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, types
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
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================

# НЕ вставляй сюда токен, который уже публиковался в чате.
# После перевыпуска токена через @BotFather вставь новый.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8918284594:AAFLxOg1eEx4JS6z6V9wHr-t8T3Q9Qwiepg")

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

# Публичный адрес Bothost
SERVER_URL = "https://bot_1787862010_6746_jix44.bothost.tech"

# Порт.
# Bothost обычно передаёт PORT через переменную окружения.
PORT = int(os.getenv("PORT", "8080"))

DB_NAME = "users.db"


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

logger = logging.getLogger("white_bear")


# ============================================================
# БОТ
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_db()
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
    # Нужна для защиты от повторного начисления.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_payment_charge_id TEXT UNIQUE,
            provider_payment_charge_id TEXT,
            user_id INTEGER,
            amount INTEGER,
            currency TEXT,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Индекс для быстрого поиска платежа
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_payments_charge
        ON payments(telegram_payment_charge_id)
    """)

    conn.commit()
    conn.close()

    logger.info("✅ База данных инициализирована")


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

def get_user(user_id: int):
    conn = get_db()
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
    conn = get_db()
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
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return result[0] if result else None


def get_balance(user_id: int) -> float:
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    if result:
        return float(result[0])

    return 0.0


def update_balance(user_id: int, amount: float):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (amount, user_id)
    )

    if cursor.rowcount == 0:
        cursor.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)",
            (user_id, amount)
        )

    conn.commit()
    conn.close()


def add_balance(user_id: int, amount: float):
    current = get_balance(user_id)
    new_balance = current + amount

    new_balance = round(new_balance, 2)

    update_balance(user_id, new_balance)

    return new_balance


# ============================================================
# РЕФЕРАЛЫ
# ============================================================

def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):
    conn = get_db()
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

    conn.commit()
    conn.close()

    add_balance(referrer_id, reward)
    add_balance(referred_id, reward)

    return True


def get_referrals_count(user_id: int) -> int:
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result[0] if result else 0


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# ============================================================
# ОСНОВНАЯ КЛАВИАТУРА
# ============================================================

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


# ============================================================
# TELEGRAM WEBAPP — ПРОВЕРКА INIT DATA
# ============================================================

def validate_telegram_webapp_data(init_data: str):
    """
    Проверяет Telegram WebApp initData.

    Важно:
    нельзя просто доверять telegram_id,
    который присылает браузер.

    Telegram WebApp передаёт подписанные данные,
    и мы проверяем их здесь через токен бота.
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

        # Telegram требует data-check-string
        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
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
                "❌ Неверная подпись Telegram WebApp"
            )
            return None

        # Проверяем свежесть авторизации.
        # 24 часа.
        auth_date = int(parsed.get("auth_date", "0"))

        if auth_date <= 0:
            return None

        if time.time() - auth_date > 86400:
            logger.warning(
                "❌ Telegram WebApp initData устарел"
            )
            return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        user_data = json.loads(user_json)

        user_id = user_data.get("id")

        if not user_id:
            return None

        return user_data

    except Exception as e:
        logger.error(
            f"Ошибка проверки Telegram WebApp: {e}"
        )

        return None


# ============================================================
# API — CORS
# ============================================================

def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )

    return response


async def options_handler(request):
    response = web.Response(status=204)

    return add_cors_headers(response)


# ============================================================
# API — ПРОВЕРКА ПОЛЬЗОВАТЕЛЯ
# ============================================================

async def api_me(request):
    try:

        init_data = request.headers.get(
            "X-Telegram-Init-Data",
            ""
        )

        user_data = validate_telegram_webapp_data(
            init_data
        )

        if not user_data:
            response = web.json_response(
                {
                    "ok": False,
                    "error": "Telegram authorization required"
                },
                status=401
            )

            return add_cors_headers(response)

        user_id = int(user_data["id"])

        # Если пользователя ещё нет — создаём.
        if not get_user(user_id):

            create_user(
                user_id=user_id,
                username=user_data.get("username"),
                first_name=user_data.get("first_name")
            )

        balance = get_balance(user_id)

        response = web.json_response({
            "ok": True,
            "user": {
                "id": user_id,
                "username": user_data.get("username"),
                "first_name": user_data.get("first_name")
            },
            "balance": round(balance, 2)
        })

        return add_cors_headers(response)

    except Exception as e:

        logger.exception(
            "Ошибка /api/me"
        )

        response = web.json_response(
            {
                "ok": False,
                "error": "Internal server error"
            },
            status=500
        )

        return add_cors_headers(response)


# ============================================================
# API — БАЛАНС
# ============================================================

async def api_balance(request):

    try:

        init_data = request.headers.get(
            "X-Telegram-Init-Data",
            ""
        )

        user_data = validate_telegram_webapp_data(
            init_data
        )

        if not user_data:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "Telegram authorization required"
                },
                status=401
            )

            return add_cors_headers(response)

        user_id = int(user_data["id"])

        if not get_user(user_id):

            create_user(
                user_id=user_id,
                username=user_data.get("username"),
                first_name=user_data.get("first_name")
            )

        balance = get_balance(user_id)

        response = web.json_response({
            "ok": True,
            "telegram_id": user_id,
            "balance": round(balance, 2)
        })

        return add_cors_headers(response)

    except Exception as e:

        logger.exception(
            "Ошибка /api/balance"
        )

        response = web.json_response(
            {
                "ok": False,
                "error": "Internal server error"
            },
            status=500
        )

        return add_cors_headers(response)


# ============================================================
# API — СОЗДАНИЕ INVOICE
# ============================================================

async def api_create_invoice(request):

    try:

        # ----------------------------------------------------
        # Проверяем Telegram WebApp
        # ----------------------------------------------------

        init_data = request.headers.get(
            "X-Telegram-Init-Data",
            ""
        )

        user_data = validate_telegram_webapp_data(
            init_data
        )

        if not user_data:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "Откройте игру через Telegram"
                },
                status=401
            )

            return add_cors_headers(response)

        user_id = int(user_data["id"])

        # ----------------------------------------------------
        # Получаем JSON
        # ----------------------------------------------------

        try:
            data = await request.json()
        except Exception:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "Invalid JSON"
                },
                status=400
            )

            return add_cors_headers(response)

        amount = data.get("amount")

        # ----------------------------------------------------
        # Проверяем сумму
        # ----------------------------------------------------

        try:
            amount = int(amount)
        except (TypeError, ValueError):

            response = web.json_response(
                {
                    "ok": False,
                    "error": "Некорректная сумма"
                },
                status=400
            )

            return add_cors_headers(response)

        # Минимум 10 Stars
        if amount < 10:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "Минимальное пополнение — 10 ⭐"
                },
                status=400
            )

            return add_cors_headers(response)

        # Максимум 1000 Stars за один платёж
        if amount > 1000:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "Максимальное пополнение — 1000 ⭐"
                },
                status=400
            )

            return add_cors_headers(response)

        # ----------------------------------------------------
        # Создаём пользователя при необходимости
        # ----------------------------------------------------

        if not get_user(user_id):

            create_user(
                user_id=user_id,
                username=user_data.get("username"),
                first_name=user_data.get("first_name")
            )

        # ----------------------------------------------------
        # Уникальный payload
        # ----------------------------------------------------

        payload = (
            f"deposit:{user_id}:"
            f"{amount}:"
            f"{secrets.token_hex(12)}"
        )

        # ----------------------------------------------------
        # Создаём Telegram Stars invoice
        # ----------------------------------------------------

        invoice_link = await bot.create_invoice_link(
            title="Пополнение баланса",
            description=(
                f"Пополнение баланса White Bear DROP "
                f"на {amount} ⭐"
            ),
            payload=payload,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=f"{amount} ⭐",
                    amount=amount
                )
            ]
        )

        logger.info(
            f"🧾 Создан invoice: "
            f"user={user_id}, "
            f"amount={amount}"
        )

        response = web.json_response({
            "ok": True,
            "invoice_url": invoice_link,
            "amount": amount
        })

        return add_cors_headers(response)

    except Exception as e:

        logger.exception(
            "Ошибка создания invoice"
        )

        response = web.json_response(
            {
                "ok": False,
                "error": str(e)
            },
            status=500
        )

        return add_cors_headers(response)


# ============================================================
# УСПЕШНАЯ ОПЛАТА TELEGRAM STARS
# ============================================================

@dp.message(
    lambda message: message.successful_payment is not None
)
async def successful_payment_handler(message: Message):

    payment = message.successful_payment

    user_id = message.from_user.id

    # --------------------------------------------------------
    # Проверяем, не был ли платёж уже обработан
    # --------------------------------------------------------

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
    """, (
        payment.telegram_payment_charge_id,
    ))

    already_exists = cursor.fetchone()

    if already_exists:

        conn.close()

        logger.warning(
            f"⚠️ Повторная обработка платежа: "
            f"{payment.telegram_payment_charge_id}"
        )

        return

    # --------------------------------------------------------
    # Сохраняем платёж
    # --------------------------------------------------------

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
        payment.telegram_payment_charge_id,
        payment.provider_payment_charge_id,
        user_id,
        payment.total_amount,
        payment.currency,
        payment.invoice_payload
    ))

    conn.commit()
    conn.close()

    # --------------------------------------------------------
    # Начисляем Stars
    # --------------------------------------------------------

    amount = int(payment.total_amount)

    new_balance = add_balance(
        user_id,
        amount
    )

    logger.info(
        f"💰 ОПЛАТА ПОЛУЧЕНА: "
        f"user={user_id}, "
        f"amount={amount} XTR, "
        f"balance={new_balance}"
    )

    # --------------------------------------------------------
    # Сообщение пользователю
    # --------------------------------------------------------

    await message.answer(
        f"✅ <b>Оплата успешно получена!</b>\n\n"
        f"💰 На баланс начислено: "
        f"<b>+{amount} ⭐</b>\n"
        f"💳 Текущий баланс: "
        f"<b>{new_balance:.2f} ⭐</b>"
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_command(message: Message):

    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

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
                        f"{first_name} "
                        f"перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили "
                        f"<b>+10 ⭐</b>\n"
                        f"📊 Всего приглашено: "
                        f"<b>{get_referrals_count(invited_by)}</b>"
                    )

                except Exception as e:

                    logger.error(
                        f"Не удалось уведомить "
                        f"реферера: {e}"
                    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await message.answer(
        f"🐻‍❄️ <b>Добро пожаловать "
        f"в DROP, {first_name}!</b>\n\n"
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
        f"чтобы открыть DROP.",
        reply_markup=get_main_keyboard()
    )


# ============================================================
# GAME
# ============================================================

@dp.message(Command("game"))
async def game_command(message: Message):

    user_id = message.from_user.id

    balance = get_balance(user_id)

    await message.answer(
        f"🎮 <b>Открываем DROP...</b>\n\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть DROP",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ]
            ]
        )
    )


# ============================================================
# BALANCE
# ============================================================

@dp.message(Command("balance"))
async def balance_command(message: Message):

    user_id = message.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    await message.answer(
        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n"
        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}"
    )


# ============================================================
# PROFILE
# ============================================================

@dp.message(Command("profile"))
async def profile_command(message: Message):

    user_id = message.from_user.id

    first_name = message.from_user.first_name

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await message.answer(
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


# ============================================================
# HELP
# ============================================================

@dp.message(Command("help"))
async def help_command(message: Message):

    await message.answer(
        "📖 <b>Помощь</b>\n\n"
        "/start — Главное меню\n"
        "/game — Открыть DROP\n"
        "/balance — Показать баланс\n"
        "/profile — Профиль\n"
        "/help — Эта справка\n\n"
        "💰 Пополняйте баланс "
        "через Telegram Stars."
    )


# ============================================================
# CALLBACK — BALANCE
# ============================================================

@dp.callback_query(
    lambda c: c.data == "balance"
)
async def balance_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(
        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n"
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


# ============================================================
# CALLBACK — PROFILE
# ============================================================

@dp.callback_query(
    lambda c: c.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    first_name = callback.from_user.first_name

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {first_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"{balance:.2f} ⭐\n"
        f"👥 Приглашено: "
        f"{ref_count}\n\n"
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


# ============================================================
# CALLBACK — REFERRAL
# ============================================================

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

    await callback.message.edit_text(
        f"📎 <b>Ваша реферальная ссылка:</b>\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"💡 Приглашайте друзей "
        f"и получайте по 10 ⭐ за каждого!",
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


# ============================================================
# CALLBACK — BACK
# ============================================================

@dp.callback_query(
    lambda c: c.data == "back_to_start"
)
async def back_to_start_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    first_name = callback.from_user.first_name

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await callback.message.edit_text(
        f"🐻‍❄️ <b>Добро пожаловать "
        f"в DROP, {first_name}!</b>\n\n"
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


# ============================================================
# WEBAPP DATA
# ============================================================

@dp.message(
    lambda msg: msg.web_app_data is not None
)
async def web_app_data_handler(
    message: Message
):

    try:

        data = json.loads(
            message.web_app_data.data
        )

        user_id = message.from_user.id

        action = data.get("action")

        if action == "getBalance":

            balance = get_balance(
                user_id
            )

            await message.answer(
                f"{balance:.2f}"
            )

        elif action == "getReferralLink":

            ref_link = get_referral_link(
                user_id
            )

            await message.answer(
                ref_link
            )

    except json.JSONDecodeError:

        await message.answer(
            "❌ Ошибка обработки данных"
        )

    except Exception as e:

        logger.exception(
            f"Ошибка WebApp data: {e}"
        )


# ============================================================
# КОМАНДЫ И MENU BUTTON
# ============================================================

async def set_commands_and_menu():

    commands = [
        BotCommand(
            command="start",
            description="Главное меню"
        ),
        BotCommand(
            command="game",
            description="Открыть DROP"
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


# ============================================================
# HTTP SERVER
# ============================================================

async def health_handler(request):

    response = web.json_response({
        "ok": True,
        "service": "White Bear DROP",
        "telegram": True,
        "stars": True,
        "api": True
    })

    return add_cors_headers(response)


def create_web_app():

    app = web.Application()

    # --------------------------------------------------------
    # OPTIONS / CORS
    # --------------------------------------------------------

    app.router.add_route(
        "OPTIONS",
        "/api/create-invoice",
        options_handler
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance",
        options_handler
    )

    app.router.add_route(
        "OPTIONS",
        "/api/me",
        options_handler
    )

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    app.router.add_post(
        "/api/create-invoice",
        api_create_invoice
    )

    app.router.add_get(
        "/api/balance",
        api_balance
    )

    app.router.add_get(
        "/api/me",
        api_me
    )

    # --------------------------------------------------------
    # Проверка сервера
    # --------------------------------------------------------

    app.router.add_get(
        "/",
        health_handler
    )

    app.router.add_get(
        "/health",
        health_handler
    )

    return app


# ============================================================
# WEB SERVER RUNNER
# ============================================================

async def start_web_server():

    app = create_web_app()

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT
    )

    await site.start()

    logger.info(
        f"🌐 API SERVER ЗАПУЩЕН "
        f"на порту {PORT}"
    )

    logger.info(
        f"🌐 API: "
        f"{SERVER_URL}/api/create-invoice"
    )

    logger.info(
        f"❤️ Health: "
        f"{SERVER_URL}/health"
    )

    return runner


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "🚀 Запуск White Bear DROP..."
    )

    # --------------------------------------------------------
    # Проверяем токен
    # --------------------------------------------------------

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "ВСТАВЬ_НОВЫЙ_ТОКЕН_БОТА"
    ):

        logger.error(
            "❌ Не указан BOT_TOKEN!"
        )

        logger.error(
            "Добавь новый токен бота "
            "в переменную BOT_TOKEN "
            "или Environment Variables."
        )

        return

    # --------------------------------------------------------
    # База
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # Проверяем соединение с Telegram
    # --------------------------------------------------------

    try:

        me = await bot.get_me()

        logger.info(
            f"✅ Бот подключён: "
            f"@{me.username}"
        )

    except Exception as e:

        logger.exception(
            f"❌ Не удалось подключить бота: {e}"
        )

        return

    # --------------------------------------------------------
    # Команды
    # --------------------------------------------------------

    await set_commands_and_menu()

    # --------------------------------------------------------
    # HTTP API
    # --------------------------------------------------------

    await start_web_server()

    logger.info(
        "========================================"
    )

    logger.info(
        "🐻‍❄️ WHITE BEAR DROP ЗАПУЩЕН"
    )

    logger.info(
        f"🌐 {SERVER_URL}"
    )

    logger.info(
        f"💳 {SERVER_URL}/api/create-invoice"
    )

    logger.info(
        "⭐ TELEGRAM STARS XTR: ENABLED"
    )

    logger.info(
        "========================================"
    )

    # --------------------------------------------------------
    # Telegram polling
    # --------------------------------------------------------

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


# ============================================================
# START
# ============================================================

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