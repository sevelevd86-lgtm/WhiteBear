import asyncio
import logging
import sys
import sqlite3
import secrets
import os
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
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties


# =====================================================
# КОНФИГУРАЦИЯ
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_NEW_BOT_TOKEN_HERE")

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

# Bothost может сам передавать PORT
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

logger = logging.getLogger(__name__)


# =====================================================
# БАЗА ДАННЫХ
# =====================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


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
    # Нужна для защиты от повторного начисления одного и того же платежа.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_payment_charge_id TEXT UNIQUE,
            provider_payment_charge_id TEXT,
            user_id INTEGER,
            amount INTEGER,
            currency TEXT,
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


def ensure_user(
    user_id: int,
    username: str = None,
    first_name: str = None
):
    user = get_user(user_id)

    if user:
        # Обновляем имя и username
        conn = get_db()
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

        return

    create_user(
        user_id=user_id,
        username=username,
        first_name=first_name
    )


# =====================================================
# БАЛАНС
# =====================================================

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
        return float(result["balance"])

    return 0.0


def add_balance(user_id: int, amount: float):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    if result:
        old_balance = float(result["balance"])
        new_balance = old_balance + amount

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
            amount
        ))

    conn.commit()
    conn.close()


# =====================================================
# РЕФЕРАЛЫ
# =====================================================

def get_user_by_ref_code(ref_code: str):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return result["user_id"] if result else None


def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):
    if referrer_id == referred_id:
        return False

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

    return int(result[0]) if result else 0


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# =====================================================
# ПЛАТЕЖИ
# =====================================================

def payment_exists(telegram_payment_charge_id: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM payments
        WHERE telegram_payment_charge_id = ?
    """, (
        telegram_payment_charge_id,
    ))

    result = cursor.fetchone()

    conn.close()

    return result is not None


def save_payment(
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str,
    user_id: int,
    amount: int,
    currency: str
):
    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO payments (
                telegram_payment_charge_id,
                provider_payment_charge_id,
                user_id,
                amount,
                currency
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            telegram_payment_charge_id,
            provider_payment_charge_id,
            user_id,
            amount,
            currency
        ))

        conn.commit()

    except sqlite3.IntegrityError:
        conn.close()
        return False

    conn.close()

    return True


# =====================================================
# БОТ
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
        text="🎮 Открыть сайт",
        web_app=WebAppInfo(url=WEBAPP_URL)
    )

    builder.button(
        text="💰 Пополнить баланс",
        callback_data="deposit"
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

    builder.adjust(1, 2, 1, 1)

    return builder.as_markup()


# =====================================================
# КЛАВИАТУРА ПОПОЛНЕНИЯ
# =====================================================

def get_deposit_keyboard():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="⭐ 10 Stars",
        callback_data="pay_10"
    )

    builder.button(
        text="⭐ 50 Stars",
        callback_data="pay_50"
    )

    builder.button(
        text="⭐ 100 Stars",
        callback_data="pay_100"
    )

    builder.button(
        text="⭐ 250 Stars",
        callback_data="pay_250"
    )

    builder.button(
        text="⭐ 500 Stars",
        callback_data="pay_500"
    )

    builder.button(
        text="⭐ 1000 Stars",
        callback_data="pay_1000"
    )

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_start"
    )

    builder.adjust(2, 2, 2, 1)

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
                        f"<b>{first_name}</b> "
                        f"перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили "
                        f"<b>+10 ⭐</b>\n\n"
                        f"📊 Всего приглашено: "
                        f"<b>{get_referrals_count(invited_by)}</b>"
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

        f"🐻‍❄️ <b>Добро пожаловать в DROP!</b>\n\n"

        f"👤 <b>{first_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n\n"

        f"📎 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"

        f"🎮 Откройте сайт кнопкой ниже.\n"
        f"💰 Пополнение производится через "
        f"<b>Telegram Stars</b>.",

        reply_markup=get_main_keyboard()
    )


# =====================================================
# КОМАНДА GAME
# =====================================================

@dp.message(Command("game"))
async def game_command(message: Message):

    ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    await message.answer(

        "🎮 <b>White Bear DROP</b>\n\n"
        "Нажмите кнопку ниже, чтобы открыть сайт.",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть сайт",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💰 Пополнить баланс",
                        callback_data="deposit"
                    )
                ]
            ]
        )
    )


# =====================================================
# КОМАНДА BALANCE
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

        f"💰 <b>Ваш баланс</b>\n\n"
        f"⭐ <b>{balance:.2f}</b>\n\n"
        f"👥 Рефералов: <b>{ref_count}</b>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Пополнить",
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
# КОМАНДА PROFILE
# =====================================================

@dp.message(Command("profile"))
async def profile_command(message: Message):

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

        f"Имя: <b>{first_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Пополнить",
                        callback_data="deposit"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть сайт",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
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
        "/game — Открыть сайт\n"
        "/balance — Баланс\n"
        "/profile — Профиль\n"
        "/help — Помощь\n\n"

        "💰 Пополнение выполняется через "
        "<b>Telegram Stars</b>.\n\n"

        "После успешной оплаты Stars автоматически "
        "зачисляются на баланс сайта."
    )


# =====================================================
# CALLBACK: ПОПОЛНЕНИЕ
# =====================================================

@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_callback(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(

        "💰 <b>Пополнение баланса</b>\n\n"

        "Выберите количество Telegram Stars.\n\n"

        "После оплаты сумма автоматически "
        "появится на вашем балансе сайта.",

        reply_markup=get_deposit_keyboard()
    )

    await callback.answer()


# =====================================================
# СОЗДАНИЕ INVOICE
# =====================================================

async def create_stars_invoice(
    callback: types.CallbackQuery,
    amount: int
):

    user_id = callback.from_user.id

    ensure_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    payload = f"deposit:{user_id}:{amount}:{secrets.token_hex(8)}"

    prices = [
        LabeledPrice(
            label=f"Пополнение баланса на {amount} ⭐",
            amount=amount
        )
    ]

    try:

        await bot.send_invoice(

            chat_id=user_id,

            title="Пополнение баланса",

            description=(
                f"Пополнение баланса White Bear DROP "
                f"на {amount} Telegram Stars."
            ),

            payload=payload,

            currency="XTR",

            prices=prices,

            provider_token=""

        )

        await callback.answer(
            "⭐ Счёт создан!"
        )

    except Exception as e:

        logger.exception(
            "Ошибка создания Stars invoice"
        )

        await callback.answer(
            "❌ Не удалось создать оплату",
            show_alert=True
        )


# =====================================================
# CALLBACK PAY 10
# =====================================================

@dp.callback_query(lambda c: c.data == "pay_10")
async def pay_10(callback: types.CallbackQuery):

    await create_stars_invoice(
        callback,
        10
    )


# =====================================================
# CALLBACK PAY 50
# =====================================================

@dp.callback_query(lambda c: c.data == "pay_50")
async def pay_50(callback: types.CallbackQuery):

    await create_stars_invoice(
        callback,
        50
    )


# =====================================================
# CALLBACK PAY 100
# =====================================================

@dp.callback_query(lambda c: c.data == "pay_100")
async def pay_100(callback: types.CallbackQuery):

    await create_stars_invoice(
        callback,
        100
    )


# =====================================================
# CALLBACK PAY 250
# =====================================================

@dp.callback_query(lambda c: c.data == "pay_250")
async def pay_250(callback: types.CallbackQuery):

    await create_stars_invoice(
        callback,
        250
    )


# =====================================================
# CALLBACK PAY 500
# =====================================================

@dp.callback_query(lambda c: c.data == "pay_500")
async def pay_500(callback: types.CallbackQuery):

    await create_stars_invoice(
        callback,
        500
    )


# =====================================================
# CALLBACK PAY 1000
# =====================================================

@dp.callback_query(lambda c: c.data == "pay_1000")
async def pay_1000(callback: types.CallbackQuery):

    await create_stars_invoice(
        callback,
        1000
    )


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):

    logger.info(
        f"💳 PreCheckout: "
        f"user={query.from_user.id}, "
        f"currency={query.currency}, "
        f"amount={query.total_amount}"
    )

    # Telegram Stars
    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message=(
                "Неверная валюта платежа."
            )
        )

        return

    # Проверяем payload
    if not query.invoice_payload.startswith(
        "deposit:"
    ):

        await query.answer(
            ok=False,
            error_message=(
                "Неверный платёж."
            )
        )

        return

    await query.answer(ok=True)


# =====================================================
# УСПЕШНАЯ ОПЛАТА
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

    amount = payment.total_amount

    currency = payment.currency

    telegram_charge_id = (
        payment.telegram_payment_charge_id
    )

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    logger.info(
        f"💰 УСПЕШНАЯ ОПЛАТА: "
        f"user={user_id}, "
        f"amount={amount}, "
        f"currency={currency}, "
        f"charge={telegram_charge_id}"
    )

    # -------------------------------------------------
    # Проверяем валюту
    # -------------------------------------------------

    if currency != "XTR":

        logger.error(
            f"❌ Неизвестная валюта: {currency}"
        )

        return

    # -------------------------------------------------
    # Защита от повторного начисления
    # -------------------------------------------------

    if payment_exists(
        telegram_charge_id
    ):

        logger.warning(
            f"⚠️ Платёж уже обработан: "
            f"{telegram_charge_id}"
        )

        await message.answer(
            "⚠️ Этот платёж уже был зачислен."
        )

        return

    # -------------------------------------------------
    # Проверяем пользователя
    # -------------------------------------------------

    ensure_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    # -------------------------------------------------
    # Сохраняем платёж
    # -------------------------------------------------

    saved = save_payment(
        telegram_payment_charge_id=telegram_charge_id,
        provider_payment_charge_id=provider_charge_id,
        user_id=user_id,
        amount=amount,
        currency=currency
    )

    if not saved:

        logger.warning(
            "⚠️ Платёж не сохранён — "
            "возможно уже обработан."
        )

        await message.answer(
            "⚠️ Этот платёж уже был обработан."
        )

        return

    # -------------------------------------------------
    # НАЧИСЛЕНИЕ
    # -------------------------------------------------

    add_balance(
        user_id,
        float(amount)
    )

    new_balance = get_balance(user_id)

    # -------------------------------------------------
    # УВЕДОМЛЕНИЕ
    # -------------------------------------------------

    await message.answer(

        f"✅ <b>Оплата прошла успешно!</b>\n\n"

        f"⭐ Зачислено: <b>+{amount}</b>\n"
        f"💰 Новый баланс: "
        f"<b>{new_balance:.2f} ⭐</b>\n\n"

        f"🎮 Теперь можете открыть сайт.",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть сайт",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ]
            ]
        )
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

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(

        f"💰 <b>Ваш баланс</b>\n\n"

        f"⭐ <b>{balance:.2f}</b>\n\n"

        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Пополнить",
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

    ref_count = get_referrals_count(
        user_id
    )

    ref_link = get_referral_link(
        user_id
    )

    await callback.message.edit_text(

        f"👤 <b>Профиль</b>\n\n"

        f"Имя: <b>{first_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n\n"

        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Пополнить",
                        callback_data="deposit"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть сайт",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
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
# CALLBACK: REFERRAL
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

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(

        f"📎 <b>Реферальная система</b>\n\n"

        f"Ваша ссылка:\n"
        f"<code>{ref_link}</code>\n\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"💰 За каждого приглашённого "
        f"начисляется <b>10 ⭐</b>.",

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

    ensure_user(
        user_id,
        callback.from_user.username,
        first_name
    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(
        user_id
    )

    await callback.message.edit_text(

        f"🐻‍❄️ <b>White Bear DROP</b>\n\n"

        f"👤 {first_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n\n"

        f"🎮 Откройте сайт или пополните баланс.",

        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# WEBAPP DATA
# =====================================================

@dp.message(
    lambda msg: msg.web_app_data is not None
)
async def web_app_data_handler(
    message: Message
):

    try:

        import json

        data = json.loads(
            message.web_app_data.data
        )

        user_id = message.from_user.id

        action = data.get("action")

        ensure_user(
            user_id,
            message.from_user.username,
            message.from_user.first_name
        )

        # ---------------------------------------------
        # Получить баланс
        # ---------------------------------------------

        if action == "getBalance":

            balance = get_balance(
                user_id
            )

            await message.answer(
                f"{balance:.2f}"
            )

        # ---------------------------------------------
        # Реферальная ссылка
        # ---------------------------------------------

        elif action == "getReferralLink":

            ref_link = get_referral_link(
                user_id
            )

            await message.answer(
                ref_link
            )

        else:

            await message.answer(
                "❌ Неизвестное действие."
            )

    except Exception as e:

        logger.exception(
            "Ошибка обработки WebApp data"
        )

        await message.answer(
            "❌ Ошибка обработки данных."
        )


# =====================================================
# WEB SERVER
# =====================================================

async def health(request):
    return web.Response(
        text="OK"
    )


# =====================================================
# API BALANCE
# =====================================================

async def api_balance(request):

    try:

        user_id = request.query.get("user_id")

        if not user_id:
            return web.json_response(
                {
                    "ok": False,
                    "error": "user_id_required"
                },
                status=400
            )

        user_id = int(user_id)

        user = get_user(user_id)

        if not user:

            return web.json_response(
                {
                    "ok": False,
                    "error": "user_not_found"
                },
                status=404
            )

        balance = get_balance(user_id)

        return web.json_response(
            {
                "ok": True,
                "user_id": user_id,
                "balance": balance
            }
        )

    except ValueError:

        return web.json_response(
            {
                "ok": False,
                "error": "invalid_user_id"
            },
            status=400
        )

    except Exception as e:

        logger.exception(
            "API balance error"
        )

        return web.json_response(
            {
                "ok": False,
                "error": "server_error"
            },
            status=500
        )


# =====================================================
# API INFO
# =====================================================

async def api_info(request):

    return web.json_response(
        {
            "ok": True,
            "service": "White Bear DROP",
            "payment": "Telegram Stars XTR",
            "status": "online"
        }
    )


# =====================================================
# WEB SERVER START
# =====================================================

async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health
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
        "/api/info",
        api_info
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
        f"🌐 Web server запущен: 0.0.0.0:{PORT}"
    )

    return runner


# =====================================================
# КОМАНДЫ И МЕНЮ
# =====================================================

async def set_commands_and_menu():

    commands = [

        BotCommand(
            command="start",
            description="Главное меню"
        ),

        BotCommand(
            command="game",
            description="Открыть сайт"
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
        "🚀 Запуск White Bear DROP..."
    )

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE"
    ):

        logger.error(
            "❌ BOT_TOKEN не установлен!"
        )

        return

    # База
    init_db()

    # Команды
    await set_commands_and_menu()

    # Web server
    web_runner = await start_web_server()

    try:

        logger.info(
            "🤖 Telegram polling запущен..."
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