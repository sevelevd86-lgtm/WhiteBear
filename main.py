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

PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)

DB_NAME = os.getenv(
    "DB_NAME",
    "users.db"
)

BASE_DIR = Path(__file__).resolve().parent

HTML_FILE = BASE_DIR / "index.html"

WEBAPP_URL = (
    "https://sevelevd86-lgtm.github.io/"
    "WhiteBear/"
)

WEBHOOK_URL = (
    "https://whitebear.bothost.tech"
    "/webhook"
)

INIT_DATA_MAX_AGE = 86400


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format=(
        "%(asctime)s - "
        "%(levelname)s - "
        "%(name)s - "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "white_bear"
)


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не установлен."
    )


# ============================================================
# DATABASE
# ============================================================

def db():

    connection = sqlite3.connect(
        DB_NAME,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():

    conn = db()
    cur = conn.cursor()

    # --------------------------------------------------------
    # USERS
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0,
            username TEXT,
            first_name TEXT,
            ref_code TEXT UNIQUE,
            invited_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # REFERRALS
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward REAL DEFAULT 10,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referrer_id, referred_id)
        )
    """)

    # --------------------------------------------------------
    # PAYMENTS
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT UNIQUE NOT NULL,
            provider_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # GAME TRANSACTIONS
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            amount REAL NOT NULL,
            game TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # PROMOCODES
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            reward REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --------------------------------------------------------
    # ИСПОЛЬЗОВАННЫЕ ПРОМОКОДЫ
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reward REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(promo_id, user_id)
        )
    """)

    # --------------------------------------------------------
    # СОЗДАЁМ ПРОМОКОДЫ
    #
    # 200 = +200 ⭐
    # met200 = +200 ⭐
    # --------------------------------------------------------

    promo_codes = [
        ("200", 200),
        ("met200", 200),
    ]

    for code, reward in promo_codes:

        cur.execute(
            """
            INSERT OR IGNORE INTO promo_codes (
                code,
                reward,
                active
            )
            VALUES (?, ?, 1)
            """,
            (
                code,
                reward
            )
        )

    conn.commit()
    conn.close()

    logger.info(
        "✅ База данных инициализирована"
    )

    logger.info(
        "🎟 Промокоды: 200, met200"
    )


# ============================================================
# USERS
# ============================================================

def get_user(
    user_id: int
):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
    )

    result = cur.fetchone()

    conn.close()

    return result


def create_user(
    user_id: int,
    username=None,
    first_name=None,
    invited_by=None
):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
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
                user_id
            )
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
            (
                ref_code,
            )
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
            invited_by
        )
    )

    conn.commit()
    conn.close()

    logger.info(
        f"👤 Создан пользователь {user_id}"
    )


def get_balance(
    user_id: int
) -> float:

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
    )

    row = cur.fetchone()

    conn.close()

    if not row:

        return 0.0

    return float(
        row["balance"]
    )


def add_balance(
    user_id: int,
    amount: float
):

    amount = round(
        float(amount),
        2
    )

    if amount <= 0:

        return None

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (
            amount,
            user_id
        )
    )

    conn.commit()

    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
    )

    row = cur.fetchone()

    conn.close()

    if row:

        return float(
            row["balance"]
        )

    return None


def deduct_balance(
    user_id: int,
    amount: float
):

    amount = round(
        float(amount),
        2
    )

    if amount <= 0:

        return None

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE users
        SET balance = balance - ?
        WHERE user_id = ?
          AND balance >= ?
        """,
        (
            amount,
            user_id,
            amount
        )
    )

    if cur.rowcount == 0:

        conn.rollback()
        conn.close()

        return None

    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
    )

    row = cur.fetchone()

    conn.commit()
    conn.close()

    if row:

        return float(
            row["balance"]
        )

    return None


# ============================================================
# GAME TRANSACTION
# ============================================================

def process_game_transaction(
    user_id: int,
    operation_id: str,
    operation_type: str,
    amount: float,
    game: str = ""
):

    amount = round(
        float(amount),
        2
    )

    if amount <= 0:

        return {
            "ok": False,
            "error": "invalid_amount"
        }

    if operation_type not in (
        "deduct",
        "add"
    ):

        return {
            "ok": False,
            "error": "invalid_operation"
        }

    conn = db()
    cur = conn.cursor()

    try:

        # ----------------------------------------------------
        # ПРОВЕРКА ПОВТОРНОЙ ОПЕРАЦИИ
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT *
            FROM game_transactions
            WHERE operation_id = ?
            """,
            (
                operation_id,
            )
        )

        existing = cur.fetchone()

        if existing:

            cur.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (
                    user_id,
                )
            )

            row = cur.fetchone()

            conn.close()

            if not row:

                return {
                    "ok": False,
                    "error": "user_not_found"
                }

            return {
                "ok": True,
                "duplicate": True,
                "balance": float(
                    row["balance"]
                ),
                "operation_id":
                    operation_id
            }

        # ----------------------------------------------------
        # USER
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
        )

        user = cur.fetchone()

        if not user:

            conn.close()

            return {
                "ok": False,
                "error": "user_not_found"
            }

        current_balance = float(
            user["balance"]
        )

        # ----------------------------------------------------
        # DEDUCT
        # ----------------------------------------------------

        if operation_type == "deduct":

            if current_balance < amount:

                conn.close()

                return {
                    "ok": False,
                    "error":
                        "insufficient_balance",
                    "balance":
                        current_balance
                }

            new_balance = round(
                current_balance - amount,
                2
            )

        # ----------------------------------------------------
        # ADD
        # ----------------------------------------------------

        else:

            new_balance = round(
                current_balance + amount,
                2
            )

        # ----------------------------------------------------
        # UPDATE BALANCE
        # ----------------------------------------------------

        cur.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE user_id = ?
            """,
            (
                new_balance,
                user_id
            )
        )

        # ----------------------------------------------------
        # SAVE TRANSACTION
        # ----------------------------------------------------

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
                game
            )
        )

        conn.commit()
        conn.close()

        logger.info(
            f"🎮 GAME TRANSACTION: "
            f"user={user_id}, "
            f"game={game}, "
            f"type={operation_type}, "
            f"amount={amount}, "
            f"balance={new_balance}, "
            f"operation={operation_id}"
        )

        return {
            "ok": True,
            "duplicate": False,
            "balance": new_balance,
            "amount": amount,
            "operation_id":
                operation_id
        }

    except sqlite3.IntegrityError:

        conn.rollback()

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
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
            "operation_id":
                operation_id
        }

    except Exception:

        conn.rollback()
        conn.close()

        logger.exception(
            "❌ Ошибка game transaction"
        )

        return {
            "ok": False,
            "error":
                "transaction_error"
        }


# ============================================================
# PROMOCODES
# ============================================================

def redeem_promo(
    user_id: int,
    code: str
):

    code = str(
        code
    ).strip().lower()

    if not code:

        return {
            "ok": False,
            "error":
                "invalid_promo"
        }

    conn = db()
    cur = conn.cursor()

    try:

        # ----------------------------------------------------
        # НАЧИНАЕМ ТРАНЗАКЦИЮ
        # ----------------------------------------------------

        conn.execute(
            "BEGIN IMMEDIATE"
        )

        # ----------------------------------------------------
        # USER
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
        )

        user = cur.fetchone()

        if not user:

            conn.rollback()
            conn.close()

            return {
                "ok": False,
                "error":
                    "user_not_found"
            }

        # ----------------------------------------------------
        # PROMO
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT *
            FROM promo_codes
            WHERE code = ?
              AND active = 1
            """,
            (
                code,
            )
        )

        promo = cur.fetchone()

        if not promo:

            conn.rollback()
            conn.close()

            return {
                "ok": False,
                "error":
                    "invalid_promo"
            }

        promo_id = int(
            promo["id"]
        )

        reward = round(
            float(
                promo["reward"]
            ),
            2
        )

        # ----------------------------------------------------
        # ПРОВЕРЯЕМ ИСПОЛЬЗОВАНИЕ
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT id
            FROM promo_uses
            WHERE promo_id = ?
              AND user_id = ?
            """,
            (
                promo_id,
                user_id
            )
        )

        already_used = cur.fetchone()

        if already_used:

            conn.rollback()
            conn.close()

            return {
                "ok": False,
                "error":
                    "promo_already_used"
            }

        # ----------------------------------------------------
        # НАЧИСЛЯЕМ
        # ----------------------------------------------------

        cur.execute(
            """
            UPDATE users
            SET balance = balance + ?
            WHERE user_id = ?
            """,
            (
                reward,
                user_id
            )
        )

        # ----------------------------------------------------
        # СОХРАНЯЕМ ИСПОЛЬЗОВАНИЕ
        # ----------------------------------------------------

        cur.execute(
            """
            INSERT INTO promo_uses (
                promo_id,
                user_id,
                reward
            )
            VALUES (?, ?, ?)
            """,
            (
                promo_id,
                user_id,
                reward
            )
        )

        # ----------------------------------------------------
        # НОВЫЙ БАЛАНС
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id = ?
            """,
            (
                user_id,
            )
        )

        row = cur.fetchone()

        new_balance = float(
            row["balance"]
        )

        conn.commit()
        conn.close()

        logger.info(
            f"🎟 PROMO: "
            f"user={user_id}, "
            f"code={code}, "
            f"reward={reward}, "
            f"balance={new_balance}"
        )

        return {
            "ok": True,
            "code": code,
            "reward": reward,
            "balance":
                new_balance
        }

    except sqlite3.IntegrityError:

        conn.rollback()
        conn.close()

        return {
            "ok": False,
            "error":
                "promo_already_used"
        }

    except Exception:

        conn.rollback()
        conn.close()

        logger.exception(
            "❌ Ошибка промокода"
        )

        return {
            "ok": False,
            "error":
                "promo_error"
        }


# ============================================================
# REFERRALS
# ============================================================

def get_referrals_count(
    user_id: int
):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM referrals
        WHERE referrer_id = ?
        """,
        (
            user_id,
        )
    )

    result = cur.fetchone()

    conn.close()

    return int(
        result[0]
    )


def get_referral_link(
    user_id: int
):

    return (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )


def get_user_by_ref_code(
    ref_code: str
):

    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE ref_code = ?
        """,
        (
            ref_code,
        )
    )

    row = cur.fetchone()

    conn.close()

    return (
        row["user_id"]
        if row
        else None
    )


def add_referral(
    referrer_id: int,
    referred_id: int
):

    if referrer_id == referred_id:

        return False

    conn = db()
    cur = conn.cursor()

    cur.execute(
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
            referred_id
        )
    )

    cur.execute(
        """
        UPDATE users
        SET balance = balance + 10
        WHERE user_id IN (?, ?)
        """,
        (
            referrer_id,
            referred_id
        )
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# TELEGRAM INIT DATA
# ============================================================

def validate_init_data(
    init_data: str
):

    if not init_data:

        return None

    try:

        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = data.pop(
            "hash",
            None
        )

        if not received_hash:

            return None

        auth_date = data.get(
            "auth_date"
        )

        if not auth_date:

            return None

        auth_timestamp = int(
            auth_date
        )

        now = int(
            time.time()
        )

        if (
            now - auth_timestamp
            > INIT_DATA_MAX_AGE
        ):

            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
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

            return None

        user_string = data.get(
            "user"
        )

        if not user_string:

            return None

        return json.loads(
            user_string
        )

    except Exception as e:

        logger.exception(
            f"❌ Ошибка проверки initData: {e}"
        )

        return None


# ============================================================
# WEBAPP AUTH
# ============================================================

def get_webapp_user(
    request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    ).strip()

    if not init_data:

        return None

    return validate_init_data(
        init_data
    )


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🎮 Открыть игру",
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
        text="📎 Реферал",
        callback_data="referral"
    )

    builder.adjust(
        1,
        2,
        2
    )

    return builder.as_markup()


def deposit_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1",
                    callback_data="buy_1"
                ),
                InlineKeyboardButton(
                    text="⭐ 10",
                    callback_data="buy_10"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 50",
                    callback_data="buy_50"
                ),
                InlineKeyboardButton(
                    text="⭐ 100",
                    callback_data="buy_100"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="back"
                )
            ]
        ]
    )


# ============================================================
# START
# ============================================================

@dp.message(
    Command("start")
)
async def start(
    message: Message
):

    user_id = message.from_user.id

    username = (
        message.from_user.username
    )

    first_name = (
        message.from_user.first_name
    )

    args = message.text.split()

    invited_by = None

    if not get_user(user_id):

        if (
            len(args) > 1
            and args[1].startswith("ref_")
        ):

            ref_code = args[1][4:]

            invited_by = (
                get_user_by_ref_code(
                    ref_code
                )
            )

            if invited_by == user_id:

                invited_by = None

        create_user(
            user_id,
            username,
            first_name,
            invited_by
        )

        if invited_by:

            if add_referral(
                invited_by,
                user_id
            ):

                try:

                    await bot.send_message(
                        invited_by,
                        "🎉 <b>Новый реферал!</b>\n\n"
                        "Вы получили <b>+10 ⭐</b>."
                    )

                except Exception:

                    pass

    else:

        create_user(
            user_id,
            username,
            first_name
        )

    balance = get_balance(
        user_id
    )

    await message.answer(
        f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"
        f"🎮 Открывайте игру кнопкой ниже.",
        reply_markup=main_keyboard()
    )


# ============================================================
# GAME COMMAND
# ============================================================

@dp.message(
    Command("game")
)
async def game_command(
    message: Message
):

    await message.answer(
        "🎮 <b>White Bear Drop</b>\n\n"
        "Нажми кнопку ниже, чтобы открыть игру.",
        reply_markup=main_keyboard()
    )


# ============================================================
# BALANCE COMMAND
# ============================================================

@dp.message(
    Command("balance")
)
async def balance_command(
    message: Message
):

    user_id = message.from_user.id

    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    await message.answer(
        f"💰 Ваш баланс: "
        f"<b>{balance:.2f} ⭐</b>"
    )


# ============================================================
# PROFILE COMMAND
# ============================================================

@dp.message(
    Command("profile")
)
async def profile_command(
    message: Message
):

    user_id = message.from_user.id

    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

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
                        callback_data="deposit"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игру",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    ]
                ]
            ]
        )
    )


# ============================================================
# DEPOSIT
# ============================================================

@dp.callback_query(
    F.data == "deposit"
)
async def deposit(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(
        "⭐ <b>Пополнение баланса</b>\n\n"
        "Выберите количество Stars.\n\n"
        "После успешной оплаты сумма "
        "автоматически зачислится "
        "на ваш игровой баланс.",
        reply_markup=deposit_keyboard()
    )

    await callback.answer()


# ============================================================
# CREATE INVOICE
# ============================================================

async def create_invoice(
    user_id: int,
    amount: int
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
                amount=amount
            )
        ],
        provider_token=""
    )


# ============================================================
# BUY STARS
# ============================================================

@dp.callback_query(
    F.data.startswith("buy_")
)
async def buy_stars(
    callback: types.CallbackQuery
):

    try:

        amount = int(
            callback.data.replace(
                "buy_",
                ""
            )
        )

        if amount < 1:

            raise ValueError

        create_user(
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name
        )

        await create_invoice(
            callback.from_user.id,
            amount
        )

        await callback.answer()

    except Exception as e:

        logger.exception(
            f"❌ Ошибка invoice: {e}"
        )

        await callback.answer(
            "❌ Не удалось создать оплату",
            show_alert=True
        )


# ============================================================
# PRE CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    logger.info(
        f"💳 PRE-CHECKOUT: "
        f"user={query.from_user.id}, "
        f"amount={query.total_amount}, "
        f"currency={query.currency}"
    )

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message="Неверная валюта."
        )

        return

    if query.total_amount < 1:

        await query.answer(
            ok=False,
            error_message="Неверная сумма."
        )

        return

    await query.answer(
        ok=True
    )


# ============================================================
# SUCCESSFUL PAYMENT
# ============================================================

@dp.message(
    F.successful_payment
)
async def successful_payment(
    message: Message
):

    payment = (
        message.successful_payment
    )

    user_id = (
        message.from_user.id
    )

    amount = int(
        payment.total_amount
    )

    charge_id = (
        payment.telegram_payment_charge_id
    )

    provider_charge_id = (
        payment.provider_payment_charge_id
    )

    logger.info(
        f"💰 SUCCESSFUL PAYMENT: "
        f"user={user_id}, "
        f"amount={amount}, "
        f"currency={payment.currency}"
    )

    if payment.currency != "XTR":

        return

    conn = db()
    cur = conn.cursor()

    # --------------------------------------------------------
    # ПРОВЕРКА ПОВТОРНОГО ПЛАТЕЖА
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT id
        FROM payments
        WHERE telegram_charge_id = ?
        """,
        (
            charge_id,
        )
    )

    if cur.fetchone():

        conn.close()

        await message.answer(
            "ℹ️ Этот платеж уже был зачислен."
        )

        return

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT user_id
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
    )

    if not cur.fetchone():

        while True:

            ref_code = secrets.token_hex(
                8
            )

            cur.execute(
                """
                SELECT user_id
                FROM users
                WHERE ref_code = ?
                """,
                (
                    ref_code,
                )
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
                ref_code
            )
        )

    # --------------------------------------------------------
    # НАЧИСЛЕНИЕ
    # --------------------------------------------------------

    cur.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (
            amount,
            user_id
        )
    )

    # --------------------------------------------------------
    # СОХРАНЯЕМ PAYMENT
    # --------------------------------------------------------

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
            "XTR"
        )
    )

    # --------------------------------------------------------
    # НОВЫЙ БАЛАНС
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (
            user_id,
        )
    )

    row = cur.fetchone()

    new_balance = float(
        row["balance"]
    )

    conn.commit()
    conn.close()

    logger.info(
        f"💰 BALANCE UPDATED: "
        f"user={user_id}, "
        f"added={amount}, "
        f"new_balance={new_balance}"
    )

    await message.answer(
        f"✅ <b>Оплата получена!</b>\n\n"
        f"⭐ Оплачено: <b>{amount}</b>\n"
        f"💰 Начислено: "
        f"<b>{amount} ⭐</b>\n"
        f"💳 Баланс: "
        f"<b>{new_balance:.2f} ⭐</b>"
    )


# ============================================================
# BALANCE CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "balance"
)
async def balance_callback(
    callback: types.CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    await callback.message.edit_text(
        f"💰 <b>Ваш баланс</b>\n\n"
        f"<b>{balance:.2f} ⭐</b>",
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
                        callback_data="back"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# PROFILE CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "profile"
)
async def profile_callback(
    callback: types.CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

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
                        callback_data="deposit"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎮 Открыть игру",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Назад",
                        callback_data="back"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# REFERRAL CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "referral"
)
async def referral_callback(
    callback: types.CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    link = get_referral_link(
        user_id
    )

    count = get_referrals_count(
        user_id
    )

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
                        callback_data="back"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# ============================================================
# BACK
# ============================================================

@dp.callback_query(
    F.data == "back"
)
async def back_callback(
    callback: types.CallbackQuery
):

    user_id = (
        callback.from_user.id
    )

    create_user(
        user_id,
        callback.from_user.username,
        callback.from_user.first_name
    )

    balance = get_balance(
        user_id
    )

    await callback.message.edit_text(
        f"🐻‍❄️ <b>White Bear Drop</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# WEB API
# ============================================================

def cors_headers():

    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods":
            "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers":
            "Content-Type, X-Telegram-Init-Data",
        "Cache-Control":
            "no-store, no-cache, must-revalidate",
        "Pragma":
            "no-cache"
    }


# ============================================================
# ROOT
# ============================================================

async def index(
    request
):

    logger.info(
        f"🌐 GET / from "
        f"{request.remote}"
    )

    if not HTML_FILE.exists():

        return web.Response(
            text=(
                "White Bear API is running.\n"
                "index.html not found."
            ),
            status=200,
            headers=cors_headers()
        )

    return web.FileResponse(
        HTML_FILE,
        headers=cors_headers()
    )


# ============================================================
# HEALTH
# ============================================================

async def health(
    request
):

    return web.json_response(
        {
            "ok": True,
            "status": "OK",
            "service": "White Bear",
            "port": PORT
        },
        headers=cors_headers()
    )


# ============================================================
# API BALANCE BY ID
# ============================================================

async def api_balance_by_id(
    request
):

    raw_user_id = (
        request.match_info.get(
            "user_id"
        )
    )

    try:

        user_id = int(
            raw_user_id
        )

    except (
        TypeError,
        ValueError
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_user_id"
            },
            status=400,
            headers=cors_headers()
        )

    user = get_user(
        user_id
    )

    if not user:

        create_user(
            user_id
        )

    balance = get_balance(
        user_id
    )

    return web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "balance": balance
        },
        headers=cors_headers()
    )


# ============================================================
# API USER
# ============================================================

async def api_user_by_id(
    request
):

    raw_user_id = (
        request.match_info.get(
            "user_id"
        )
    )

    try:

        user_id = int(
            raw_user_id
        )

    except (
        TypeError,
        ValueError
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_user_id"
            },
            status=400,
            headers=cors_headers()
        )

    user = get_user(
        user_id
    )

    if not user:

        create_user(
            user_id
        )

        user = get_user(
            user_id
        )

    return web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "username":
                user["username"],
            "first_name":
                user["first_name"],
            "balance":
                float(
                    user["balance"]
                ),
            "ref_code":
                user["ref_code"]
        },
        headers=cors_headers()
    )


# ============================================================
# API DEDUCT
# ============================================================

async def api_balance_deduct(
    request
):

    telegram_user = (
        get_webapp_user(
            request
        )
    )

    if not telegram_user:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_telegram_init_data"
            },
            status=401,
            headers=cors_headers()
        )

    authenticated_user_id = int(
        telegram_user["id"]
    )

    try:

        data = await request.json()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_json"
            },
            status=400,
            headers=cors_headers()
        )

    try:

        requested_user_id = int(
            data.get("user_id")
        )

        amount = float(
            data.get("amount")
        )

        operation_id = str(
            data.get(
                "operation_id",
                ""
            )
        ).strip()

        game = str(
            data.get(
                "game",
                ""
            )
        ).strip()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_data"
            },
            status=400,
            headers=cors_headers()
        )

    if (
        requested_user_id
        != authenticated_user_id
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "user_mismatch"
            },
            status=403,
            headers=cors_headers()
        )

    if not operation_id:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "operation_id_required"
            },
            status=400,
            headers=cors_headers()
        )

    if (
        amount <= 0
        or amount > 1000000
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_amount"
            },
            status=400,
            headers=cors_headers()
        )

    create_user(
        authenticated_user_id,
        telegram_user.get(
            "username"
        ),
        telegram_user.get(
            "first_name"
        )
    )

    result = process_game_transaction(
        user_id=authenticated_user_id,
        operation_id=operation_id,
        operation_type="deduct",
        amount=amount,
        game=game
    )

    if not result["ok"]:

        status = 400

        if (
            result["error"]
            == "insufficient_balance"
        ):

            status = 402

        return web.json_response(
            result,
            status=status,
            headers=cors_headers()
        )

    return web.json_response(
        result,
        headers=cors_headers()
    )


# ============================================================
# API ADD
# ============================================================

async def api_balance_add(
    request
):

    telegram_user = (
        get_webapp_user(
            request
        )
    )

    if not telegram_user:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_telegram_init_data"
            },
            status=401,
            headers=cors_headers()
        )

    authenticated_user_id = int(
        telegram_user["id"]
    )

    try:

        data = await request.json()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_json"
            },
            status=400,
            headers=cors_headers()
        )

    try:

        requested_user_id = int(
            data.get("user_id")
        )

        amount = float(
            data.get("amount")
        )

        operation_id = str(
            data.get(
                "operation_id",
                ""
            )
        ).strip()

        game = str(
            data.get(
                "game",
                ""
            )
        ).strip()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_data"
            },
            status=400,
            headers=cors_headers()
        )

    if (
        requested_user_id
        != authenticated_user_id
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "user_mismatch"
            },
            status=403,
            headers=cors_headers()
        )

    if not operation_id:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "operation_id_required"
            },
            status=400,
            headers=cors_headers()
        )

    if (
        amount <= 0
        or amount > 1000000
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_amount"
            },
            status=400,
            headers=cors_headers()
        )

    create_user(
        authenticated_user_id,
        telegram_user.get(
            "username"
        ),
        telegram_user.get(
            "first_name"
        )
    )

    result = process_game_transaction(
        user_id=authenticated_user_id,
        operation_id=operation_id,
        operation_type="add",
        amount=amount,
        game=game
    )

    if not result["ok"]:

        return web.json_response(
            result,
            status=400,
            headers=cors_headers()
        )

    return web.json_response(
        result,
        headers=cors_headers()
    )


# ============================================================
# API GAME TRANSACTION
# ============================================================

async def api_game_transaction(
    request
):

    telegram_user = (
        get_webapp_user(
            request
        )
    )

    if not telegram_user:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_telegram_init_data"
            },
            status=401,
            headers=cors_headers()
        )

    authenticated_user_id = int(
        telegram_user["id"]
    )

    try:

        data = await request.json()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_json"
            },
            status=400,
            headers=cors_headers()
        )

    try:

        requested_user_id = int(
            data.get("user_id")
        )

        amount = float(
            data.get("amount")
        )

        operation_id = str(
            data.get(
                "operation_id",
                ""
            )
        ).strip()

        operation_type = str(
            data.get(
                "type",
                ""
            )
        ).strip().lower()

        game = str(
            data.get(
                "game",
                ""
            )
        ).strip()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_data"
            },
            status=400,
            headers=cors_headers()
        )

    if (
        requested_user_id
        != authenticated_user_id
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "user_mismatch"
            },
            status=403,
            headers=cors_headers()
        )

    if operation_type not in (
        "deduct",
        "add"
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_operation"
            },
            status=400,
            headers=cors_headers()
        )

    if not operation_id:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "operation_id_required"
            },
            status=400,
            headers=cors_headers()
        )

    if (
        amount <= 0
        or amount > 1000000
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_amount"
            },
            status=400,
            headers=cors_headers()
        )

    create_user(
        authenticated_user_id,
        telegram_user.get(
            "username"
        ),
        telegram_user.get(
            "first_name"
        )
    )

    result = process_game_transaction(
        user_id=authenticated_user_id,
        operation_id=operation_id,
        operation_type=operation_type,
        amount=amount,
        game=game
    )

    status = 200

    if not result["ok"]:

        status = 400

        if (
            result["error"]
            == "insufficient_balance"
        ):

            status = 402

    return web.json_response(
        result,
        status=status,
        headers=cors_headers()
    )


# ============================================================
# API PROMO
#
# POST /api/promo/redeem
#
# {
#     "user_id": 123456,
#     "code": "200"
# }
# ============================================================

async def api_promo_redeem(
    request
):

    # --------------------------------------------------------
    # TELEGRAM AUTH
    # --------------------------------------------------------

    telegram_user = (
        get_webapp_user(
            request
        )
    )

    if not telegram_user:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_telegram_init_data"
            },
            status=401,
            headers=cors_headers()
        )

    authenticated_user_id = int(
        telegram_user["id"]
    )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:

        data = await request.json()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_json"
            },
            status=400,
            headers=cors_headers()
        )

    try:

        requested_user_id = int(
            data.get("user_id")
        )

        code = str(
            data.get(
                "code",
                ""
            )
        ).strip().lower()

    except Exception:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_data"
            },
            status=400,
            headers=cors_headers()
        )

    # --------------------------------------------------------
    # USER MUST MATCH TELEGRAM USER
    # --------------------------------------------------------

    if (
        requested_user_id
        != authenticated_user_id
    ):

        return web.json_response(
            {
                "ok": False,
                "error":
                    "user_mismatch"
            },
            status=403,
            headers=cors_headers()
        )

    # --------------------------------------------------------
    # VALIDATE CODE
    # --------------------------------------------------------

    if not code:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_promo"
            },
            status=400,
            headers=cors_headers()
        )

    if len(code) > 64:

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_promo"
            },
            status=400,
            headers=cors_headers()
        )

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

    create_user(
        authenticated_user_id,
        telegram_user.get(
            "username"
        ),
        telegram_user.get(
            "first_name"
        )
    )

    # --------------------------------------------------------
    # REDEEM
    # --------------------------------------------------------

    result = redeem_promo(
        authenticated_user_id,
        code
    )

    if not result["ok"]:

        status = 400

        if (
            result["error"]
            == "promo_already_used"
        ):

            status = 409

        return web.json_response(
            result,
            status=status,
            headers=cors_headers()
        )

    return web.json_response(
        result,
        headers=cors_headers()
    )


# ============================================================
# OPTIONS
# ============================================================

async def options(
    request
):

    return web.Response(
        status=204,
        headers=cors_headers()
    )


# ============================================================
# WEBHOOK
# ============================================================

async def webhook(
    request
):

    logger.info(
        f"📡 WEBHOOK REQUEST from "
        f"{request.remote}"
    )

    try:

        data = await request.json()

    except Exception:

        logger.warning(
            "⚠️ Webhook получил не JSON"
        )

        return web.json_response(
            {
                "ok": False,
                "error":
                    "invalid_json"
            },
            status=400,
            headers=cors_headers()
        )

    try:

        update = types.Update.model_validate(
            data
        )

        await dp.feed_update(
            bot,
            update
        )

    except Exception as e:

        logger.exception(
            f"❌ Ошибка обработки webhook: {e}"
        )

        return web.json_response(
            {
                "ok": False,
                "error":
                    "update_processing_error"
            },
            status=500,
            headers=cors_headers()
        )

    return web.json_response(
        {
            "ok": True
        },
        headers=cors_headers()
    )


# ============================================================
# WEB SERVER
# ============================================================

async def start_web_server():

    app = web.Application()

    # --------------------------------------------------------
    # MAIN
    # --------------------------------------------------------

    app.router.add_get(
        "/",
        index
    )

    # --------------------------------------------------------
    # HEALTH
    # --------------------------------------------------------

    app.router.add_get(
        "/health",
        health
    )

    # --------------------------------------------------------
    # BALANCE
    # --------------------------------------------------------

    app.router.add_get(
        "/api/balance/{user_id}",
        api_balance_by_id
    )

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

    app.router.add_get(
        "/api/user/{user_id}",
        api_user_by_id
    )

    # --------------------------------------------------------
    # DEDUCT
    # --------------------------------------------------------

    app.router.add_post(
        "/api/balance/deduct",
        api_balance_deduct
    )

    # --------------------------------------------------------
    # ADD
    # --------------------------------------------------------

    app.router.add_post(
        "/api/balance/add",
        api_balance_add
    )

    # --------------------------------------------------------
    # GAME
    # --------------------------------------------------------

    app.router.add_post(
        "/api/game/transaction",
        api_game_transaction
    )

    # --------------------------------------------------------
    # PROMO
    # --------------------------------------------------------

    app.router.add_post(
        "/api/promo/redeem",
        api_promo_redeem
    )

    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    app.router.add_route(
        "OPTIONS",
        "/api/balance/{user_id}",
        options
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance/deduct",
        options
    )

    app.router.add_route(
        "OPTIONS",
        "/api/balance/add",
        options
    )

    app.router.add_route(
        "OPTIONS",
        "/api/game/transaction",
        options
    )

    app.router.add_route(
        "OPTIONS",
        "/api/promo/redeem",
        options
    )

    app.router.add_route(
        "OPTIONS",
        "/api/user/{user_id}",
        options
    )

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    app.router.add_post(
        "/webhook",
        webhook
    )

    # --------------------------------------------------------
    # RUNNER
    # --------------------------------------------------------

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    logger.info(
        "=========================================="
    )

    logger.info(
        "🌐 WEB SERVER STARTED"
    )

    logger.info(
        f"🌐 LISTENING: 0.0.0.0:{PORT}"
    )

    logger.info(
        f"🌐 WEBAPP: {WEBAPP_URL}"
    )

    logger.info(
        "❤️ HEALTH: /health"
    )

    logger.info(
        "💰 GET BALANCE: /api/balance/{user_id}"
    )

    logger.info(
        "➖ DEDUCT: /api/balance/deduct"
    )

    logger.info(
        "➕ ADD: /api/balance/add"
    )

    logger.info(
        "🎮 GAME: /api/game/transaction"
    )

    logger.info(
        "👤 USER: /api/user/{user_id}"
    )

    logger.info(
        "🎟 PROMO: /api/promo/redeem"
    )

    logger.info(
        "📡 WEBHOOK: /webhook"
    )

    logger.info(
        "🎟 PROMOCODES: 200 / met200"
    )

    logger.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # LOCAL HEALTH TEST
    # --------------------------------------------------------

    await asyncio.sleep(1)

    try:

        import aiohttp

        async with aiohttp.ClientSession() as session:

            async with session.get(
                f"http://127.0.0.1:{PORT}/health"
            ) as response:

                text = await response.text()

                logger.info(
                    f"🔎 LOCAL HEALTH: "
                    f"{response.status}"
                )

                logger.info(
                    f"🔎 RESPONSE: {text}"
                )

    except Exception as e:

        logger.warning(
            f"⚠️ Local health test failed: {e}"
        )

    # --------------------------------------------------------
    # KEEP SERVER ALIVE
    # --------------------------------------------------------

    while True:

        await asyncio.sleep(
            3600
        )


# ============================================================
# BOT SETUP
# ============================================================

async def setup_bot():

    commands = [
        BotCommand(
            command="start",
            description="Главное меню"
        ),
        BotCommand(
            command="game",
            description="Открыть игру"
        ),
        BotCommand(
            command="balance",
            description="Баланс"
        ),
        BotCommand(
            command="profile",
            description="Профиль"
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

    logger.info(
        f"🌐 WebApp URL: {WEBAPP_URL}"
    )


# ============================================================
# WEBHOOK SETUP
# ============================================================

async def setup_webhook():

    try:

        info = await bot.get_webhook_info()

        current_url = (
            info.url
            or "не установлен"
        )

        logger.info(
            f"📡 Текущий webhook: "
            f"{current_url}"
        )

        if current_url == WEBHOOK_URL:

            logger.info(
                "✅ Webhook уже установлен"
            )

            return

        await bot.set_webhook(
            url=WEBHOOK_URL,
            drop_pending_updates=False
        )

        logger.info(
            f"✅ Webhook установлен: "
            f"{WEBHOOK_URL}"
        )

    except Exception as e:

        logger.exception(
            f"❌ Ошибка установки webhook: {e}"
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info(
        "=========================================="
    )

    logger.info(
        "🚀 Запуск White Bear..."
    )

    logger.info(
        f"🐍 Python: {sys.version}"
    )

    logger.info(
        f"📁 BASE_DIR: {BASE_DIR}"
    )

    logger.info(
        f"📁 DB: {DB_NAME}"
    )

    logger.info(
        f"📁 HTML: {HTML_FILE}"
    )

    logger.info(
        f"🌐 PORT: {PORT}"
    )

    logger.info(
        f"🌐 WEBAPP: {WEBAPP_URL}"
    )

    logger.info(
        f"📡 WEBHOOK: {WEBHOOK_URL}"
    )

    logger.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

    await setup_bot()

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    await setup_webhook()

    logger.info(
        "🐻‍❄️ White Bear полностью запущен!"
    )

    # --------------------------------------------------------
    # WEB SERVER
    # --------------------------------------------------------

    await start_web_server()


# ============================================================
# ENTRY POINT
# ============================================================

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