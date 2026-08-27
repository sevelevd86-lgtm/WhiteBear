import asyncio
import logging
import os
import sys
import sqlite3
import secrets
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from datetime import datetime
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
# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = "White_Bear_ROBOT"
# URL твоего HTML Mini App
WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"
# Порт для Bothost
PORT = int(os.getenv("PORT", "8080"))
DB_NAME = "users.db"
# Сколько секунд считаем Telegram initData актуальным
INIT_DATA_MAX_AGE = 86400
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
# ПРОВЕРКА ТОКЕНА
# ============================================================
if not BOT_TOKEN:
    logger.error(
        "BOT_TOKEN не найден. "
        "Добавь переменную BOT_TOKEN в настройках хостинга."
    )
    raise RuntimeError("BOT_TOKEN is not configured")
# ============================================================
# БАЗА ДАННЫХ
# ============================================================
def get_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30)
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
    # Таблица платежей.
    # Она нужна, чтобы один и тот же successful_payment
    # случайно не был обработан дважды.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE,
            provider_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")
# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================
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
    # Проверяем, существует ли пользователь
    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )
    if cursor.fetchone():
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
    logger.info(f"👤 Создан пользователь {user_id}")
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
    if result:
        return float(result["balance"])
    return 0.0
def update_balance(user_id: int, new_balance: float):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )
    if cursor.fetchone():
        cursor.execute("""
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
        """, (
            new_balance,
            user_id
        ))
    else:
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
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )
    result = cursor.fetchone()
    if result:
        new_balance = float(result["balance"]) + float(amount)
        cursor.execute("""
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
        """, (
            new_balance,
            user_id
        ))
    else:
        new_balance = float(amount)
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
    return new_balance
# ============================================================
# РЕФЕРАЛЫ
# ============================================================
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
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (referrer_id,)
    )
    referrer = cursor.fetchone()
    if referrer:
        cursor.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            reward,
            referrer_id
        ))
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (referred_id,)
    )
    referred = cursor.fetchone()
    if referred:
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
# ============================================================
# TELEGRAM WEBAPP INIT DATA
# ============================================================
def validate_telegram_init_data(init_data: str):
    """
    Проверяет Telegram WebApp initData.
    Возвращает данные пользователя, если подпись корректная.
    Иначе возвращает None.
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
        try:
            auth_timestamp = int(auth_date)
        except ValueError:
            return None
        now = int(datetime.now().timestamp())
        if now - auth_timestamp > INIT_DATA_MAX_AGE:
            return None
        # data-check-string
        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
        )
        # secret key
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
# ============================================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)
dp = Dispatcher()
# ============================================================
# ГЛАВНАЯ КЛАВИАТУРА
# ============================================================
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
    builder.button(
        text="⭐ Пополнить баланс",
        callback_data="deposit"
    )
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()
# ============================================================
# КНОПКИ ПОПОЛНЕНИЯ
# ============================================================
def get_deposit_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1 Star",
                    callback_data="buy_stars_1"
                ),
                InlineKeyboardButton(
                    text="⭐ 10 Stars",
                    callback_data="buy_stars_10"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 50 Stars",
                    callback_data="buy_stars_50"
                ),
                InlineKeyboardButton(
                    text="⭐ 100 Stars",
                    callback_data="buy_stars_100"
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
    existing_user = get_user(user_id)
    if not existing_user:
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
                        f"Ошибка уведомления реферера: {e}"
                    )
    else:
        # Обновляем данные Telegram
        create_user(
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
        f"🎮 Откройте игры кнопкой ниже.",
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
        f"🎮 <b>Открываем игры...</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>",
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
# ============================================================
# BALANCE
# ============================================================
@dp.message(Command("balance"))
async def balance_command(message: Message):
    user_id = message.from_user.id
    balance = get_balance(user_id)
    ref_count = get_referrals_count(user_id)
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
    ref_count = get_referrals_count(user_id)
    ref_link = get_referral_link(user_id)
    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {first_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: {ref_count}\n\n"
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
        "/game — Открыть игры\n"
        "/balance — Баланс\n"
        "/profile — Профиль\n"
        "/help — Помощь\n\n"
        "⭐ Пополнение производится "
        "через Telegram Stars."
    )
# ============================================================
# DEPOSIT
# ============================================================
@dp.callback_query(F.data == "deposit")
async def deposit_callback(
    callback: types.CallbackQuery
):
    await callback.message.edit_text(
        "⭐ <b>Пополнение баланса</b>\n\n"
        "Выберите количество Telegram Stars.\n\n"
        "После успешной оплаты Stars "
        "будут автоматически начислены "
        "на баланс этого Telegram ID.",
        reply_markup=get_deposit_keyboard()
    )
    await callback.answer()
# ============================================================
# СОЗДАНИЕ INVOICE
# ============================================================
async def send_stars_invoice(
    chat_id: int,
    stars_amount: int
):
    if stars_amount < 1:
        raise ValueError(
            "Количество Stars должно быть не меньше 1"
        )
    await bot.send_invoice(
        chat_id=chat_id,
        title=f"Пополнение на {stars_amount} ⭐",
        description=(
            f"Пополнение игрового баланса "
            f"на {stars_amount} Telegram Stars."
        ),
        payload=(
            f"deposit_{chat_id}_"
            f"{stars_amount}_"
            f"{secrets.token_hex(8)}"
        ),
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{stars_amount} Telegram Stars",
                amount=stars_amount
            )
        ],
        provider_token=""
    )
# ============================================================
# BUY STARS
# ============================================================
@dp.callback_query(
    F.data.startswith("buy_stars_")
)
async def buy_stars_callback(
    callback: types.CallbackQuery
):
    try:
        stars_amount = int(
            callback.data.replace(
                "buy_stars_",
                ""
            )
        )
        if stars_amount < 1:
            await callback.answer(
                "Минимум 1 Star",
                show_alert=True
            )
            return
        await send_stars_invoice(
            callback.from_user.id,
            stars_amount
        )
        await callback.answer()
    except Exception as e:
        logger.exception(
            "Ошибка создания invoice"
        )
        await callback.answer(
            "❌ Не удалось создать оплату",
            show_alert=True
        )
# ============================================================
# PRE-CHECKOUT
# ============================================================
@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: types.PreCheckoutQuery
):
    try:
        if query.currency != "XTR":
            await query.answer(
                ok=False,
                error_message=(
                    "Используйте Telegram Stars."
                )
            )
            return
        if query.total_amount < 1:
            await query.answer(
                ok=False,
                error_message=(
                    "Некорректная сумма."
                )
            )
            return
        await query.answer(ok=True)
        logger.info(
            f"✅ PreCheckout OK: "
            f"user={query.from_user.id}, "
            f"amount={query.total_amount} XTR"
        )
    except Exception as e:
        logger.exception(
            f"Ошибка pre_checkout: {e}"
        )
        try:
            await query.answer(
                ok=False,
                error_message=(
                    "Ошибка обработки платежа."
                )
            )
        except Exception:
            pass
# ============================================================
# УСПЕШНАЯ ОПЛАТА STARS
# ============================================================
@dp.message(
    F.successful_payment
)
async def successful_payment_handler(
    message: Message
):
    try:
        payment = message.successful_payment
        user_id = message.from_user.id
        # ====================================================
        # ВАЖНО:
        # Telegram сам сообщает фактически оплаченную сумму.
        # Мы НЕ берём сумму из HTML.
        # ====================================================
        amount = int(
            payment.total_amount
        )
        currency = payment.currency
        telegram_charge_id = (
            payment.telegram_payment_charge_id
        )
        provider_charge_id = (
            payment.provider_payment_charge_id
        )
        if currency != "XTR":
            logger.error(
                f"Получена оплата не XTR: "
                f"{currency}"
            )
            await message.answer(
                "❌ Ошибка валюты платежа."
            )
            return
        if amount < 1:
            await message.answer(
                "❌ Некорректная сумма платежа."
            )
            return
        # ====================================================
        # Проверяем, не был ли этот платеж обработан раньше.
        # ====================================================
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id
            FROM payments
            WHERE telegram_charge_id = ?
        """, (
            telegram_charge_id,
        ))
        existing_payment = cursor.fetchone()
        if existing_payment:
            conn.close()
            logger.warning(
                f"⚠️ Повторный платеж "
                f"{telegram_charge_id}"
            )
            await message.answer(
                "ℹ️ Этот платеж уже был зачислен."
            )
            return
        # ====================================================
        # Убеждаемся, что пользователь существует.
        # ====================================================
        cursor.execute("""
            SELECT user_id
            FROM users
            WHERE user_id = ?
        """, (
            user_id,
        ))
        user_exists = cursor.fetchone()
        if not user_exists:
            cursor.execute("""
                INSERT INTO users (
                    user_id,
                    balance,
                    username,
                    first_name,
                    ref_code
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                user_id,
                0.0,
                message.from_user.username,
                message.from_user.first_name,
                secrets.token_hex(8)
            ))
        # ====================================================
        # Начисляем РОВНО amount Stars.
        # ====================================================
        cursor.execute("""
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
        """, (
            amount,
            user_id
        ))
        # ====================================================
        # Сохраняем платеж.
        # ====================================================
        cursor.execute("""
            INSERT INTO payments (
                user_id,
                telegram_charge_id,
                provider_charge_id,
                amount,
                currency
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            telegram_charge_id,
            provider_charge_id,
            amount,
            currency
        ))
        conn.commit()
        # Получаем новый баланс
        cursor.execute("""
            SELECT balance
            FROM users
            WHERE user_id = ?
        """, (
            user_id,
        ))
        result = cursor.fetchone()
        new_balance = (
            float(result["balance"])
            if result
            else float(amount)
        )
        conn.close()
        # ====================================================
        # ЛОГ
        # ====================================================
        logger.info(
            f"💰 УСПЕШНАЯ ОПЛАТА: "
            f"user_id={user_id}, "
            f"amount={amount} XTR, "
            f"new_balance={new_balance}"
        )
        # ====================================================
        # ОТВЕТ ПОЛЬЗОВАТЕЛЮ
        # ====================================================
        await message.answer(
            f"✅ <b>Оплата успешно получена!</b>\n\n"
            f"⭐ Оплачено: <b>{amount} Stars</b>\n"
            f"💰 Начислено: <b>{amount} ⭐</b>\n"
            f"💳 Новый баланс: "
            f"<b>{new_balance:.2f} ⭐</b>\n\n"
            f"🆔 Telegram ID: "
            f"<code>{user_id}</code>"
        )
    except Exception as e:
        logger.exception(
            f"❌ Ошибка обработки successful_payment: {e}"
        )
        try:
            await message.answer(
                "⚠️ Оплата получена, "
                "но произошла ошибка обработки. "
                "Обратитесь к администратору."
            )
        except Exception:
            pass
# ============================================================
# BALANCE CALLBACK
# ============================================================
@dp.callback_query(F.data == "balance")
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
# ============================================================
# PROFILE CALLBACK
# ============================================================
@dp.callback_query(F.data == "profile")
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
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n\n"
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
# ============================================================
# REFERRAL CALLBACK
# ============================================================
@dp.callback_query(F.data == "referral")
async def referral_callback(
    callback: types.CallbackQuery
):
    user_id = callback.from_user.id
    ref_link = get_referral_link(user_id)
    count = get_referrals_count(user_id)
    await callback.message.edit_text(
        f"📎 <b>Реферальная ссылка</b>\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: <b>{count}</b>\n\n"
        f"💰 За каждого приглашённого "
        f"пользователя начисляется 10 ⭐.",
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
# BACK
# ============================================================
@dp.callback_query(F.data == "back_to_start")
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
        f"🆔 Ваш ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>\n\n"
        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()
# ============================================================
# WEB API
# ============================================================
async def health(request: web.Request):
    return web.Response(
        text="OK",
        content_type="text/plain"
    )
# ============================================================
# CORS
# ============================================================
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Telegram-Init-Data"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, OPTIONS"
    )
    return response
# ============================================================
# API OPTIONS
# ============================================================
async def api_options(request: web.Request):
    response = web.Response(status=204)
    return add_cors_headers(response)
# ============================================================
# API BALANCE
# ============================================================
async def api_balance(request: web.Request):
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )
    # Также разрешаем передавать initData через query
    if not init_data:
        init_data = request.query.get(
            "initData",
            ""
        )
    user_data = validate_telegram_init_data(
        init_data
    )
    if not user_data:
        response = web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_data"
            },
            status=401
        )
        return add_cors_headers(response)
    try:
        user_id = int(
            user_data["id"]
        )
    except Exception:
        response = web.json_response(
            {
                "ok": False,
                "error": "invalid_user_id"
            },
            status=400
        )
        return add_cors_headers(response)
    # Создаём/обновляем пользователя
    create_user(
        user_id=user_id,
        username=user_data.get("username"),
        first_name=user_data.get("first_name")
    )
    balance = get_balance(user_id)
    response = web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "balance": balance
        }
    )
    return add_cors_headers(response)
# ============================================================
# API USER
# ============================================================
async def api_user(request: web.Request):
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )
    if not init_data:
        init_data = request.query.get(
            "initData",
            ""
        )
    user_data = validate_telegram_init_data(
        init_data
    )
    if not user_data:
        response = web.json_response(
            {
                "ok": False,
                "error": "invalid_telegram_data"
            },
            status=401
        )
        return add_cors_headers(response)
    user_id = int(
        user_data["id"]
    )
    create_user(
        user_id=user_id,
        username=user_data.get("username"),
        first_name=user_data.get("first_name")
    )
    balance = get_balance(user_id)
    response = web.json_response(
        {
            "ok": True,
            "user": {
                "id": user_id,
                "username": user_data.get("username"),
                "first_name": user_data.get("first_name"),
                "balance": balance
            }
        }
    )
    return add_cors_headers(response)
# ============================================================
# WEB SERVER
# ============================================================
async def start_web_server():
    app = web.Application()
    # Health
    app.router.add_get(
        "/health",
        health
    )
    # API
    app.router.add_get(
        "/api/balance",
        api_balance
    )
    app.router.add_get(
        "/api/user",
        api_user
    )
    # OPTIONS
    app.router.add_route(
        "OPTIONS",
        "/api/balance",
        api_options
    )
    app.router.add_route(
        "OPTIONS",
        "/api/user",
        api_options
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
        f"🌐 Web API запущен на порту {PORT}"
    )
    logger.info(
        f"❤️ Health: /health"
    )
    logger.info(
        f"💰 API: /api/balance"
    )
    logger.info(
        f"👤 API: /api/user"
    )
    # Сервер должен работать постоянно
    while True:
        await asyncio.sleep(3600)
# ============================================================
# КОМАНДЫ БОТА
# ============================================================
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
        "✅ Команды и WebApp-кнопка установлены"
    )
# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    logger.info(
        "🚀 Запуск White Bear Bot..."
    )
    init_db()
    await bot.delete_webhook(
        drop_pending_updates=False
    )
    await set_commands_and_menu()
    logger.info(
        "🤖 Telegram polling запускается..."
    )
    # Одновременно запускаем:
    #
    # 1. Telegram bot
    # 2. Web API
    #
    await asyncio.gather(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        ),
        start_web_server()
    )
# ============================================================
# ENTRY POINT
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