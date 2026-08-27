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

# Лучше хранить токен в переменной окружения BOT_TOKEN
# на Bothost.
#
# Если хочешь временно вставить токен прямо сюда:
# BOT_TOKEN = "ТВОЙ_НОВЫЙ_ТОКЕН"

BOT_TOKEN = os.getenv("BOT_TOKEN", "8918284594:AAFLxOg1eEx4JS6z6V9wHr-t8T3Q9Qwiepg")

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

# Публичный URL Bothost
SERVER_URL = "https://bot_1787862010_6746_jix44.bothost.tech"

# Порт Bothost обычно передаёт через переменную PORT
PORT = int(os.getenv("PORT", "8080"))

DB_NAME = "users.db"


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
# DATABASE
# =====================================================

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
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
            UNIQUE(referrer_id, referred_id)
        )
    """)

    # Таблица заказов Stars
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS star_orders (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            payload TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'pending',
            telegram_charge_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT
        )
    """)

    conn.commit()
    conn.close()

    logger.info("✅ База данных инициализирована")


# =====================================================
# USERS
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


def ensure_user(
    user_id: int,
    username: str = None,
    first_name: str = None
):
    user = get_user(user_id)

    if user:
        return

    create_user(
        user_id=user_id,
        username=username,
        first_name=first_name
    )


def get_user_by_ref_code(ref_code: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return result["user_id"] if result else None


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

    return float(result["balance"])


def update_balance(user_id: int, amount: float):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE users
        SET balance = ?
        WHERE user_id = ?
        """,
        (amount, user_id)
    )

    if cursor.rowcount == 0:
        cursor.execute(
            """
            INSERT INTO users (user_id, balance)
            VALUES (?, ?)
            """,
            (user_id, amount)
        )

    conn.commit()
    conn.close()


def add_balance(user_id: int, amount: float):
    current = get_balance(user_id)
    new_balance = round(current + amount, 2)

    update_balance(user_id, new_balance)

    return new_balance


# =====================================================
# REFERRALS
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

    conn.commit()
    conn.close()

    add_balance(referrer_id, reward)
    add_balance(referred_id, reward)

    return True


def get_referrals_count(user_id: int) -> int:
    conn = get_connection()
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

    return int(result[0]) if result else 0


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# =====================================================
# TELEGRAM WEB APP INIT DATA
# =====================================================

def validate_telegram_init_data(init_data: str):
    """
    Проверяет Telegram WebApp initData.

    Возвращает данные пользователя при успешной проверке.
    Возвращает None при ошибке.
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

        auth_date = parsed.get("auth_date")

        if not auth_date:
            return None

        # Защита от слишком старого initData
        if int(time.time()) - int(auth_date) > 86400:
            logger.warning("⚠️ Telegram initData устарел")
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
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
            logger.warning("❌ Неверная подпись Telegram initData")
            return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        user_data = json.loads(user_json)

        return user_data

    except Exception as e:
        logger.error(
            f"Ошибка проверки Telegram initData: {e}"
        )

        return None


# =====================================================
# STAR ORDERS
# =====================================================

def create_star_order(
    user_id: int,
    amount: int
):
    order_id = secrets.token_hex(16)

    payload = f"deposit:{order_id}:{user_id}:{amount}"

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO star_orders (
            order_id,
            user_id,
            amount,
            payload,
            status
        )
        VALUES (?, ?, ?, ?, 'pending')
    """, (
        order_id,
        user_id,
        amount,
        payload
    ))

    conn.commit()
    conn.close()

    return order_id, payload


def get_star_order_by_payload(payload: str):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM star_orders
        WHERE payload = ?
    """, (payload,))

    result = cursor.fetchone()

    conn.close()

    return result


def mark_order_paid(
    order_id: str,
    telegram_charge_id: str
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE star_orders
        SET
            status = 'paid',
            telegram_charge_id = ?,
            paid_at = ?
        WHERE order_id = ?
          AND status = 'pending'
    """, (
        telegram_charge_id,
        datetime.utcnow().isoformat(),
        order_id
    ))

    changed = cursor.rowcount

    conn.commit()
    conn.close()

    return changed > 0


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
                        f"Пользователь {first_name} "
                        f"перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили +10 ⭐\n"
                        f"📊 Всего приглашено: "
                        f"{get_referrals_count(invited_by)}"
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
        f"🆔 Ваш ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>\n\n"
        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"🎮 Нажмите кнопку ниже.",
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
        f"💰 Баланс: {balance:.2f} ⭐",
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

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    await message.answer(
        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n"
        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}"
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
        f"Имя: {message.from_user.first_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: {balance:.2f} ⭐\n"
        f"👥 Приглашено: {ref_count}\n\n"
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
        "/help — Помощь\n"
        "/paysupport — Поддержка по оплате"
    )


# =====================================================
# PAYMENT SUPPORT
# =====================================================

@dp.message(Command("paysupport"))
async def pay_support(message: Message):

    await message.answer(
        "💳 <b>Поддержка по оплате</b>\n\n"
        "Если оплата прошла, но Stars не были "
        "начислены, обратитесь к администратору "
        "бота и укажите ваш Telegram ID."
    )


# =====================================================
# CALLBACK: BALANCE
# =====================================================

@dp.callback_query(F.data == "balance")
async def balance_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

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


# =====================================================
# CALLBACK: PROFILE
# =====================================================

@dp.callback_query(F.data == "profile")
async def profile_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {callback.from_user.first_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: {balance:.2f} ⭐\n"
        f"👥 Приглашено: {ref_count}\n\n"
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

@dp.callback_query(F.data == "referral")
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

@dp.callback_query(F.data == "back_to_start")
async def back_to_start_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await callback.message.edit_text(
        f"🐻‍❄️ <b>White Bear DROP</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n\n"
        f"📎 <code>{ref_link}</code>",
        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def process_pre_checkout(
    pre_checkout_query: types.PreCheckoutQuery
):

    try:

        payload = pre_checkout_query.invoice_payload

        order = get_star_order_by_payload(
            payload
        )

        if not order:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Заказ не найден."
            )

            return

        if order["status"] != "pending":

            await pre_checkout_query.answer(
                ok=False,
                error_message="Этот заказ уже обработан."
            )

            return

        expected_amount = int(order["amount"])

        if (
            pre_checkout_query.currency != "XTR"
            or
            int(pre_checkout_query.total_amount)
            != expected_amount
        ):

            logger.error(
                "❌ Несовпадение суммы оплаты: "
                f"expected={expected_amount}, "
                f"received={pre_checkout_query.total_amount}"
            )

            await pre_checkout_query.answer(
                ok=False,
                error_message="Неверная сумма заказа."
            )

            return

        if (
            pre_checkout_query.from_user.id
            != int(order["user_id"])
        ):

            await pre_checkout_query.answer(
                ok=False,
                error_message="Заказ принадлежит другому пользователю."
            )

            return

        await pre_checkout_query.answer(
            ok=True
        )

        logger.info(
            f"✅ PreCheckout подтверждён: "
            f"user={order['user_id']} "
            f"amount={order['amount']}"
        )

    except Exception as e:

        logger.exception(
            f"Ошибка pre_checkout: {e}"
        )

        try:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Ошибка обработки платежа."
            )

        except Exception:
            pass


# =====================================================
# SUCCESSFUL PAYMENT
# =====================================================

@dp.message(F.successful_payment)
async def successful_payment(
    message: Message
):

    payment = message.successful_payment

    user_id = message.from_user.id

    payload = payment.invoice_payload

    logger.info(
        f"💰 Получена успешная оплата: "
        f"user={user_id}, "
        f"amount={payment.total_amount}, "
        f"currency={payment.currency}"
    )

    order = get_star_order_by_payload(
        payload
    )

    if not order:

        logger.error(
            f"❌ Не найден заказ для payload: {payload}"
        )

        await message.answer(
            "⚠️ Оплата получена, "
            "но заказ не найден.\n"
            "Обратитесь в поддержку."
        )

        return

    # Проверяем пользователя
    if int(order["user_id"]) != user_id:

        logger.error(
            "❌ Пользователь не совпадает с заказом"
        )

        return

    # Проверяем валюту
    if payment.currency != "XTR":

        logger.error(
            "❌ Оплата не в XTR"
        )

        return

    # Проверяем сумму
    if int(payment.total_amount) != int(order["amount"]):

        logger.error(
            "❌ Сумма оплаты не совпадает"
        )

        return

    # Помечаем заказ как оплаченный.
    # Если он уже был обработан, повторно баланс не начисляем.
    success = mark_order_paid(
        order["order_id"],
        payment.telegram_payment_charge_id
    )

    if not success:

        logger.warning(
            f"⚠️ Заказ уже был обработан: "
            f"{order['order_id']}"
        )

        return

    # Начисляем Stars
    new_balance = add_balance(
        user_id,
        int(payment.total_amount)
    )

    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"💰 Зачислено: "
        f"<b>+{payment.total_amount} ⭐</b>\n"
        f"💳 Новый баланс: "
        f"<b>{new_balance:.2f} ⭐</b>"
    )

    logger.info(
        f"🎉 Баланс пополнен: "
        f"user={user_id}, "
        f"+{payment.total_amount} ⭐, "
        f"balance={new_balance}"
    )


# =====================================================
# HTTP API
# =====================================================

async def health_handler(request):
    return web.json_response({
        "ok": True,
        "service": "White Bear API",
        "status": "online"
    })


def cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )
    return response


async def options_handler(request):

    response = web.Response()

    return cors_headers(response)


def get_init_data_from_request(request):
    return (
        request.headers.get("X-Telegram-Init-Data")
        or request.headers.get("x-telegram-init-data")
        or request.query.get("initData")
    )


async def api_me_handler(request):

    init_data = get_init_data_from_request(request)

    user_data = validate_telegram_init_data(
        init_data
    )

    if not user_data:

        response = web.json_response(
            {
                "ok": False,
                "error": "INVALID_TELEGRAM_INIT_DATA",
                "message": "Откройте сайт через Telegram."
            },
            status=401
        )

        return cors_headers(response)

    user_id = int(user_data["id"])

    ensure_user(
        user_id,
        user_data.get("username"),
        user_data.get("first_name")
    )

    balance = get_balance(user_id)

    response = web.json_response({
        "ok": True,
        "user": {
            "id": user_id,
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name")
        },
        "balance": round(balance, 2),
        "referrals": get_referrals_count(user_id)
    })

    return cors_headers(response)


# =====================================================
# CREATE INVOICE
# =====================================================

async def create_invoice_handler(request):

    try:

        init_data = get_init_data_from_request(request)

        user_data = validate_telegram_init_data(
            init_data
        )

        if not user_data:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "INVALID_TELEGRAM_INIT_DATA",
                    "message": "Откройте сайт через Telegram."
                },
                status=401
            )

            return cors_headers(response)

        user_id = int(user_data["id"])

        body = await request.json()

        amount = int(body.get("amount", 0))

        # Ограничения пополнения
        if amount < 1:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "INVALID_AMOUNT",
                    "message": "Минимальная сумма — 1 ⭐."
                },
                status=400
            )

            return cors_headers(response)

        if amount > 10000:

            response = web.json_response(
                {
                    "ok": False,
                    "error": "MAX_AMOUNT",
                    "message": "Максимальная сумма — 10000 ⭐."
                },
                status=400
            )

            return cors_headers(response)

        ensure_user(
            user_id,
            user_data.get("username"),
            user_data.get("first_name")
        )

        # Создаём уникальный заказ
        order_id, payload = create_star_order(
            user_id,
            amount
        )

        # Создаём invoice link Telegram Stars
        invoice_link = await bot.create_invoice_link(
            title="Пополнение White Bear",
            description=f"Пополнение баланса на {amount} ⭐",
            payload=payload,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=f"White Bear +{amount} ⭐",
                    amount=amount
                )
            ],
            provider_token=""
        )

        logger.info(
            f"🧾 Создан invoice: "
            f"order={order_id}, "
            f"user={user_id}, "
            f"amount={amount}"
        )

        response = web.json_response({
            "ok": True,
            "invoice_url": invoice_link,
            "order_id": order_id,
            "amount": amount
        })

        return cors_headers(response)

    except Exception as e:

        logger.exception(
            f"❌ Ошибка создания invoice: {e}"
        )

        response = web.json_response(
            {
                "ok": False,
                "error": "CREATE_INVOICE_ERROR",
                "message": str(e)
            },
            status=500
        )

        return cors_headers(response)


# =====================================================
# API TEST
# =====================================================

async def api_info_handler(request):

    response = web.json_response({
        "ok": True,
        "api": "White Bear",
        "version": "1.0",
        "telegram": "Stars XTR",
        "endpoints": [
            "/health",
            "/api/me",
            "/api/create-invoice"
        ]
    })

    return cors_headers(response)


# =====================================================
# HTTP SERVER
# =====================================================

async def start_http_server():

    app = web.Application()

    # CORS preflight
    app.router.add_route(
        "OPTIONS",
        "/health",
        options_handler
    )

    app.router.add_route(
        "OPTIONS",
        "/api/me",
        options_handler
    )

    app.router.add_route(
        "OPTIONS",
        "/api/create-invoice",
        options_handler
    )

    # Health
    app.router.add_get(
        "/health",
        health_handler
    )

    # API info
    app.router.add_get(
        "/",
        api_info_handler
    )

    # User information
    app.router.add_get(
        "/api/me",
        api_me_handler
    )

    # Invoice
    app.router.add_post(
        "/api/create-invoice",
        create_invoice_handler
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT
    )

    await site.start()

    logger.info(
        f"🌐 API запущен на 0.0.0.0:{PORT}"
    )

    logger.info(
        f"🌐 SERVER_URL: {SERVER_URL}"
    )

    logger.info(
        f"🔎 Health: {SERVER_URL}/health"
    )

    while True:
        await asyncio.sleep(3600)


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
        BotCommand(
            command="paysupport",
            description="Поддержка по оплате"
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
# START BOT
# =====================================================

async def start_bot():

    logger.info(
        "🤖 Запуск Telegram бота..."
    )

    await set_commands_and_menu()

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


# =====================================================
# MAIN
# =====================================================

async def main():

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE"
    ):

        logger.error(
            "❌ BOT_TOKEN не установлен!"
        )

        logger.error(
            "Установите переменную окружения BOT_TOKEN "
            "на Bothost."
        )

        return

    init_db()

    logger.info(
        "🚀 White Bear запускается..."
    )

    logger.info(
        f"🌐 WebApp: {WEBAPP_URL}"
    )

    logger.info(
        f"🌐 API: {SERVER_URL}"
    )

    await asyncio.gather(
        start_bot(),
        start_http_server()
    )


# =====================================================
# RUN
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