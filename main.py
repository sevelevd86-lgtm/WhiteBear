import asyncio
import logging
import sys
import sqlite3
import secrets

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

BOT_TOKEN = "8918284594:AAFLxOg1eEx4JS6z6V9wHr-t8T3Q9Qwiepg"

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
            FOREIGN KEY (referrer_id) REFERENCES users(user_id),
            FOREIGN KEY (referred_id) REFERENCES users(user_id)
        )
    """)

    # Таблица платежей.
    # Нужна, чтобы один successful_payment
    # не был начислен повторно.
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
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    exists = cursor.fetchone()

    if exists:
        cursor.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (amount, user_id)
        )
    else:
        cursor.execute(
            "INSERT INTO users (user_id, balance) VALUES (?, ?)",
            (user_id, amount)
        )

    conn.commit()
    conn.close()


def add_balance(user_id: int, amount: float):
    current_balance = get_balance(user_id)

    new_balance = current_balance + amount

    # Округляем до 2 знаков
    new_balance = round(new_balance, 2)

    update_balance(user_id, new_balance)

    return new_balance


# =====================================================
# РЕФЕРАЛЬНАЯ СИСТЕМА
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

def payment_exists(telegram_payment_charge_id: str) -> bool:
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
    telegram_payment_charge_id: str,
    provider_payment_charge_id: str,
    user_id: int,
    amount: int,
    currency: str
):
    conn = get_connection()
    cursor = conn.cursor()

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
    conn.close()


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
        text="⭐ Пополнить баланс",
        callback_data="deposit"
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

    # Минимальное пополнение — 1 ⭐
    builder.button(
        text="⭐ 1",
        callback_data="deposit_amount_1"
    )

    builder.button(
        text="⭐ 10",
        callback_data="deposit_amount_10"
    )

    builder.button(
        text="⭐ 50",
        callback_data="deposit_amount_50"
    )

    builder.button(
        text="⭐ 100",
        callback_data="deposit_amount_100"
    )

    builder.button(
        text="⭐ 250",
        callback_data="deposit_amount_250"
    )

    builder.button(
        text="⭐ 500",
        callback_data="deposit_amount_500"
    )

    builder.button(
        text="✏️ Другая сумма",
        callback_data="deposit_custom"
    )

    builder.button(
        text="🔙 Назад",
        callback_data="back_to_start"
    )

    builder.adjust(3, 3, 1, 1)

    return builder.as_markup()


# =====================================================
# СОЗДАНИЕ INVOICE
# =====================================================

async def send_stars_invoice(
    user_id: int,
    amount: int
):
    """
    Создаёт настоящий Telegram Stars invoice.

    currency = XTR
    provider_token НЕ нужен.
    """

    if amount < 1:
        return False

    if amount > 100000:
        return False

    prices = [
        LabeledPrice(
            label=f"Пополнение баланса на {amount} ⭐",
            amount=amount
        )
    ]

    payload = f"deposit:{user_id}:{amount}"

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

        start_parameter=f"deposit_{amount}",

        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,

        is_flexible=False
    )

    return True


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

                referrer_id = get_user_by_ref_code(ref_code)

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
                        f"💰 Вы получили +10 ⭐\n"
                        f"📊 Всего приглашено: "
                        f"{get_referrals_count(invited_by)}"
                    )

                except Exception as e:

                    logger.error(
                        f"Ошибка уведомления: {e}"
                    )

    balance = get_balance(user_id)

    ref_count = get_referrals_count(user_id)

    ref_link = get_referral_link(user_id)

    await message.answer(

        f"🐻‍❄️ <b>Добро пожаловать в DROP!</b>\n\n"

        f"👤 Имя: <b>{first_name}</b>\n"

        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>\n\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>\n\n"

        f"🎮 Нажмите «Открыть игры», "
        f"чтобы перейти на сайт.",

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
        f"{ref_count}"
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

        f"Имя: {first_name}\n"

        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"

        f"👥 Приглашено: "
        f"{ref_count}\n\n"

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
# ПОПОЛНЕНИЕ
# =====================================================

@dp.callback_query(
    lambda c: c.data == "deposit"
)
async def deposit_callback(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(

        "⭐ <b>Пополнение баланса</b>\n\n"

        "Выберите количество Telegram Stars.\n\n"

        "Минимальное пополнение — <b>1 ⭐</b>.\n\n"

        "После успешной оплаты Stars "
        "автоматически зачислятся "
        "на ваш баланс.",

        reply_markup=get_deposit_keyboard()
    )

    await callback.answer()


# =====================================================
# ФИКСИРОВАННЫЕ СУММЫ
# =====================================================

@dp.callback_query(
    lambda c: c.data.startswith("deposit_amount_")
)
async def deposit_amount_callback(
    callback: types.CallbackQuery
):

    try:

        amount = int(
            callback.data.replace(
                "deposit_amount_",
                ""
            )
        )

    except ValueError:

        await callback.answer(
            "❌ Некорректная сумма",
            show_alert=True
        )

        return

    if amount < 1:

        await callback.answer(
            "❌ Минимум 1 ⭐",
            show_alert=True
        )

        return

    try:

        await send_stars_invoice(
            callback.from_user.id,
            amount
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


# =====================================================
# ДРУГАЯ СУММА
# =====================================================

@dp.callback_query(
    lambda c: c.data == "deposit_custom"
)
async def deposit_custom_callback(
    callback: types.CallbackQuery
):

    await callback.message.answer(

        "✏️ <b>Введите сумму пополнения</b>\n\n"
        "Например:\n"
        "<code>1</code>\n"
        "<code>25</code>\n"
        "<code>100</code>\n\n"
        "Минимум: <b>1 ⭐</b>\n"
        "Максимум: <b>100000 ⭐</b>"
    )

    await callback.answer()


# =====================================================
# ВВОД СУММЫ
# =====================================================

@dp.message()
async def general_message_handler(
    message: Message
):

    # Если это успешная оплата,
    # Telegram обработает её отдельным handler ниже.
    if message.successful_payment:
        return

    text = message.text

    if not text:
        return

    # Если пользователь вводит число —
    # считаем это суммой пополнения.
    if text.isdigit():

        amount = int(text)

        if amount < 1:

            await message.answer(
                "❌ Минимальное пополнение — 1 ⭐"
            )

            return

        if amount > 100000:

            await message.answer(
                "❌ Максимальное пополнение — 100000 ⭐"
            )

            return

        try:

            await send_stars_invoice(
                message.from_user.id,
                amount
            )

        except Exception as e:

            logger.exception(
                "Ошибка invoice"
            )

            await message.answer(
                "❌ Не удалось создать оплату."
            )

        return


# =====================================================
# PRE-CHECKOUT
# =====================================================

@dp.pre_checkout_query()
async def process_pre_checkout_query(
    pre_checkout_query: PreCheckoutQuery
):

    try:

        payload = pre_checkout_query.invoice_payload

        parts = payload.split(":")

        if len(parts) != 3:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный платёж."
            )

            return

        action = parts[0]

        user_id = int(parts[1])

        amount = int(parts[2])

        # Проверяем, что платёж относится
        # к тому же Telegram пользователю.
        if user_id != pre_checkout_query.from_user.id:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Пользователь платежа не совпадает."
            )

            return

        if action != "deposit":

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный тип платежа."
            )

            return

        if amount < 1:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Минимальная сумма — 1 ⭐."
            )

            return

        # Для Stars сумма приходит в smallest unit.
        # Для XTR это целое количество Stars.
        if pre_checkout_query.total_amount != amount:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Сумма платежа не совпадает."
            )

            return

        await pre_checkout_query.answer(
            ok=True
        )

        logger.info(
            f"PreCheckout OK: "
            f"user={user_id}, "
            f"amount={amount}"
        )

    except Exception as e:

        logger.exception(
            "Ошибка pre_checkout"
        )

        try:

            await pre_checkout_query.answer(
                ok=False,
                error_message="Ошибка проверки платежа."
            )

        except Exception:
            pass


# =====================================================
# УСПЕШНАЯ ОПЛАТА
# =====================================================

@dp.message(
    lambda message: message.successful_payment is not None
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
        f"УСПЕШНАЯ ОПЛАТА: "
        f"user={user_id}, "
        f"amount={amount}, "
        f"currency={currency}, "
        f"charge={telegram_charge_id}"
    )

    # -------------------------------------------------
    # Проверяем, не было ли уже начисление
    # -------------------------------------------------

    if payment_exists(
        telegram_charge_id
    ):

        logger.warning(
            f"Повторный платёж: "
            f"{telegram_charge_id}"
        )

        await message.answer(
            "⚠️ Этот платёж уже был обработан."
        )

        return

    # -------------------------------------------------
    # Проверяем валюту
    # -------------------------------------------------

    if currency != "XTR":

        logger.error(
            f"Неверная валюта платежа: {currency}"
        )

        await message.answer(
            "❌ Неверная валюта платежа."
        )

        return

    # -------------------------------------------------
    # Минимум 1 ⭐
    # -------------------------------------------------

    if amount < 1:

        await message.answer(
            "❌ Некорректная сумма платежа."
        )

        return

    # -------------------------------------------------
    # Сохраняем платёж
    # -------------------------------------------------

    try:

        save_payment(
            telegram_charge_id=telegram_charge_id,
            provider_payment_charge_id=provider_charge_id,
            user_id=user_id,
            amount=amount,
            currency=currency
        )

    except sqlite3.IntegrityError:

        logger.warning(
            "Платёж уже существует."
        )

        await message.answer(
            "⚠️ Этот платёж уже был обработан."
        )

        return

    # -------------------------------------------------
    # НАЧИСЛЯЕМ STARS НА БАЛАНС
    # -------------------------------------------------

    new_balance = add_balance(
        user_id,
        amount
    )

    # -------------------------------------------------
    # ОТПРАВЛЯЕМ ПОДТВЕРЖДЕНИЕ
    # -------------------------------------------------

    await message.answer(

        f"✅ <b>Оплата успешно получена!</b>\n\n"

        f"⭐ Зачислено: "
        f"<b>+{amount} ⭐</b>\n\n"

        f"💰 Новый баланс: "
        f"<b>{new_balance:.2f} ⭐</b>\n\n"

        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>"
    )

    logger.info(
        f"Баланс пользователя {user_id} "
        f"увеличен на {amount}. "
        f"Новый баланс: {new_balance}"
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

        f"💰 <b>Ваш баланс</b>\n\n"

        f"⭐ Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"

        f"👥 Приглашено друзей: "
        f"<b>{ref_count}</b>",

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

        f"Имя: {first_name}\n\n"

        f"🆔 Telegram ID:\n"
        f"<code>{user_id}</code>\n\n"

        f"💰 Баланс:\n"
        f"<b>{balance:.2f} ⭐</b>\n\n"

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
# REFERRAL
# =====================================================

@dp.callback_query(
    lambda c: c.data == "referral"
)
async def referral_callback(
    callback: types.CallbackQuery
):

    user_id = callback.from_user.id

    ref_link = get_referral_link(user_id)

    count = get_referrals_count(user_id)

    await callback.message.edit_text(

        f"📎 <b>Реферальная система</b>\n\n"

        f"Ваша ссылка:\n"
        f"<code>{ref_link}</code>\n\n"

        f"👥 Приглашено: "
        f"<b>{count}</b>\n\n"

        f"🎁 За каждого приглашённого "
        f"начисляется 10 ⭐.",

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

    await callback.message.edit_text(

        f"🐻‍❄️ <b>White Bear DROP</b>\n\n"

        f"👤 {first_name}\n"

        f"🆔 ID: "
        f"<code>{user_id}</code>\n\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"

        f"👥 Приглашено: "
        f"<b>{ref_count}</b>",

        reply_markup=get_main_keyboard()
    )

    await callback.answer()


# =====================================================
# WEBAPP
# =====================================================

@dp.message(
    lambda msg: msg.web_app_data is not None
)
async def web_app_data_handler(
    message: Message
):

    # ВАЖНО:
    # Сайт больше НЕ может самостоятельно
    # менять баланс.
    #
    # Баланс меняется только после успешной
    # Telegram Stars оплаты.

    try:

        data = message.web_app_data.data

        logger.info(
            f"Получены данные WebApp "
            f"от {message.from_user.id}: {data}"
        )

    except Exception as e:

        logger.error(
            f"Ошибка WebApp: {e}"
        )


# =====================================================
# HELP
# =====================================================

@dp.message(Command("help"))
async def help_command(
    message: Message
):

    await message.answer(

        "📖 <b>Помощь</b>\n\n"

        "/start — Главное меню\n"
        "/game — Открыть игры\n"
        "/balance — Баланс\n"
        "/profile — Профиль\n"
        "/help — Помощь\n\n"

        "⭐ Пополнение осуществляется "
        "через Telegram Stars.\n\n"

        "Минимальное пополнение: "
        "<b>1 ⭐</b>."
    )


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
        "🚀 White Bear Bot запускается..."
    )

    init_db()

    await set_commands_and_menu()

    logger.info(
        "⭐ Telegram Stars включены"
    )

    logger.info(
        "💰 Минимальное пополнение: 1 ⭐"
    )

    logger.info(
        "🌐 WebApp: " + WEBAPP_URL
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


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