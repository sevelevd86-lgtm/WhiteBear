import asyncio
import logging
import sys
import sqlite3
import secrets
import json

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

# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН ПОСЛЕ ПЕРЕВЫПУСКА В @BotFather
BOT_TOKEN = "8918284594:AAG-h12sJhc7a0qaV5LgS-ea29FNeZVtJvY"

BOT_USERNAME = "White_Bear_ROBOT"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

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
            UNIQUE(referrer_id, referred_id)
        )
    """)

    # Таблица платежей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE,
            provider_charge_id TEXT,
            stars INTEGER NOT NULL,
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


# =====================================================
# БАЛАНС
# =====================================================

def get_balance(user_id: int) -> float:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return float(result[0]) if result else 0.0


def update_balance(user_id: int, amount: float):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    exists = cursor.fetchone()

    if exists:
        cursor.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
            """,
            (round(amount, 2), user_id)
        )
    else:
        cursor.execute(
            """
            INSERT INTO users (user_id, balance)
            VALUES (?, ?)
            """,
            (user_id, round(amount, 2))
        )

    conn.commit()
    conn.close()


def add_balance(user_id: int, amount: float):
    current = get_balance(user_id)
    new_balance = current + amount

    update_balance(user_id, new_balance)

    return new_balance


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

    conn.commit()
    conn.close()

    # Начисляем обоим
    add_balance(referrer_id, reward)
    add_balance(referred_id, reward)

    return True


def get_referrals_count(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
    """, (user_id,))

    result = cursor.fetchone()

    conn.close()

    return result[0] if result else 0


def get_referral_link(user_id: int) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


# =====================================================
# ПЛАТЕЖИ
# =====================================================

def payment_exists(telegram_charge_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM payments
        WHERE telegram_charge_id = ?
    """, (telegram_charge_id,))

    result = cursor.fetchone()

    conn.close()

    return result is not None


def save_payment(
    user_id: int,
    telegram_charge_id: str,
    provider_charge_id: str,
    stars: int
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO payments (
            user_id,
            telegram_charge_id,
            provider_charge_id,
            stars
        )
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        telegram_charge_id,
        provider_charge_id,
        stars
    ))

    conn.commit()
    conn.close()


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
# /START
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
                        f"{first_name} "
                        f"перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили "
                        f"<b>+10 ⭐</b>\n\n"
                        f"📊 Всего приглашено: "
                        f"<b>{get_referrals_count(invited_by)}</b>"
                    )

                except Exception as e:

                    logger.error(
                        f"Ошибка уведомления реферера: {e}"
                    )

    else:

        # Обновляем имя и username
        conn = get_connection()
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
# /GAME
# =====================================================

@dp.message(Command("game"))
async def game_command(message: Message):

    user_id = message.from_user.id

    balance = get_balance(user_id)

    await message.answer(

        f"🎮 <b>Открываем приложение...</b>\n\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="🎮 Открыть",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ]

            ]
        )
    )


# =====================================================
# /BALANCE
# =====================================================

@dp.message(Command("balance"))
async def balance_command(message: Message):

    user_id = message.from_user.id

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    await message.answer(

        f"💰 <b>Ваш баланс</b>\n\n"

        f"⭐ {balance:.2f}\n\n"

        f"👥 Приглашено: "
        f"{ref_count}"
    )


# =====================================================
# /PROFILE
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

        f"Имя: <b>{first_name}</b>\n"

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
# /HELP
# =====================================================

@dp.message(Command("help"))
async def help_command(message: Message):

    await message.answer(

        "📖 <b>Помощь</b>\n\n"

        "/start — Главное меню\n"
        "/game — Открыть приложение\n"
        "/balance — Показать баланс\n"
        "/profile — Профиль\n"
        "/help — Помощь\n\n"

        "💰 Пополнение выполняется "
        "через Telegram Stars."
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

        f"💰 <b>Ваш баланс</b>\n\n"

        f"⭐ <b>{balance:.2f}</b>\n\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>",

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

        f"Имя: <b>{first_name}</b>\n"

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

        f"📎 <b>Ваша реферальная ссылка</b>\n\n"

        f"<code>{ref_link}</code>\n\n"

        f"💡 Приглашайте друзей "
        f"и получайте по <b>10 ⭐</b>.",

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

        f"🆔 <code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Рефералов: "
        f"<b>{ref_count}</b>\n\n"

        f"📎 <code>{ref_link}</code>",

        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# TELEGRAM STARS — СОЗДАНИЕ ПЛАТЕЖА
# =====================================================

async def create_stars_invoice(
    user_id: int,
    stars: int
):

    if stars <= 0:
        return

    payload = json.dumps({
        "type": "balance",
        "user_id": user_id,
        "stars": stars
    })

    await bot.send_invoice(

        chat_id=user_id,

        title=f"Пополнение баланса на {stars} ⭐",

        description=(
            f"Пополнение внутреннего баланса "
            f"White Bear на {stars} Telegram Stars."
        ),

        payload=payload,

        currency="XTR",

        prices=[
            LabeledPrice(
                label=f"{stars} ⭐",
                amount=stars
            )
        ]
    )


# =====================================================
# CALLBACK ДЛЯ ПОПОЛНЕНИЯ
# =====================================================

@dp.callback_query(
    lambda c: c.data.startswith("deposit_")
)
async def deposit_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    try:

        stars = int(
            callback.data.replace(
                "deposit_",
                ""
            )
        )

    except ValueError:

        await callback.answer(
            "❌ Некорректная сумма",
            show_alert=True
        )

        return

    if stars < 1:

        await callback.answer(
            "❌ Некорректная сумма",
            show_alert=True
        )

        return

    if stars > 100000:

        await callback.answer(
            "❌ Слишком большая сумма",
            show_alert=True
        )

        return

    try:

        await create_stars_invoice(
            user_id,
            stars
        )

        await callback.answer()

    except Exception as e:

        logger.exception(
            "Ошибка создания invoice"
        )

        await callback.answer(
            "❌ Не удалось создать платеж",
            show_alert=True
        )


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def process_pre_checkout(
    pre_checkout_query: types.PreCheckoutQuery
):

    try:

        payload = json.loads(
            pre_checkout_query.invoice_payload
        )

        if payload.get("type") != "balance":

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный платеж."
            )

            return

        user_id = payload.get("user_id")

        stars = payload.get("stars")

        if user_id != pre_checkout_query.from_user.id:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Пользователь платежа не совпадает."
            )

            return

        if not isinstance(stars, int) or stars <= 0:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректная сумма."
            )

            return

        if pre_checkout_query.currency != "XTR":

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректная валюта."
            )

            return

        if pre_checkout_query.total_amount != stars:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректная стоимость платежа."
            )

            return

        await pre_checkout_query.answer(
            ok=True
        )

    except Exception as e:

        logger.exception(
            "Ошибка pre_checkout"
        )

        await pre_checkout_query.answer(
            ok=False,
            error_message="Ошибка проверки платежа."
        )


# =====================================================
# УСПЕШНЫЙ ПЛАТЕЖ
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

    charge_id = payment.telegram_payment_charge_id

    if payment_exists(charge_id):

        logger.warning(
            f"Повторный платеж: {charge_id}"
        )

        await message.answer(
            "⚠️ Этот платеж уже был обработан."
        )

        return

    try:

        payload = json.loads(
            payment.invoice_payload
        )

        stars = int(
            payload.get("stars", 0)
        )

    except Exception:

        await message.answer(
            "❌ Ошибка обработки платежа."
        )

        return

    if stars <= 0:

        await message.answer(
            "❌ Некорректная сумма платежа."
        )

        return

    # Проверяем, что сумма действительно совпадает
    if payment.total_amount != stars:

        logger.error(
            "Несовпадение суммы платежа"
        )

        await message.answer(
            "❌ Ошибка суммы платежа."
        )

        return

    # Сохраняем платеж ДО начисления
    save_payment(
        user_id=user_id,
        telegram_charge_id=charge_id,
        provider_charge_id=(
            payment.provider_payment_charge_id
        ),
        stars=stars
    )

    # Начисляем звезды на внутренний баланс
    new_balance = add_balance(
        user_id,
        stars
    )

    logger.info(
        f"💰 Пользователь {user_id} "
        f"пополнил баланс на {stars} ⭐. "
        f"Новый баланс: {new_balance}"
    )

    await message.answer(

        f"✅ <b>Оплата успешно получена!</b>\n\n"

        f"💰 Зачислено: "
        f"<b>+{stars} ⭐</b>\n"

        f"💳 Ваш баланс: "
        f"<b>{new_balance:.2f} ⭐</b>\n\n"

        f"🎮 Можете возвращаться "
        f"в приложение."
    )


# =====================================================
# WEB APP DATA
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

        action = data.get("action")

        # ---------------------------------------------
        # GET BALANCE
        # ---------------------------------------------

        if action == "getBalance":

            balance = get_balance(user_id)

            await message.answer(
                json.dumps({
                    "success": True,
                    "balance": balance
                })
            )

        # ---------------------------------------------
        # GET PROFILE
        # ---------------------------------------------

        elif action == "getProfile":

            user = get_user(user_id)

            if not user:

                create_user(
                    user_id,
                    message.from_user.username,
                    message.from_user.first_name
                )

            balance = get_balance(user_id)

            ref_count = get_referrals_count(
                user_id
            )

            await message.answer(
                json.dumps({
                    "success": True,
                    "user_id": user_id,
                    "first_name": message.from_user.first_name,
                    "username": message.from_user.username,
                    "balance": balance,
                    "referrals": ref_count
                })
            )

        # ---------------------------------------------
        # GET REFERRAL LINK
        # ---------------------------------------------

        elif action == "getReferralLink":

            ref_link = get_referral_link(
                user_id
            )

            await message.answer(
                json.dumps({
                    "success": True,
                    "referral_link": ref_link
                })
            )

        # ---------------------------------------------
        # CREATE PAYMENT
        # ---------------------------------------------

        elif action == "createPayment":

            stars = data.get("stars")

            try:
                stars = int(stars)
            except:
                stars = 0

            if stars < 1:

                await message.answer(
                    json.dumps({
                        "success": False,
                        "error": "Некорректная сумма"
                    })
                )

                return

            if stars > 100000:

                await message.answer(
                    json.dumps({
                        "success": False,
                        "error": "Слишком большая сумма"
                    })
                )

                return

            await create_stars_invoice(
                user_id,
                stars
            )

            await message.answer(
                json.dumps({
                    "success": True,
                    "stars": stars
                })
            )

        # ---------------------------------------------
        # ADD REFERRAL
        # ---------------------------------------------

        elif action == "addReferral":

            referrer_id = data.get(
                "referrer_id"
            )

            referred_id = data.get(
                "referred_id"
            )

            if not referrer_id:

                await message.answer(
                    json.dumps({
                        "success": False,
                        "error": "Нет referrer_id"
                    })
                )

                return

            # ВАЖНО:
            # referred_id НЕ доверяем данным сайта.
            # Берем настоящий Telegram ID.
            referred_id = user_id

            success = add_referral(
                int(referrer_id),
                referred_id,
                10.0
            )

            if success:

                await message.answer(
                    json.dumps({
                        "success": True,
                        "reward": 10
                    })
                )

            else:

                await message.answer(
                    json.dumps({
                        "success": False,
                        "error": "Реферал уже существует"
                    })
                )

        else:

            await message.answer(
                json.dumps({
                    "success": False,
                    "error": "Unknown action"
                })
            )

    except json.JSONDecodeError:

        await message.answer(
            json.dumps({
                "success": False,
                "error": "Invalid JSON"
            })
        )

    except Exception as e:

        logger.exception(
            "Ошибка WebApp"
        )

        await message.answer(
            json.dumps({
                "success": False,
                "error": "Server error"
            })
        )


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
        "✅ Команды и кнопка Mini App установлены"
    )


# =====================================================
# ЗАПУСК
# =====================================================

async def main():

    logger.info(
        "🚀 White Bear Bot запускается..."
    )

    init_db()

    await set_commands_and_menu()

    logger.info(
        "✅ Бот готов"
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


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