import asyncio
import logging
import sys
import sqlite3
import secrets
import json
from datetime import datetime

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

# !!! ВСТАВЬ СЮДА НОВЫЙ ТОКЕН ОТ @BotFather !!!
BOT_TOKEN = "ВСТАВЬ_НОВЫЙ_BOT_TOKEN"

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

    # -------------------------------------------------
    # USERS
    # -------------------------------------------------

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

    # -------------------------------------------------
    # REFERRALS
    # -------------------------------------------------

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

    # -------------------------------------------------
    # PAYMENTS
    #
    # Храним Telegram charge_id.
    # Это защищает от повторного начисления.
    # -------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT UNIQUE NOT NULL,
            provider_payment_charge_id TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            payload TEXT,
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


def update_user_info(
    user_id: int,
    username: str = None,
    first_name: str = None
):

    conn = get_connection()
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

    current_balance = get_balance(user_id)

    new_balance = current_balance + amount

    new_balance = round(new_balance, 2)

    update_balance(
        user_id,
        new_balance
    )

    return new_balance


def remove_balance(user_id: int, amount: float):

    current_balance = get_balance(user_id)

    new_balance = current_balance - amount

    if new_balance < 0:
        return False

    new_balance = round(new_balance, 2)

    update_balance(
        user_id,
        new_balance
    )

    return True


# =====================================================
# РЕФЕРАЛЫ
# =====================================================

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

    # Начисляем обоим пользователям
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

    conn = get_connection()
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


# =====================================================
# ПЛАТЕЖИ
# =====================================================

def payment_exists(
    telegram_payment_charge_id: str
) -> bool:

    conn = get_connection()
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
    user_id: int,
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str,
    amount: float,
    currency: str,
    payload: str
):

    conn = get_connection()
    cursor = conn.cursor()

    try:

        cursor.execute("""
            INSERT INTO payments (
                user_id,
                telegram_payment_charge_id,
                provider_payment_charge_id,
                amount,
                currency,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            telegram_payment_charge_id,
            provider_payment_charge_id,
            amount,
            currency,
            payload
        ))

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        conn.rollback()

        return False

    finally:

        conn.close()


# =====================================================
# ЦЕНЫ ПОПОЛНЕНИЯ
# =====================================================

STAR_PACKAGES = {
    100: 100,
    250: 250,
    500: 500,
    1000: 1000,
}


# =====================================================
# КЛАВИАТУРА ПОПОЛНЕНИЯ
# =====================================================

def get_deposit_keyboard():

    builder = InlineKeyboardBuilder()

    builder.button(
        text="⭐ 100",
        callback_data="deposit_100"
    )

    builder.button(
        text="⭐ 250",
        callback_data="deposit_250"
    )

    builder.button(
        text="⭐ 500",
        callback_data="deposit_500"
    )

    builder.button(
        text="⭐ 1000",
        callback_data="deposit_1000"
    )

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_start"
    )

    builder.adjust(2, 2, 1)

    return builder.as_markup()


# =====================================================
# ОСНОВНАЯ КЛАВИАТУРА
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
        text="⭐ Пополнить",
        callback_data="deposit"
    )

    builder.button(
        text="📎 Реферальная ссылка",
        callback_data="referral"
    )

    builder.adjust(1, 2, 1, 1)

    return builder.as_markup()


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

    # -------------------------------------------------
    # Создаём пользователя
    # -------------------------------------------------

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

        # -------------------------------------------------
        # Реферал
        # -------------------------------------------------

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
                        f"Не удалось уведомить "
                        f"реферера: {e}"
                    )

    else:

        update_user_info(
            user_id,
            username,
            first_name
        )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

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

        f"💡 Приглашайте друзей "
        f"и получайте по 10 ⭐ за каждого!\n\n"

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
        f"{ref_count}",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить",
                        callback_data="deposit"
                    )
                ]
            ]
        )
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
        "/help — Эта справка\n\n"

        "⭐ Пополнение выполняется "
        "через Telegram Stars.\n\n"

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
# CALLBACK: DEPOSIT
# =====================================================

@dp.callback_query(
    lambda c: c.data == "deposit"
)
async def deposit_callback(
    callback: types.CallbackQuery
):

    balance = get_balance(
        callback.from_user.id
    )

    await callback.message.edit_text(

        f"⭐ <b>Пополнение баланса</b>\n\n"

        f"Ваш баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"

        f"Выберите сумму пополнения:",

        reply_markup=get_deposit_keyboard()
    )

    await callback.answer()


# =====================================================
# СОЗДАНИЕ INVOICE
# =====================================================

async def create_stars_invoice(
    user_id: int,
    amount: int
):

    if amount not in STAR_PACKAGES:

        return False

    # -------------------------------------------------
    # Payload
    # -------------------------------------------------

    payload_data = {
        "type": "balance_deposit",
        "user_id": user_id,
        "amount": amount,
        "created": int(
            datetime.now().timestamp()
        )
    }

    payload = json.dumps(
        payload_data,
        separators=(",", ":")
    )

    # -------------------------------------------------
    # Invoice
    #
    # Для Telegram Stars:
    # currency = XTR
    # provider_token = ""
    # -------------------------------------------------

    prices = [
        LabeledPrice(
            label=f"Пополнение на {amount} ⭐",
            amount=amount
        )
    ]

    await bot.send_invoice(

        chat_id=user_id,

        title=f"Пополнение на {amount} ⭐",

        description=(
            f"Пополнение игрового баланса "
            f"на {amount} Telegram Stars."
        ),

        payload=payload,

        currency="XTR",

        prices=prices,

        provider_token="",

        start_parameter=f"deposit_{amount}"
    )

    return True


# =====================================================
# CALLBACK: ВЫБОР СУММЫ
# =====================================================

@dp.callback_query(
    lambda c: c.data.startswith("deposit_")
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
            "❌ Некорректная сумма",
            show_alert=True
        )

        return

    if amount not in STAR_PACKAGES:

        await callback.answer(
            "❌ Такая сумма недоступна",
            show_alert=True
        )

        return

    user_id = callback.from_user.id

    try:

        await create_stars_invoice(
            user_id,
            amount
        )

        await callback.answer(
            "⭐ Счёт создан!"
        )

    except Exception as e:

        logger.exception(
            f"Ошибка создания invoice: {e}"
        )

        await callback.answer(
            "❌ Не удалось создать счёт",
            show_alert=True
        )


# =====================================================
# PRE-CHECKOUT
#
# Telegram спрашивает:
# "Можно ли провести эту оплату?"
#
# Мы проверяем payload и сумму.
# =====================================================

@dp.pre_checkout_query()
async def pre_checkout_handler(
    query: PreCheckoutQuery
):

    try:

        payload = json.loads(
            query.invoice_payload
        )

        if payload.get("type") != "balance_deposit":

            await query.answer(
                ok=False,
                error_message="Некорректный платёж."
            )

            return

        user_id = int(
            payload.get("user_id")
        )

        amount = int(
            payload.get("amount")
        )

        # Проверяем пользователя
        if user_id != query.from_user.id:

            await query.answer(
                ok=False,
                error_message="Пользователь платежа не совпадает."
            )

            return

        # Проверяем сумму
        if amount not in STAR_PACKAGES:

            await query.answer(
                ok=False,
                error_message="Некорректная сумма."
            )

            return

        # Проверяем валюту
        if query.currency != "XTR":

            await query.answer(
                ok=False,
                error_message="Некорректная валюта."
            )

            return

        # Проверяем стоимость
        if query.total_amount != amount:

            await query.answer(
                ok=False,
                error_message="Некорректная стоимость."
            )

            return

        await query.answer(
            ok=True
        )

        logger.info(
            f"✅ PreCheckout OK | "
            f"user={user_id} | "
            f"amount={amount}"
        )

    except Exception as e:

        logger.exception(
            f"Ошибка pre_checkout: {e}"
        )

        try:

            await query.answer(
                ok=False,
                error_message="Ошибка проверки платежа."
            )

        except Exception:
            pass


# =====================================================
# SUCCESSFUL PAYMENT
#
# НАСТОЯЩЕЕ НАЧИСЛЕНИЕ БАЛАНСА
# происходит именно здесь.
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

    # -------------------------------------------------
    # Данные платежа
    # -------------------------------------------------

    telegram_charge_id = (
        payment.telegram_payment_charge_id
    )

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    amount = payment.total_amount

    currency = payment.currency

    payload = payment.invoice_payload

    logger.info(
        f"💰 Получен успешный платеж | "
        f"user={user_id} | "
        f"amount={amount} | "
        f"currency={currency} | "
        f"charge={telegram_charge_id}"
    )

    # -------------------------------------------------
    # Проверяем валюту
    # -------------------------------------------------

    if currency != "XTR":

        logger.error(
            f"❌ Некорректная валюта: {currency}"
        )

        return

    # -------------------------------------------------
    # Защита от повторной оплаты
    # -------------------------------------------------

    if payment_exists(
        telegram_charge_id
    ):

        logger.warning(
            f"⚠️ Платёж уже обработан: "
            f"{telegram_charge_id}"
        )

        await message.answer(
            "ℹ️ Этот платёж уже был зачислен."
        )

        return

    # -------------------------------------------------
    # Проверяем payload
    # -------------------------------------------------

    try:

        payload_data = json.loads(
            payload
        )

        payload_type = payload_data.get(
            "type"
        )

        payload_user_id = int(
            payload_data.get("user_id")
        )

        payload_amount = int(
            payload_data.get("amount")
        )

    except Exception as e:

        logger.error(
            f"❌ Ошибка payload: {e}"
        )

        return

    # -------------------------------------------------
    # Проверяем тип
    # -------------------------------------------------

    if payload_type != "balance_deposit":

        logger.error(
            f"❌ Неизвестный тип платежа: "
            f"{payload_type}"
        )

        return

    # -------------------------------------------------
    # Проверяем пользователя
    # -------------------------------------------------

    if payload_user_id != user_id:

        logger.error(
            f"❌ User ID не совпадает: "
            f"{payload_user_id} != {user_id}"
        )

        return

    # -------------------------------------------------
    # Проверяем сумму
    # -------------------------------------------------

    if payload_amount != amount:

        logger.error(
            f"❌ Сумма не совпадает: "
            f"{payload_amount} != {amount}"
        )

        return

    # -------------------------------------------------
    # Проверяем допустимую сумму
    # -------------------------------------------------

    if amount not in STAR_PACKAGES:

        logger.error(
            f"❌ Недопустимая сумма: {amount}"
        )

        return

    # -------------------------------------------------
    # Сначала сохраняем платёж
    #
    # Это важно для защиты от повторного
    # начисления.
    # -------------------------------------------------

    saved = save_payment(

        user_id=user_id,

        telegram_payment_charge_id=(
            telegram_charge_id
        ),

        provider_payment_charge_id=(
            provider_charge_id
        ),

        amount=amount,

        currency=currency,

        payload=payload
    )

    if not saved:

        logger.warning(
            "⚠️ Платёж уже существует."
        )

        await message.answer(
            "ℹ️ Этот платёж уже был обработан."
        )

        return

    # -------------------------------------------------
    # НАЧИСЛЯЕМ БАЛАНС
    # -------------------------------------------------

    new_balance = add_balance(
        user_id,
        amount
    )

    logger.info(
        f"✅ Баланс пополнен | "
        f"user={user_id} | "
        f"+{amount} ⭐ | "
        f"new_balance={new_balance}"
    )

    # -------------------------------------------------
    # Сообщение пользователю
    # -------------------------------------------------

    await message.answer(

        f"🎉 <b>Оплата прошла успешно!</b>\n\n"

        f"⭐ Зачислено: "
        f"<b>+{amount} ⭐</b>\n\n"

        f"💰 Новый баланс: "
        f"<b>{new_balance:.2f} ⭐</b>\n\n"

        f"Спасибо за пополнение! 🐻‍❄️",

        reply_markup=get_main_keyboard()
    )


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

        f"📎 <b>Ваша реферальная ссылка</b>\n\n"

        f"<code>{ref_link}</code>\n\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>\n\n"

        f"💡 Приглашайте друзей "
        f"и получайте по <b>10 ⭐</b> "
        f"за каждого!",

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
# COPY REFERRAL
# =====================================================

@dp.callback_query(
    lambda c: c.data.startswith("copy_ref_")
)
async def copy_ref_callback(
    callback: types.CallbackQuery
):

    try:

        user_id = int(
            callback.data.replace(
                "copy_ref_",
                ""
            )
        )

    except ValueError:

        await callback.answer(
            "❌ Ошибка",
            show_alert=True
        )

        return

    ref_link = get_referral_link(
        user_id
    )

    await callback.answer(
        f"Ссылка:\n{ref_link}",
        show_alert=True
    )


# =====================================================
# BACK TO START
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

        f"🎮 Нажмите кнопку ниже, "
        f"чтобы открыть игры.",

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

        data = json.loads(
            message.web_app_data.data
        )

        user_id = message.from_user.id

        action = data.get("action")

        # -------------------------------------------------
        # Получить баланс
        # -------------------------------------------------

        if action == "getBalance":

            balance = get_balance(
                user_id
            )

            await message.answer(
                f"{balance:.2f}"
            )

        # -------------------------------------------------
        # Получить реферальную ссылку
        # -------------------------------------------------

        elif action == "getReferralLink":

            ref_link = get_referral_link(
                user_id
            )

            await message.answer(
                ref_link
            )

        # -------------------------------------------------
        # Открыть оплату
        # -------------------------------------------------

        elif action == "buyStars":

            amount = data.get(
                "amount"
            )

            try:

                amount = int(amount)

            except:

                await message.answer(
                    "❌ Некорректная сумма."
                )

                return

            if amount not in STAR_PACKAGES:

                await message.answer(
                    "❌ Такая сумма недоступна."
                )

                return

            await create_stars_invoice(
                user_id,
                amount
            )

        # -------------------------------------------------
        # РЕФЕРАЛ
        # -------------------------------------------------

        elif action == "addReferral":

            referrer_id = data.get(
                "referrer_id"
            )

            referred_id = data.get(
                "referred_id"
            )

            if referrer_id and referred_id:

                referrer_id = int(
                    referrer_id
                )

                referred_id = int(
                    referred_id
                )

                # Не позволяем добавлять
                # реферал самому себе.

                if referrer_id == referred_id:

                    await message.answer(
                        "❌ Нельзя пригласить самого себя."
                    )

                    return

                success = add_referral(
                    referrer_id,
                    referred_id,
                    10.0
                )

                if success:

                    await message.answer(
                        "🎉 Реферал засчитан! "
                        "+10 ⭐"
                    )

                else:

                    await message.answer(
                        "❌ Этот реферал "
                        "уже был засчитан."
                    )

    except json.JSONDecodeError:

        await message.answer(
            "❌ Ошибка обработки данных."
        )

    except Exception as e:

        logger.exception(
            f"Ошибка WebApp: {e}"
        )

        await message.answer(
            "❌ Произошла ошибка."
        )


# =====================================================
# КОМАНДЫ И КНОПКА MENU
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
        "🚀 Запуск White Bear Bot..."
    )

    # База
    init_db()

    # Команды
    await set_commands_and_menu()

    logger.info(
        "⭐ Telegram Stars payments готовы"
    )

    logger.info(
        "🌐 WebApp: %s",
        WEBAPP_URL
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