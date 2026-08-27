import asyncio
import logging
import sys
import sqlite3
import secrets
import json
import os
import hashlib
import hmac
import urllib.parse
from datetime import datetime
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
# ОБЯЗАТЕЛЬНО:
# На Bothost создай переменную окружения BOT_TOKEN
# и вставь туда токен своего бота.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = "White_Bear_ROBOT"
WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"
# Bothost обычно передаёт порт через PORT.
PORT = int(os.getenv("PORT", "8080"))
HOST = "0.0.0.0"
DB_NAME = "users.db"
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. "
        "Создай переменную окружения BOT_TOKEN на Bothost "
        "и вставь туда токен Telegram-бота."
    )
# =====================================================
# ЛОГИРОВАНИЕ
# =====================================================
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)
# =====================================================
# БАЗА ДАННЫХ
# =====================================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
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
    # Нужна для защиты от повторного начисления Stars.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payload TEXT UNIQUE NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            telegram_charge_id TEXT UNIQUE,
            provider_charge_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")
def get_user(user_id: int):
    conn = sqlite3.connect(DB_NAME)
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
    conn = sqlite3.connect(DB_NAME)
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
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
        """, (
            username,
            first_name,
            user_id
        ))
        conn.commit()
        conn.close()
        return
    create_user(
        user_id=user_id,
        username=username,
        first_name=first_name
    )
def get_user_by_ref_code(ref_code: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None
def get_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return float(result[0]) if result else 0.0
def update_balance(user_id: int, amount: float):
    conn = sqlite3.connect(DB_NAME)
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
    new_balance = round(
        current + amount,
        2
    )
    update_balance(
        user_id,
        new_balance
    )
    return new_balance
def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):
    conn = sqlite3.connect(DB_NAME)
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
            referred_id
        )
    )
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
    add_balance(
        referrer_id,
        reward
    )
    add_balance(
        referred_id,
        reward
    )
    return True
def get_referrals_count(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
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
    return result[0] if result else 0
def get_referral_link(user_id: int) -> str:
    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )
# =====================================================
# ПЛАТЕЖИ
# =====================================================
def create_payment(
    user_id: int,
    amount: int
):
    payment_id = secrets.token_hex(16)
    payload = f"stars_{user_id}_{payment_id}"
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO payments (
            user_id,
            payload,
            amount,
            status
        )
        VALUES (?, ?, ?, 'pending')
    """, (
        user_id,
        payload,
        amount
    ))
    conn.commit()
    conn.close()
    return payload
def get_payment_by_payload(payload: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            id,
            user_id,
            payload,
            amount,
            status,
            telegram_charge_id,
            provider_charge_id,
            created_at,
            paid_at
        FROM payments
        WHERE payload = ?
    """, (payload,))
    result = cursor.fetchone()
    conn.close()
    return result
def mark_payment_paid(
    payload: str,
    telegram_charge_id: str,
    provider_charge_id: str
):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            user_id,
            amount,
            status
        FROM payments
        WHERE payload = ?
    """, (payload,))
    payment = cursor.fetchone()
    if not payment:
        conn.close()
        return False, "payment_not_found"
    user_id = int(payment[0])
    amount = int(payment[1])
    status = payment[2]
    # Защита от повторной обработки
    if status == "paid":
        conn.close()
        return False, "already_paid"
    cursor.execute("""
        UPDATE payments
        SET status = 'paid',
            telegram_charge_id = ?,
            provider_charge_id = ?,
            paid_at = ?
        WHERE payload = ?
          AND status = 'pending'
    """, (
        telegram_charge_id,
        provider_charge_id,
        datetime.utcnow().isoformat(),
        payload
    ))
    if cursor.rowcount != 1:
        conn.close()
        return False, "payment_update_failed"
    conn.commit()
    conn.close()
    # Начисляем Stars только после успешной оплаты.
    add_balance(
        user_id,
        amount
    )
    return True, "success"
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
# КЛАВИАТУРА
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
async def start_command(message: Message) -> None:
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
                        f"💰 Вы получили +10 звёзд!\n"
                        f"📊 Всего приглашено: "
                        f"{get_referrals_count(invited_by)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Не удалось уведомить реферера: {e}"
                    )
    else:
        ensure_user(
            user_id,
            username,
            first_name
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
        f"🎮 Нажмите кнопку ниже, "
        f"чтобы открыть приложение.",
        reply_markup=get_main_keyboard()
    )
# =====================================================
# GAME
# =====================================================
@dp.message(Command("game"))
async def game_command(message: Message) -> None:
    user_id = message.from_user.id
    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )
    balance = get_balance(user_id)
    await message.answer(
        f"🎮 <b>Открываем приложение...</b>\n"
        f"💰 Баланс: {balance:.2f} ⭐",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть приложение",
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
async def balance_command(message: Message) -> None:
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
async def profile_command(message: Message) -> None:
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    ensure_user(
        user_id,
        message.from_user.username,
        first_name
    )
    balance = get_balance(user_id)
    ref_count = get_referrals_count(user_id)
    ref_link = get_referral_link(user_id)
    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {first_name}\n"
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
async def help_command(message: Message) -> None:
    await message.answer(
        "📖 <b>Помощь</b>\n\n"
        "/start — Главное меню\n"
        "/game — Открыть приложение\n"
        "/balance — Показать баланс\n"
        "/profile — Профиль\n"
        "/help — Эта справка"
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
    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )
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
@dp.callback_query(
    lambda c: c.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery
):
    user_id = callback.from_user.id
    first_name = callback.from_user.first_name
    ensure_user(
        user_id,
        callback.from_user.username,
        first_name
    )
    balance = get_balance(user_id)
    ref_count = get_referrals_count(user_id)
    ref_link = get_referral_link(user_id)
    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {first_name}\n"
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
        f"🐻‍❄️ <b>White Bear DROP</b>\n\n"
        f"👤 {first_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Рефералов: <b>{ref_count}</b>\n\n"
        f"📎 <code>{ref_link}</code>",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()
# =====================================================
# TELEGRAM WEB APP DATA
# =====================================================
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
        ensure_user(
            user_id,
            message.from_user.username,
            message.from_user.first_name
        )
        action = data.get("action")
        if action == "getBalance":
            balance = get_balance(user_id)
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
        logger.error(
            f"WebApp error: {e}"
        )
# =====================================================
# PRE-CHECKOUT QUERY
# =====================================================
@dp.pre_checkout_query()
async def pre_checkout_query_handler(
    query: types.PreCheckoutQuery
):
    try:
        payload = query.invoice_payload
        payment = get_payment_by_payload(
            payload
        )
        if not payment:
            await query.answer(
                ok=False,
                error_message="Платёж не найден."
            )
            return
        user_id = int(payment[1])
        amount = int(payment[3])
        if user_id != query.from_user.id:
            await query.answer(
                ok=False,
                error_message="Этот платёж принадлежит другому пользователю."
            )
            return
        if amount != query.total_amount:
            await query.answer(
                ok=False,
                error_message="Сумма платежа не совпадает."
            )
            return
        if payment[4] == "paid":
            await query.answer(
                ok=False,
                error_message="Этот платёж уже обработан."
            )
            return
        await query.answer(
            ok=True
        )
    except Exception as e:
        logger.error(
            f"PreCheckout error: {e}"
        )
        await query.answer(
            ok=False,
            error_message="Ошибка проверки платежа."
        )
# =====================================================
# УСПЕШНАЯ ОПЛАТА TELEGRAM STARS
# =====================================================
@dp.message(
    lambda message:
    message.successful_payment is not None
)
async def successful_payment_handler(
    message: Message
):
    payment = message.successful_payment
    try:
        payload = payment.invoice_payload
        user_id = message.from_user.id
        db_payment = get_payment_by_payload(
            payload
        )
        if not db_payment:
            logger.error(
                f"Платёж не найден: {payload}"
            )
            await message.answer(
                "⚠️ Оплата получена, "
                "но платёж не найден в базе. "
                "Обратитесь в поддержку."
            )
            return
        expected_user_id = int(
            db_payment[1]
        )
        expected_amount = int(
            db_payment[3]
        )
        if expected_user_id != user_id:
            logger.error(
                "Несовпадение Telegram ID "
                f"для платежа {payload}"
            )
            return
        if expected_amount != payment.total_amount:
            logger.error(
                "Несовпадение суммы "
                f"для платежа {payload}"
            )
            return
        success, reason = mark_payment_paid(
            payload=payload,
            telegram_charge_id=payment.telegram_payment_charge_id,
            provider_charge_id=payment.provider_payment_charge_id
        )
        if success:
            new_balance = get_balance(
                user_id
            )
            await message.answer(
                f"✅ <b>Оплата успешно получена!</b>\n\n"
                f"💰 Зачислено: "
                f"<b>+{payment.total_amount} ⭐</b>\n"
                f"💳 Новый баланс: "
                f"<b>{new_balance:.2f} ⭐</b>"
            )
            logger.info(
                f"PAYMENT SUCCESS | "
                f"user={user_id} | "
                f"amount={payment.total_amount} | "
                f"payload={payload}"
            )
        elif reason == "already_paid":
            logger.warning(
                f"Повторная обработка платежа: {payload}"
            )
        else:
            logger.error(
                f"Ошибка обработки платежа: "
                f"{payload} | {reason}"
            )
    except Exception as e:
        logger.exception(
            f"Successful payment error: {e}"
        )
# =====================================================
# ПРОВЕРКА TELEGRAM WEB APP INIT DATA
# =====================================================
def validate_telegram_init_data(
    init_data: str
):
    """
    Проверяет Telegram WebApp initData
    и возвращает данные пользователя.
    Если данные неправильные — возвращает None.
    """
    if not init_data:
        return None
    try:
        parsed = dict(
            urllib.parse.parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )
        received_hash = parsed.pop(
            "hash",
            None
        )
        if not received_hash:
            return None
        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                parsed.items()
            )
        )
        # Telegram WebApp validation:
        # secret_key = HMAC_SHA256(
        #     "WebAppData",
        #     bot_token
        # )
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
            return None
        # Проверяем user JSON.
        user_json = parsed.get("user")
        if not user_json:
            return None
        user_data = json.loads(
            user_json
        )
        user_id = user_data.get("id")
        if not user_id:
            return None
        return {
            "user_id": int(user_id),
            "username": user_data.get(
                "username"
            ),
            "first_name": user_data.get(
                "first_name"
            ),
            "last_name": user_data.get(
                "last_name"
            )
        }
    except Exception as e:
        logger.error(
            f"initData validation error: {e}"
        )
        return None
# =====================================================
# HTTP API
# =====================================================
async def health(request):
    """
    Проверка сервера.
    GET /health
    Ответ:
    OK
    """
    return web.Response(
        text="OK"
    )
async def root(request):
    """
    Главная страница API.
    """
    return web.Response(
        text="White Bear API is running"
    )
async def api_get_balance(request):
    """
    GET /api/balance
    Заголовок:
    X-Telegram-Init-Data: ...
    Возвращает баланс пользователя.
    """
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
    user_id = user["user_id"]
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
            "balance": round(balance, 2)
        }
    )
async def api_create_invoice(request):
    """
    POST /api/create-invoice
    JSON:
    {
        "amount": 100
    }
    Заголовок:
    X-Telegram-Init-Data
    Создаёт Telegram Stars invoice.
    """
    try:
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
        body = await request.json()
        amount = int(
            body.get("amount", 0)
        )
        # Ограничения пополнения.
        if amount < 1:
            return web.json_response(
                {
                    "ok": False,
                    "error": "minimum_amount_is_1"
                },
                status=400
            )
        if amount > 10000:
            return web.json_response(
                {
                    "ok": False,
                    "error": "maximum_amount_is_10000"
                },
                status=400
            )
        user_id = user["user_id"]
        ensure_user(
            user_id,
            user.get("username"),
            user.get("first_name")
        )
        # Создаём запись платежа.
        payload = create_payment(
            user_id,
            amount
        )
        # Telegram Stars.
        #
        # Для Stars:
        # currency = XTR
        # provider_token = ""
        #
        # 1 Star = 1 XTR.
        invoice_link = await bot.create_invoice_link(
            title="Пополнение White Bear",
            description=(
                f"Пополнение баланса "
                f"на {amount} ⭐"
            ),
            payload=payload,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=f"{amount} ⭐",
                    amount=amount
                )
            ],
            provider_token=""
        )
        return web.json_response(
            {
                "ok": True,
                "invoice_url": invoice_link,
                "amount": amount
            }
        )
    except json.JSONDecodeError:
        return web.json_response(
            {
                "ok": False,
                "error": "invalid_json"
            },
            status=400
        )
    except Exception as e:
        logger.exception(
            f"Create invoice error: {e}"
        )
        return web.json_response(
            {
                "ok": False,
                "error": "server_error"
            },
            status=500
        )
async def api_user(request):
    """
    GET /api/user
    Возвращает Telegram ID и баланс.
    """
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
    user_id = user["user_id"]
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
# CORS
# =====================================================
@web.middleware
async def cors_middleware(
    request,
    handler
):
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        response = await handler(
            request
        )
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )
    return response
# =====================================================
# HTTP SERVER
# =====================================================
async def start_http_server():
    app = web.Application(
        middlewares=[
            cors_middleware
        ]
    )
    # Проверка сервера
    app.router.add_get(
        "/",
        root
    )
    app.router.add_get(
        "/health",
        health
    )
    # API
    app.router.add_get(
        "/api/user",
        api_user
    )
    app.router.add_get(
        "/api/balance",
        api_get_balance
    )
    app.router.add_post(
        "/api/create-invoice",
        api_create_invoice
    )
    # OPTIONS для браузера
    app.router.add_options(
        "/api/{tail:.*}",
        lambda request: web.Response()
    )
    runner = web.AppRunner(
        app
    )
    await runner.setup()
    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )
    await site.start()
    logger.info(
        f"🌐 HTTP API запущен: "
        f"http://{HOST}:{PORT}"
    )
    logger.info(
        f"❤️ Health: /health"
    )
    logger.info(
        f"💳 Create invoice: "
        f"POST /api/create-invoice"
    )
    # Держим HTTP-сервер запущенным.
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
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
            description="Открыть приложение"
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
    init_db()
    await set_commands_and_menu()
    # Запускаем одновременно:
    #
    # 1. Telegram polling
    # 2. HTTP API
    #
    await asyncio.gather(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        ),
        start_http_server()
    )
# =====================================================
# ENTRY POINT
# =====================================================
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