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
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

BOT_USERNAME = "White_Bear_ROBOT"

PORT = int(os.getenv("PORT", "8080"))

DB_NAME = os.getenv("DB_NAME", "users.db")

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"

WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"

WEBHOOK_URL = "https://whitebear.bothost.tech/webhook"

INIT_DATA_MAX_AGE = 86400


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

logger = logging.getLogger("white_bear")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не установлен.")


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_NAME,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0,
            username TEXT,
            first_name TEXT,
            ref_code TEXT UNIQUE,
            invited_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            reward REAL DEFAULT 10,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referrer_id, referred_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE NOT NULL,
            provider_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS game_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            amount REAL NOT NULL,
            game TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            promo_code TEXT NOT NULL,
            reward REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, promo_code)
        )
        """
    )

    conn.commit()
    conn.close()

    logger.info("✅ База данных инициализирована")


# ============================================================
# USERS
# ============================================================

def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    row = cur.fetchone()

    conn.close()

    return row


def create_user(
    user_id: int,
    username=None,
    first_name=None,
    invited_by=None,
):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    exists = cur.fetchone()

    if exists:
        cur.execute(
            """
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE user_id = ?
            """,
            (
                username,
                first_name,
                user_id,
            ),
        )

        conn.commit()
        conn.close()

        return

    while True:
        ref_code = secrets.token_hex(8)

        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE ref_code = ?
            """,
            (ref_code,),
        )

        if not cur.fetchone():
            break

    cur.execute(
        """
        INSERT INTO users (
            user_id,
            balance,
            username,
            first_name,
            ref_code,
            invited_by
        )
        VALUES (?, 0, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            first_name,
            ref_code,
            invited_by,
        ),
    )

    conn.commit()
    conn.close()

    logger.info("👤 Создан пользователь %s", user_id)


def get_balance(user_id: int) -> float:
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )

    row = cur.fetchone()

    conn.close()

    if not row:
        return 0.0

    return round(float(row["balance"]), 2)


# ============================================================
# TELEGRAM INIT DATA
# ============================================================

def validate_init_data(init_data: str):
    if not init_data:
        return None

    try:
        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        auth_date = data.get("auth_date")

        if not auth_date:
            return None

        auth_timestamp = int(auth_date)
        now = int(time.time())

        if now - auth_timestamp > INIT_DATA_MAX_AGE:
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            return None

        user_string = data.get("user")

        if not user_string:
            return None

        return json.loads(user_string)

    except Exception:
        logger.exception("❌ Ошибка проверки initData")
        return None


def get_webapp_user(request):
    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        "",
    ).strip()

    if not init_data:
        return None

    return validate_init_data(init_data)


# ============================================================
# BALANCE TRANSACTION
# ============================================================

def process_game_transaction(
    user_id: int,
    operation_id: str,
    operation_type: str,
    amount: float,
    game: str = "",
):
    amount = round(float(amount), 2)

    if amount <= 0:
        return {
            "ok": False,
            "error": "invalid_amount",
        }

    if operation_type not in ("deduct", "add"):
        return {
            "ok": False,
            "error": "invalid_operation",
        }

    conn = db()
    cur = conn.cursor()

    try:
        # Проверка повторной операции
        cur.execute(
            """
            SELECT *
            FROM game_transactions
            WHERE operation_id = ?
            """,
            (operation_id,),
        )

        existing = cur.fetchone()

        if existing:
            cur.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            )

            row = cur.fetchone()

            conn.close()

            if not row:
                return {
                    "ok": False,
                    "error": "user_not_found",
                }

            return {
                "ok": True,
                "duplicate": True,
                "balance": float(row["balance"]),
                "operation_id": operation_id,
            }

        # Пользователь
        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        user = cur.fetchone()

        if not user:
            conn.close()

            return {
                "ok": False,
                "error": "user_not_found",
            }

        current_balance = float(user["balance"])

        # Списание
        if operation_type == "deduct":
            if current_balance < amount:
                conn.close()

                return {
                    "ok": False,
                    "error": "insufficient_balance",
                    "balance": current_balance,
                }

            new_balance = round(
                current_balance - amount,
                2,
            )

        # Начисление
        else:
            new_balance = round(
                current_balance + amount,
                2,
            )

        cur.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
            """,
            (
                new_balance,
                user_id,
            ),
        )

        cur.execute(
            """
            INSERT INTO game_transactions (
                operation_id,
                user_id,
                operation_type,
                amount,
                game
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                user_id,
                operation_type,
                amount,
                game,
            ),
        )

        conn.commit()
        conn.close()

        logger.info(
            "🎮 TRANSACTION user=%s game=%s type=%s amount=%s balance=%s",
            user_id,
            game,
            operation_type,
            amount,
            new_balance,
        )

        return {
            "ok": True,
            "duplicate": False,
            "balance": new_balance,
            "amount": amount,
            "operation_id": operation_id,
        }

    except sqlite3.IntegrityError:
        conn.rollback()

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        row = cur.fetchone()

        conn.close()

        return {
            "ok": True,
            "duplicate": True,
            "balance": (
                float(row["balance"])
                if row
                else 0.0
            ),
            "operation_id": operation_id,
        }

    except Exception:
        conn.rollback()
        conn.close()

        logger.exception("❌ Ошибка транзакции")

        return {
            "ok": False,
            "error": "transaction_error",
        }


# ============================================================
# PROMOCODES
# ============================================================

PROMOCODES = {
    "200": 200,
    "met200": 200,
}


def activate_promo(user_id: int, code: str):
    code = str(code).strip()

    if code not in PROMOCODES:
        return {
            "ok": False,
            "error": "invalid_promo",
        }

    reward = PROMOCODES[code]

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id
            FROM promo_uses
            WHERE user_id = ?
              AND promo_code = ?
            """,
            (
                user_id,
                code,
            ),
        )

        if cur.fetchone():
            conn.close()

            return {
                "ok": False,
                "error": "promo_already_used",
            }

        cur.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                user_id,
            ),
        )

        if cur.rowcount == 0:
            conn.rollback()
            conn.close()

            return {
                "ok": False,
                "error": "user_not_found",
            }

        cur.execute(
            """
            INSERT INTO promo_uses (
                user_id,
                promo_code,
                reward
            )
            VALUES (?, ?, ?)
            """,
            (
                user_id,
                code,
                reward,
            ),
        )

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        row = cur.fetchone()

        conn.commit()
        conn.close()

        new_balance = float(row["balance"])

        logger.info(
            "🎁 PROMO user=%s code=%s reward=%s balance=%s",
            user_id,
            code,
            reward,
            new_balance,
        )

        return {
            "ok": True,
            "code": code,
            "reward": reward,
            "balance": new_balance,
        }

    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()

        return {
            "ok": False,
            "error": "promo_already_used",
        }

    except Exception:
        conn.rollback()
        conn.close()

        logger.exception("❌ Ошибка промокода")

        return {
            "ok": False,
            "error": "promo_error",
        }


# ============================================================
# REFERRALS
# ============================================================

def get_referrals_count(user_id: int):
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (user_id,),
    )

    result = cur.fetchone()

    conn.close()

    return int(result[0])


def get_referral_link(user_id: int):
    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )


def add_referral(
    referrer_id: int,
    referred_id: int,
):
    if referrer_id == referred_id:
        return False

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id
            FROM referrals
            WHERE referrer_id = ?
              AND referred_id = ?
            """,
            (
                referrer_id,
                referred_id,
            ),
        )

        if cur.fetchone():
            conn.close()
            return False

        cur.execute(
            """
            INSERT INTO referrals (
                referrer_id,
                referred_id,
                reward
            )
            VALUES (?, ?, 10)
            """,
            (
                referrer_id,
                referred_id,
            ),
        )

        cur.execute(
            """
            UPDATE users
            SET balance = balance + 10
            WHERE user_id = ?
            """,
            (referrer_id,),
        )

        cur.execute(
            """
            UPDATE users
            SET balance = balance + 10
            WHERE user_id = ?
            """,
            (referred_id,),
        )

        conn.commit()
        conn.close()

        return True

    except Exception:
        conn.rollback()
        conn.close()

        logger.exception("❌ Ошибка referral")

        return False


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🎮 Открыть игру",
        web_app=WebAppInfo(
            url=WEBAPP_URL,
        ),
    )

    builder.button(
        text="💰 Баланс",
        callback_data="balance",
    )

    builder.button(
        text="👤 Профиль",
        callback_data="profile",
    )

    builder.button(
        text="⭐ Пополнить",
        callback_data="deposit",
    )

    builder.button(
        text="📎 Реферал",
        callback_data="referral",
    )

    builder.adjust(
        1,
        2,
        2,
    )

    return builder.as_markup()


def deposit_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1",
                    callback_data="buy_1",
                ),
                InlineKeyboardButton(
                    text="⭐ 10",
                    callback_data="buy_10",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 50",
                    callback_data="buy_50",
                ),
                InlineKeyboardButton(
                    text="⭐ 100",
                    callback_data="buy_100",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="back",
                ),
            ],
        ],
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start(message: Message):
    user_id = message.from_user.id

    username = message.from_user.username
    first_name = message.from_user.first_name

    args = message.text.split()

    invited_by = None

    if not get_user(user_id):
        if (
            len(args) > 1
            and args[1].startswith("ref_")
        ):
            ref_code = args[1][4:]

            conn = db()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id = ?
                """,
                (ref_code,),
            )

            conn.close()

            try:
                invited_by = int(ref_code)
            except ValueError:
                invited_by = None

            if invited_by == user_id:
                invited_by = None

        create_user(
            user_id,
            username,
            first_name,
            invited_by,
        )

        if invited_by:
            if get_user(invited_by):
                if add_referral(
                    invited_by,
                    user_id,
                ):
                    try:
                        await bot.send_message(
                            invited_by,
                            "🎉 <b>Новый реферал!</b>\n\n"
                            "Вы получили <b>+10 ⭐</b>.",
                        )
                    except Exception:
                        pass
    else:
        create_user(
            user_id,
            username,
            first_name,
        )

    balance = get_balance(user_id)

    await message.answer(
        f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance:.2f} ⭐</b>\n\n"
        f"🎮 Открывайте игру кнопкой ниже.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# GAME
# ============================================================

@dp.message(Command("game"))
async def game_command(message: Message):
    await message.answer(
        "🎮 <b>White Bear Drop</b>\n\n"
        "Нажми кнопку ниже, чтобы открыть игру.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# BALANCE COMMAND
# ============================================================

@dp.message(Command("balance"))
async def balance_command(message: Message):
    user_id = message.from_user.id

    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    balance = get_balance(user_id)

    await message.answer(
        f"💰 Ваш баланс: "
        f"<b>{balance:.2f} ⭐</b>",
    )


# ============================================================
# PROFILE COMMAND
# ============================================================

@dp.message(Command("profile"))
async def profile_command(message: Message):
    user_id = message.from_user.id

    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
    )

    balance = get_balance(user_id)

    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить баланс",
                        callback_data="deposit",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игру",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL,
                        ),
                    ),
                ],
            ],
        ),
    )


# ============================================================
# DEPOSIT
# ============================================================

@dp.callback_query(F.data == "deposit")
async def deposit(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "⭐ <b>Пополнение баланса</b>\n\n"
        "Выберите количество Stars.\n\n"
        "После успешной оплаты сумма "
        "автоматически зачислится "
        "на игровой баланс.",
        reply_markup=deposit_keyboard(),
    )

    await callback.answer()


# ============================================================
# INVOICE
# ============================================================

async def create_invoice(
    user_id: int,
    amount: int,
):
    payload = (
        f"deposit:"
        f"{user_id}:"
        f"{amount}:"
        f"{secrets.token_hex(8)}"
    )

    await bot.send_invoice(
        chat_id=user_id,
        title=f"Пополнение {amount} ⭐",
        description=(
            f"Пополнение игрового баланса "
            f"на {amount} Telegram Stars."
        ),
        payload=payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{amount} Stars",
                amount=amount,
            ),
        ],
        provider_token="",
    )


@dp.callback_query(F.data.startswith("buy_"))
async def buy_stars(callback: types.CallbackQuery):
    try:
        amount = int(
            callback.data.replace(
                "buy_",
                "",
            )
        )

        if amount < 1:
            raise ValueError

        create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )

        await create_invoice(
            callback.from_user.id,
            amount,
        )

        await callback.answer()

    except Exception:
        logger.exception("❌ Ошибка invoice")

        await callback.answer(
            "❌ Не удалось создать оплату",
            show_alert=True,
        )


# ============================================================
# PRE CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    if query.currency != "XTR":
        await query.answer(
            ok=False,
            error_message="Неверная валюта.",
        )
        return

    if query.total_amount < 1:
        await query.answer(
            ok=False,
            error_message="Неверная сумма.",
        )
        return

    await query.answer(ok=True)


# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment

    user_id = message.from_user.id

    amount = int(payment.total_amount)

    charge_id = payment.telegram_payment_charge_id

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    if payment.currency != "XTR":
        return

    conn = db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id
            FROM payments
            WHERE telegram_charge_id = ?
            """,
            (charge_id,),
        )

        if cur.fetchone():
            conn.close()

            await message.answer(
                "ℹ️ Этот платеж уже был зачислен.",
            )

            return

        cur.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        if not cur.fetchone():
            while True:
                ref_code = secrets.token_hex(8)

                cur.execute(
                    """
                    SELECT user_id
                    FROM users
                    WHERE ref_code = ?
                    """,
                    (ref_code,),
                )

                if not cur.fetchone():
                    break

            cur.execute(
                """
                INSERT INTO users (
                    user_id,
                    balance,
                    username,
                    first_name,
                    ref_code
                )
                VALUES (?, 0, ?, ?, ?)
                """,
                (
                    user_id,
                    message.from_user.username,
                    message.from_user.first_name,
                    ref_code,
                ),
            )

        cur.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                amount,
                user_id,
            ),
        )

        cur.execute(
            """
            INSERT INTO payments (
                user_id,
                telegram_charge_id,
                provider_charge_id,
                amount,
                currency
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                charge_id,
                provider_charge_id,
                amount,
                "XTR",
            ),
        )

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        )

        row = cur.fetchone()

        new_balance = float(row["balance"])

        conn.commit()
        conn.close()

        await message.answer(
            f"✅ <b>Оплата получена!</b>\n\n"
            f"⭐ Оплачено: <b>{amount}</b>\n"
            f"💰 Начислено: <b>{amount} ⭐</b>\n"
            f"💳 Баланс: "
            f"<b>{new_balance:.2f} ⭐</b>",
        )

    except Exception:
        conn.rollback()
        conn.close()

        logger.exception(
            "❌ Ошибка successful payment"
        )


# ============================================================
# BALANCE CALLBACK
# ============================================================

@dp.callback_query(F.data == "balance")
async def balance_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    balance = get_balance(user_id)

    await callback.message.edit_text(
        f"💰 <b>Ваш баланс</b>\n\n"
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить",
                        callback_data="deposit",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back",
                    ),
                ],
            ],
        ),
    )

    await callback.answer()


# ============================================================
# PROFILE CALLBACK
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    balance = get_balance(user_id)

    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 Telegram ID: "
        f"<code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ Пополнить баланс",
                        callback_data="deposit",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игру",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL,
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back",
                    ),
                ],
            ],
        ),
    )

    await callback.answer()


# ============================================================
# REFERRAL
# ============================================================

@dp.callback_query(F.data == "referral")
async def referral_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    link = get_referral_link(user_id)

    count = get_referrals_count(user_id)

    await callback.message.edit_text(
        f"📎 <b>Реферальная система</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"👥 Приглашено: <b>{count}</b>\n"
        f"💰 Награда: <b>10 ⭐</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back",
                    ),
                ],
            ],
        ),
    )

    await callback.answer()


# ============================================================
# BACK
# ============================================================

@dp.callback_query(F.data == "back")
async def back_callback(
    callback: types.CallbackQuery,
):
    user_id = callback.from_user.id

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name,
    )

    balance = get_balance(user_id)

    await callback.message.edit_text(
        f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard(),
    )

    await callback.answer()


# ============================================================
# API HELPERS
# ============================================================

def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": (
            "Content-Type, X-Telegram-Init-Data"
        ),
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
    }


def json_response(data, status=200):
    return web.json_response(
        data,
        status=status,
        headers=cors_headers(),
    )


# ============================================================
# ROOT
# ============================================================

async def index(request):
    if not HTML_FILE.exists():
        return web.Response(
            text=(
                "White Bear API is running.\n"
                "index.html not found."
            ),
            headers=cors_headers(),
        )

    return web.FileResponse(
        HTML_FILE,
        headers=cors_headers(),
    )


# ============================================================
# HEALTH
# ============================================================

async def health(request):
    return json_response(
        {
            "ok": True,
            "status": "OK",
            "service": "White Bear",
            "port": PORT,
        }
    )


# ============================================================
# AUTH HELPER
# ============================================================

def authenticate_request(request):
    telegram_user = get_webapp_user(request)

    if not telegram_user:
        return None

    try:
        return int(telegram_user["id"])
    except Exception:
        return None


# ============================================================
# API USER
# ============================================================

async def api_user(request):
    user_id = authenticate_request(request)

    if not user_id:
        return json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data",
            },
            401,
        )

    telegram_user = get_webapp_user(request)

    create_user(
        user_id,
        telegram_user.get("username"),
        telegram_user.get("first_name"),
    )

    user = get_user(user_id)

    return json_response(
        {
            "ok": True,
            "user_id": user_id,
            "username": user["username"],
            "first_name": user["first_name"],
            "balance": float(user["balance"]),
            "ref_code": user["ref_code"],
        }
    )


# ============================================================
# API BALANCE
# ============================================================

async def api_balance(request):
    user_id = authenticate_request(request)

    if not user_id:
        return json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data",
            },
            401,
        )

    create_user(user_id)

    return json_response(
        {
            "ok": True,
            "user_id": user_id,
            "balance": get_balance(user_id),
        }
    )


# ============================================================
# API GAME TRANSACTION
# ============================================================

async def api_game_transaction(request):
    user_id = authenticate_request(request)

    if not user_id:
        return json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data",
            },
            401,
        )

    try:
        data = await request.json()
    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_json",
            },
            400,
        )

    try:
        requested_user_id = int(data.get("user_id"))
        amount = float(data.get("amount"))
        operation_id = str(
            data.get("operation_id", "")
        ).strip()

        operation_type = str(
            data.get("type", "")
        ).strip().lower()

        game = str(
            data.get("game", "")
        ).strip()

    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_data",
            },
            400,
        )

    if requested_user_id != user_id:
        return json_response(
            {
                "ok": False,
                "error": "user_mismatch",
            },
            403,
        )

    if operation_type not in (
        "deduct",
        "add",
    ):
        return json_response(
            {
                "ok": False,
                "error": "invalid_operation",
            },
            400,
        )

    if not operation_id:
        return json_response(
            {
                "ok": False,
                "error": "operation_id_required",
            },
            400,
        )

    if amount <= 0 or amount > 1000000:
        return json_response(
            {
                "ok": False,
                "error": "invalid_amount",
            },
            400,
        )

    telegram_user = get_webapp_user(request)

    create_user(
        user_id,
        telegram_user.get("username"),
        telegram_user.get("first_name"),
    )

    result = process_game_transaction(
        user_id=user_id,
        operation_id=operation_id,
        operation_type=operation_type,
        amount=amount,
        game=game,
    )

    if not result["ok"]:
        status = 400

        if result["error"] == "insufficient_balance":
            status = 402

        return json_response(
            result,
            status,
        )

    return json_response(result)


# ============================================================
# API DEDUCT
# ============================================================

async def api_balance_deduct(request):
    user_id = authenticate_request(request)

    if not user_id:
        return json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data",
            },
            401,
        )

    try:
        data = await request.json()
    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_json",
            },
            400,
        )

    try:
        requested_user_id = int(data.get("user_id"))
        amount = float(data.get("amount"))
        operation_id = str(
            data.get("operation_id", "")
        ).strip()

        game = str(
            data.get("game", "")
        ).strip()

    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_data",
            },
            400,
        )

    if requested_user_id != user_id:
        return json_response(
            {
                "ok": False,
                "error": "user_mismatch",
            },
            403,
        )

    if not operation_id:
        return json_response(
            {
                "ok": False,
                "error": "operation_id_required",
            },
            400,
        )

    if amount <= 0 or amount > 1000000:
        return json_response(
            {
                "ok": False,
                "error": "invalid_amount",
            },
            400,
        )

    create_user(user_id)

    result = process_game_transaction(
        user_id=user_id,
        operation_id=operation_id,
        operation_type="deduct",
        amount=amount,
        game=game,
    )

    if not result["ok"]:
        return json_response(
            result,
            402 if result["error"] == "insufficient_balance" else 400,
        )

    return json_response(result)


# ============================================================
# API ADD
# ============================================================

async def api_balance_add(request):
    user_id = authenticate_request(request)

    if not user_id:
        return json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data",
            },
            401,
        )

    try:
        data = await request.json()
    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_json",
            },
            400,
        )

    try:
        requested_user_id = int(data.get("user_id"))
        amount = float(data.get("amount"))
        operation_id = str(
            data.get("operation_id", "")
        ).strip()

        game = str(
            data.get("game", "")
        ).strip()

    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_data",
            },
            400,
        )

    if requested_user_id != user_id:
        return json_response(
            {
                "ok": False,
                "error": "user_mismatch",
            },
            403,
        )

    if not operation_id:
        return json_response(
            {
                "ok": False,
                "error": "operation_id_required",
            },
            400,
        )

    if amount <= 0 or amount > 1000000:
        return json_response(
            {
                "ok": False,
                "error": "invalid_amount",
            },
            400,
        )

    create_user(user_id)

    result = process_game_transaction(
        user_id=user_id,
        operation_id=operation_id,
        operation_type="add",
        amount=amount,
        game=game,
    )

    if not result["ok"]:
        return json_response(
            result,
            400,
        )

    return json_response(result)


# ============================================================
# API PROMO
# ============================================================

async def api_promo(request):
    user_id = authenticate_request(request)

    if not user_id:
        return json_response(
            {
                "ok": False,
                "error": "invalid_telegram_init_data",
            },
            401,
        )

    try:
        data = await request.json()
    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_json",
            },
            400,
        )

    code = str(
        data.get("code", "")
    ).strip()

    if not code:
        return json_response(
            {
                "ok": False,
                "error": "promo_required",
            },
            400,
        )

    create_user(user_id)

    result = activate_promo(
        user_id,
        code,
    )

    if not result["ok"]:
        status = 400

        if result["error"] == "promo_already_used":
            status = 409

        return json_response(
            result,
            status,
        )

    return json_response(result)


# ============================================================
# OPTIONS
# ============================================================

async def options(request):
    return web.Response(
        status=204,
        headers=cors_headers(),
    )


# ============================================================
# WEBHOOK
# ============================================================

async def webhook(request):
    try:
        data = await request.json()
    except Exception:
        return json_response(
            {
                "ok": False,
                "error": "invalid_json",
            },
            400,
        )

    try:
        update = types.Update.model_validate(data)

        await dp.feed_update(
            bot,
            update,
        )

    except Exception:
        logger.exception(
            "❌ Ошибка webhook"
        )

        return json_response(
            {
                "ok": False,
                "error": "update_processing_error",
            },
            500,
        )

    return json_response(
        {
            "ok": True,
        }
    )


# ============================================================
# WEB SERVER
# ============================================================

async def start_web_server():
    app = web.Application()

    # Главная
    app.router.add_get(
        "/",
        index,
    )

    # Health
    app.router.add_get(
        "/health",
        health,
    )

    # Authenticated API
    app.router.add_get(
        "/api/user",
        api_user,
    )

    app.router.add_get(
        "/api/balance",
        api_balance,
    )

    # Старый endpoint тоже оставляем
    app.router.add_get(
        "/api/balance/{user_id}",
        api_balance,
    )

    # Game
    app.router.add_post(
        "/api/game/transaction",
        api_game_transaction,
    )

    app.router.add_post(
        "/api/balance/deduct",
        api_balance_deduct,
    )

    app.router.add_post(
        "/api/balance/add",
        api_balance_add,
    )

    # Promo
    app.router.add_post(
        "/api/promo",
        api_promo,
    )

    # Webhook
    app.router.add_post(
        "/webhook",
        webhook,
    )

    # OPTIONS
    app.router.add_route(
        "OPTIONS",
        "/api/user",
        options,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance",
        options,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance/{user_id}",
        options,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/game/transaction",
        options,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance/deduct",
        options,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance/add",
        options,
    )

    app.router.add_route(
        "OPTIONS",
        "/api/promo",
        options,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logger.info("==========================================")
    logger.info("🌐 WEB SERVER STARTED")
    logger.info("🌐 PORT: %s", PORT)
    logger.info("🌐 WEBAPP: %s", WEBAPP_URL)
    logger.info("📡 WEBHOOK: %s", WEBHOOK_URL)
    logger.info("==========================================")

    while True:
        await asyncio.sleep(3600)


# ============================================================
# BOT SETUP
# ============================================================

async def setup_bot():
    commands = [
        BotCommand(
            command="start",
            description="Главное меню",
        ),
        BotCommand(
            command="game",
            description="Открыть игру",
        ),
        BotCommand(
            command="balance",
            description="Баланс",
        ),
        BotCommand(
            command="profile",
            description="Профиль",
        ),
    ]

    await bot.set_my_commands(
        commands,
        scope=BotCommandScopeDefault(),
    )

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🎮 Играть",
            web_app=WebAppInfo(
                url=WEBAPP_URL,
            ),
        )
    )

    logger.info(
        "✅ Команды и WebApp-кнопка установлены"
    )


# ============================================================
# WEBHOOK SETUP
# ============================================================

async def setup_webhook():
    try:
        info = await bot.get_webhook_info()

        current_url = info.url or ""

        logger.info(
            "📡 Текущий webhook: %s",
            current_url or "не установлен",
        )

        if current_url != WEBHOOK_URL:
            await bot.set_webhook(
                url=WEBHOOK_URL,
                drop_pending_updates=False,
            )

            logger.info(
                "✅ Webhook установлен: %s",
                WEBHOOK_URL,
            )
        else:
            logger.info(
                "✅ Webhook уже установлен"
            )

    except Exception:
        logger.exception(
            "❌ Ошибка установки webhook"
        )


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info("==========================================")
    logger.info("🚀 Запуск White Bear")
    logger.info("🐍 Python: %s", sys.version)
    logger.info("📁 BASE_DIR: %s", BASE_DIR)
    logger.info("📁 DB: %s", DB_NAME)
    logger.info("📁 HTML: %s", HTML_FILE)
    logger.info("🌐 PORT: %s", PORT)
    logger.info("🌐 WEBAPP: %s", WEBAPP_URL)
    logger.info("📡 WEBHOOK: %s", WEBHOOK_URL)
    logger.info("==========================================")

    init_db()

    await setup_bot()

    await setup_webhook()

    logger.info(
        "🐻‍❄️ White Bear полностью запущен!"
    )

    await start_web_server()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")

    except Exception:
        logger.exception(
            "❌ Критическая ошибка"
        )