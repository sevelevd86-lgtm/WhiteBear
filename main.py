import asyncio
import logging
import sys
import sqlite3
import secrets
import os
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

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "8918284594:AAFLxOg1eEx4JS6z6V9wHr-t8T3Q9Qwiepg"
)

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

SERVER_URL = "https://bot_1787862010_6746_jix44.bothost.tech"

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

def get_connection():
    return sqlite3.connect(DB_NAME)


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

    # Таблица оплаченных Stars.
    # Нужна для защиты от повторного начисления.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS star_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT UNIQUE NOT NULL,
            provider_payment_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
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
        INSERT INTO users
        (
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
        return float(result[0])

    return 0.0


def update_balance(user_id: int, amount: float):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (amount, user_id)
    )

    if cursor.rowcount == 0:

        cursor.execute("""
            INSERT INTO users
            (
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
            INSERT INTO users
            (
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

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )

    result = cursor.fetchone()

    conn.close()

    return result[0] if result else None


def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):

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
        INSERT INTO referrals
        (
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

    cursor.execute("""
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
    """, (
        reward,
        referrer_id
    ))

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


def get_referrals_count(user_id: int):

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

    return result[0] if result else 0


def get_referral_link(user_id: int):

    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )


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
# КЛАВИАТУРА ПОПОЛНЕНИЯ
# =====================================================

def get_deposit_keyboard():

    builder = InlineKeyboardBuilder()

    builder.button(
        text="⭐ 100 Stars",
        callback_data="deposit_100"
    )

    builder.button(
        text="⭐ 250 Stars",
        callback_data="deposit_250"
    )

    builder.button(
        text="⭐ 500 Stars",
        callback_data="deposit_500"
    )

    builder.button(
        text="⭐ 1000 Stars",
        callback_data="deposit_1000"
    )

    builder.button(
        text="🔙 Назад",
        callback_data="profile"
    )

    builder.adjust(2, 2, 1)

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

        if len(args) > 1:

            if args[1].startswith("ref_"):

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
                        f"<b>+10 ⭐</b>\n\n"
                        f"📊 Всего приглашено: "
                        f"<b>{get_referrals_count(invited_by)}</b>"
                    )

                except Exception as e:

                    logger.error(
                        f"Ошибка уведомления: {e}"
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

        f"🎮 Нажмите кнопку ниже, "
        f"чтобы открыть приложение.",

        reply_markup=get_main_keyboard()
    )


# =====================================================
# КОМАНДА GAME
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
# КОМАНДА BALANCE
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
        f"{ref_count}",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
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
# КОМАНДА PROFILE
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

        f"Имя: "
        f"<b>{first_name}</b>\n"

        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 Реферальная ссылка:\n"
        f"<code>{ref_link}</code>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

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
# ПОПОЛНЕНИЕ
# =====================================================

@dp.callback_query(lambda c: c.data == "deposit")
async def deposit_callback(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(

        "⭐ <b>Пополнение баланса</b>\n\n"
        "Выберите количество Telegram Stars:\n\n"
        "После успешной оплаты Stars "
        "автоматически будут начислены "
        "на ваш баланс.",

        reply_markup=get_deposit_keyboard()
    )

    await callback.answer()


# =====================================================
# СОЗДАНИЕ INVOICE
# =====================================================

async def create_star_invoice(
    callback: types.CallbackQuery,
    amount: int
):

    user_id = callback.from_user.id

    payload = (
        f"deposit:"
        f"{user_id}:"
        f"{amount}:"
        f"{secrets.token_hex(8)}"
    )

    prices = [
        LabeledPrice(
            label=f"{amount} Telegram Stars",
            amount=amount
        )
    ]

    await bot.send_invoice(

        chat_id=user_id,

        title="Пополнение баланса",

        description=(
            f"Пополнение игрового баланса "
            f"на {amount} Telegram Stars."
        ),

        payload=payload,

        currency="XTR",

        prices=prices,

        provider_token="",

    )

    await callback.answer()


# =====================================================
# КНОПКИ СУММ
# =====================================================

@dp.callback_query(
    lambda c: c.data.startswith("deposit_")
    and c.data != "deposit"
)
async def deposit_amount_callback(
    callback: types.CallbackQuery
):

    try:

        amount = int(
            callback.data.replace(
                "deposit_",
                ""
            )
        )

    except ValueError:

        await callback.answer(
            "Ошибка суммы",
            show_alert=True
        )

        return

    allowed_amounts = [
        100,
        250,
        500,
        1000
    ]

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

    try:

        payload = query.invoice_payload

        if not payload.startswith("deposit:"):

            await query.answer(
                ok=False,
                error_message="Неверный платёж."
            )

            return

        parts = payload.split(":")

        if len(parts) < 4:

            await query.answer(
                ok=False,
                error_message="Неверные данные платежа."
            )

            return

        payment_user_id = int(parts[1])

        amount = int(parts[2])

        if payment_user_id != query.from_user.id:

            await query.answer(
                ok=False,
                error_message="Пользователь платежа не совпадает."
            )

            return

        if amount <= 0:

            await query.answer(
                ok=False,
                error_message="Неверная сумма."
            )

            return

        if query.currency != "XTR":

            await query.answer(
                ok=False,
                error_message="Неверная валюта платежа."
            )

            return

        if query.total_amount != amount:

            await query.answer(
                ok=False,
                error_message="Неверная сумма платежа."
            )

            return

        await query.answer(ok=True)

    except Exception as e:

        logger.exception(
            f"Ошибка pre-checkout: {e}"
        )

        await query.answer(
            ok=False,
            error_message="Не удалось проверить платёж."
        )


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

    try:

        payload = payment.invoice_payload

        parts = payload.split(":")

        if len(parts) < 4:

            logger.error(
                "Неверный payload платежа"
            )

            return

        payment_user_id = int(parts[1])

        amount = int(parts[2])

        if payment_user_id != user_id:

            logger.error(
                f"Несовпадение ID: "
                f"{payment_user_id} != {user_id}"
            )

            return

        if payment.currency != "XTR":

            logger.error(
                "Платёж имеет неправильную валюту"
            )

            return

        if payment.total_amount != amount:

            logger.error(
                f"Неверная сумма: "
                f"{payment.total_amount} != {amount}"
            )

            return

        charge_id = (
            payment.telegram_payment_charge_id
        )

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id
            FROM star_payments
            WHERE telegram_payment_charge_id = ?
        """, (
            charge_id,
        ))

        already_exists = cursor.fetchone()

        if already_exists:

            conn.close()

            logger.warning(
                f"Платёж {charge_id} "
                f"уже был обработан"
            )

            await message.answer(
                "⚠️ Этот платёж уже был зачислен."
            )

            return

        cursor.execute("""
            INSERT INTO star_payments
            (
                user_id,
                telegram_payment_charge_id,
                provider_payment_charge_id,
                amount,
                currency
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            charge_id,
            payment.provider_payment_charge_id,
            amount,
            payment.currency
        ))

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
                INSERT INTO users
                (
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

        new_balance = get_balance(user_id)

        logger.info(
            f"⭐ УСПЕШНАЯ ОПЛАТА | "
            f"user_id={user_id} | "
            f"amount={amount} XTR | "
            f"balance={new_balance}"
        )

        await message.answer(

            f"✅ <b>Оплата успешно получена!</b>\n\n"

            f"⭐ Зачислено: "
            f"<b>+{amount} ⭐</b>\n\n"

            f"💰 Новый баланс: "
            f"<b>{new_balance:.2f} ⭐</b>",

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

    except Exception as e:

        logger.exception(
            f"Ошибка обработки оплаты: {e}"
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
        "/balance — Баланс\n"
        "/profile — Профиль\n"
        "/help — Помощь\n\n"

        "⭐ Пополнение производится "
        "через Telegram Stars."

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
# CALLBACK PROFILE
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

        f"Имя: <b>{first_name}</b>\n"

        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 Реферальная ссылка:\n"
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
# CALLBACK REFERRAL
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

        f"📎 <b>Ваша реферальная ссылка</b>\n\n"

        f"<code>{ref_link}</code>\n\n"

        f"💡 Приглашайте друзей "
        f"и получайте по 10 ⭐.",

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

        f"📎 Реферальная ссылка:\n"
        f"<code>{ref_link}</code>",

        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# WEBAPP API
# =====================================================

async def health(request):

    return web.Response(
        text="OK"
    )


async def api_get_balance(request):

    try:

        user_id = int(
            request.query.get("user_id")
        )

    except:

        return web.json_response(
            {
                "ok": False,
                "error": "Invalid user_id"
            },
            status=400
        )

    user = get_user(user_id)

    if not user:

        return web.json_response({
            "ok": True,
            "user_id": user_id,
            "balance": 0
        })

    return web.json_response({

        "ok": True,

        "user_id": user_id,

        "balance": get_balance(user_id)

    })


async def api_user(request):

    try:

        user_id = int(
            request.query.get("user_id")
        )

    except:

        return web.json_response(
            {
                "ok": False,
                "error": "Invalid user_id"
            },
            status=400
        )

    user = get_user(user_id)

    if not user:

        return web.json_response({
            "ok": False,
            "error": "User not found"
        })

    return web.json_response({

        "ok": True,

        "user_id": user_id,

        "balance": get_balance(user_id),

        "username": user[2],

        "first_name": user[3],

        "referrals": get_referrals_count(
            user_id
        )

    })


# =====================================================
# WEB SERVER
# =====================================================

async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/health",
        health
    )

    app.router.add_get(
        "/api/balance",
        api_get_balance
    )

    app.router.add_get(
        "/api/user",
        api_user
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
        f"🌐 HTTP SERVER STARTED "
        f"0.0.0.0:{PORT}"
    )

    logger.info(
        f"❤️ Health: "
        f"{SERVER_URL}/health"
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
        "🚀 Запуск White Bear DROP..."
    )

    init_db()

    await set_commands_and_menu()

    web_runner = await start_web_server()

    try:

        await dp.start_polling(
            bot,
            allowed_updates=
            dp.resolve_used_update_types()
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