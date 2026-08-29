import os
import json
import time
import hmac
import hashlib
import secrets
import sqlite3
import asyncio
import logging

from contextlib import closing
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import uvicorn

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    LabeledPrice,
    PreCheckoutQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
    BotCommand,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    ""
).strip()

PORT = int(
    os.getenv(
        "PORT",
        "3000"
    )
)

DB_PATH = os.getenv(
    "DB_PATH",
    "whitebear.db"
)

# ИГРА
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://sevelevd86-lgtm.github.io/WhiteBear/"
).strip()

# API
API_URL = "https://whitebear.bothost.tech"


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан в Secrets"
    )


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO
)

log = logging.getLogger(
    "whitebear"
)


# ============================================================
# BOT
# ============================================================

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="White Bear Drop API",
    version="3.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)


# ============================================================
# DATABASE
# ============================================================

def db():

    con = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    con.row_factory = sqlite3.Row

    con.execute(
        "PRAGMA journal_mode=WAL"
    )

    return con


def init_db():

    with closing(db()) as con:

        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                operation_id TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                reason TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                payload TEXT NOT NULL UNIQUE,
                amount INTEGER NOT NULL,
                telegram_charge_id TEXT UNIQUE,
                provider_charge_id TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                paid_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT DEFAULT '🎁',
                value REAL NOT NULL DEFAULT 0,
                rarity TEXT DEFAULT '',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS referrals (
   invited_user_id INTEGER PRIMARY KEY,
   referrer_user_id INTEGER NOT NULL,
   rewarded INTEGER NOT NULL DEFAULT 0,
   created_at INTEGER NOT NULL
 );

  CREATE TABLE IF NOT EXISTS promo_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(user_id, code)
            );
            """
        )

        con.commit()

    log.info(
        "Database initialized"
    )


# ============================================================
# ============================================================
# REFERRALS
# ============================================================
REFERRAL_REWARD = 5.0

def register_referral(invited_user_id: int, referrer_user_id: int):
    invited_user_id = int(invited_user_id)
    referrer_user_id = int(referrer_user_id)

    if invited_user_id <= 0 or referrer_user_id <= 0:
        return False
    if invited_user_id == referrer_user_id:
        return False

    ensure_user(invited_user_id)
    ensure_user(referrer_user_id)

    with closing(db()) as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT 1 FROM referrals WHERE invited_user_id=?",
            (invited_user_id,)
        ).fetchone()
        if existing:
            con.commit()
            return False

        con.execute(
            "INSERT INTO referrals (invited_user_id, referrer_user_id, rewarded, created_at) VALUES (?,?,0,?)",
            (invited_user_id, referrer_user_id, 0, int(time.time()))
        )
        con.commit()

    balance_transaction(
        invited_user_id,
        f"referral:new:{invited_user_id}",
        "add",
        REFERRAL_REWARD,
        "referral:new_user"
    )
    balance_transaction(
        referrer_user_id,
        f"referral:invite:{invited_user_id}",
        "add",
        REFERRAL_REWARD,
        "referral:invite"
    )

    with closing(db()) as con:
        con.execute(
            "UPDATE referrals SET rewarded=1 WHERE invited_user_id=?",
            (invited_user_id,)
        )
        con.commit()

    return True

# USER
# ============================================================

def ensure_user(
    user_id: int,
    telegram_user=None
):

    now = int(
        time.time()
    )

    username = ""
    first_name = ""
    last_name = ""

    if telegram_user:

        username = (
            getattr(
                telegram_user,
                "username",
                ""
            )
            or ""
        )

        first_name = (
            getattr(
                telegram_user,
                "first_name",
                ""
            )
            or ""
        )

        last_name = (
            getattr(
                telegram_user,
                "last_name",
                ""
            )
            or ""
        )

    with closing(db()) as con:

        row = con.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id=?
            """,
            (
                user_id,
            )
        ).fetchone()

        if row is None:

            con.execute(
                """
                INSERT INTO users
                (
                    user_id,
                    balance,
                    username,
                    first_name,
                    last_name,
                    created_at,
                    updated_at
                )
                VALUES (?,0,?,?,?,?,?)
                """,
                (
                    user_id,
                    username,
                    first_name,
                    last_name,
                    now,
                    now
                )
            )

        elif telegram_user:

            con.execute(
                """
                UPDATE users
                SET
                    username=?,
                    first_name=?,
                    last_name=?,
                    updated_at=?
                WHERE user_id=?
                """,
                (
                    username,
                    first_name,
                    last_name,
                    now,
                    user_id
                )
            )

        con.commit()


def get_balance(
    user_id: int
):

    ensure_user(
        user_id
    )

    with closing(db()) as con:

        row = con.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (
                user_id,
            )
        ).fetchone()

        if not row:
            return 0.0

        return round(
            float(
                row["balance"]
            ),
            2
        )


# ============================================================
# BALANCE TRANSACTION
# ============================================================

def balance_transaction(
    user_id: int,
    operation_id: str,
    op_type: str,
    amount: float,
    reason=""
):

    amount = round(
        float(amount),
        2
    )

    if amount <= 0:

        raise ValueError(
            "Сумма должна быть больше 0"
        )

    if op_type not in (
        "add",
        "deduct"
    ):

        raise ValueError(
            "Неверный тип операции"
        )

    ensure_user(
        user_id
    )

    with closing(db()) as con:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        existing = con.execute(
            """
            SELECT 1
            FROM transactions
            WHERE operation_id=?
            """,
            (
                operation_id,
            )
        ).fetchone()

        if existing:

            row = con.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id=?
                """,
                (
                    user_id,
                )
            ).fetchone()

            con.commit()

            if row:
                return float(
                    row["balance"]
                )

            return 0.0

        row = con.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (
                user_id,
            )
        ).fetchone()

        if not row:

            con.rollback()

            raise ValueError(
                "Пользователь не найден"
            )

        balance = float(
            row["balance"]
        )

        if op_type == "deduct":

            if balance < amount:

                con.rollback()

                raise ValueError(
                    "Недостаточно ⭐"
                )

            new_balance = round(
                balance - amount,
                2
            )

        else:

            new_balance = round(
                balance + amount,
                2
            )

        now = int(
            time.time()
        )

        con.execute(
            """
            UPDATE users
            SET
                balance=?,
                updated_at=?
            WHERE user_id=?
            """,
            (
                new_balance,
                now,
                user_id
            )
        )

        con.execute(
            """
            INSERT INTO transactions
            (
                user_id,
                operation_id,
                type,
                amount,
                reason,
                created_at
            )
            VALUES (?,?,?,?,?,?)
            """,
            (
                user_id,
                operation_id,
                op_type,
                amount,
                reason[:200],
                now
            )
        )

        con.commit()

        return new_balance


# ============================================================
# TELEGRAM INIT DATA
# ============================================================

def verify_init_data(
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

        auth_date = int(
            data.get(
                "auth_date",
                "0"
            )
        )

        if not auth_date:
            return None

        if (
            int(time.time())
            - auth_date
            > 86400
        ):

            return None

        check_string = "\n".join(
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
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):

            return None

        return json.loads(
            data["user"]
        )

    except Exception:

        return None


def resolve_user(
    user_id,
    init_data
):

    verified = verify_init_data(
        init_data or ""
    )

    if verified:

        uid = int(
            verified["id"]
        )

        if (
            user_id is not None
            and int(user_id) != uid
        ):

            raise HTTPException(
                403,
                "Telegram ID mismatch"
            )

        ensure_user(
            uid
        )

        return (
            uid,
            verified
        )

    if (
        user_id is None
        or int(user_id) <= 0
    ):

        raise HTTPException(
            401,
            "Открой приложение через Telegram"
        )

    ensure_user(
        int(user_id)
    )

    return (
        int(user_id),
        None
    )


# ============================================================
# MODELS
# ============================================================

class BalanceRequest(BaseModel):

    user_id: int

    amount: float = Field(
        gt=0
    )

    reason: str = ""


class TransactionRequest(BaseModel):

    user_id: int

    operation_id: str

    type: str

    amount: float = Field(
        gt=0
    )

    game: str = ""


class PaymentRequest(BaseModel):

    user_id: int

    amount: int = Field(
        gt=0,
        le=100000
    )


class PromoRequest(BaseModel):

    user_id: int

    code: str


class CaseRequest(BaseModel):

    user_id: int

    case_id: str

    price: float = 0


class BallRequest(BaseModel):

    user_id: int

    bet: int = Field(
        gt=0
    )


class ScratchRequest(BaseModel):

    user_id: int


class UpgradeRequest(BaseModel):

    user_id: int

    item_id: str

    bet: int = Field(
        gt=0
    )


class SellRequest(BaseModel):

    user_id: int

    item_id: str


# ============================================================
# CASES
# ============================================================

CASES = {

    "free": {

        "price": 0,

        "items": [

            (
                "⭐",
                "3 Stars",
                3,
                30,
                ""
            ),

            (
                "⭐",
                "5 Stars",
                5,
                25,
                ""
            ),

            (
                "⭐",
                "10 Stars",
                10,
                20,
                ""
            ),

            (
                "🐻",
                "Bear",
                15,
                15,
                ""
            ),

            (
                "🎁",
                "Gift",
                25,
                8,
                "uncommon"
            ),

            (
                "💎",
                "Diamond",
                50,
                2,
                "rare"
            )

        ]

    },

    "pepe": {

        "price": 0,

        "items": [

            (
                "⭐",
                "1 ⭐",
                1,
                30,
                ""
            ),

            (
                "⭐",
                "3 ⭐",
                3,
                25,
                ""
            ),

            (
                "⭐",
                "5 ⭐",
                5,
                20,
                ""
            ),

            (
                "⭐",
                "10 ⭐",
                10,
                15,
                ""
            ),

            (
                "🎁",
                "25 ⭐",
                25,
                8,
                "rare"
            ),

            (
                "🐸",
                "Plush Pepe",
                100,
                2,
                "legendary"
            )

        ]

    },

    "case50": {

        "price": 50,

        "items": [

            (
                "⭐",
                "7 ⭐",
                7,
                25,
                ""
            ),

            (
                "⭐",
                "15 ⭐",
                15,
                20,
                ""
            ),

            (
                "🎁",
                "25 ⭐",
                25,
                20,
                "uncommon"
            ),

            (
                "💎",
                "50 ⭐",
                50,
                15,
                "rare"
            ),

            (
                "💎",
                "100 ⭐",
                100,
                10,
                "rare"
            ),

            (
                "🏆",
                "250 ⭐",
                250,
                5,
                "legendary"
            ),

            (
                "🐻",
                "500 ⭐",
                500,
                0.5,
                "legendary"
            ),

            (
                "🖊️",
                "1000 ⭐",
                1000,
                0.5,
                "legendary"
            )

        ]

    },

    "case250": {

        "price": 250,

        "items": [

            (
                "⭐",
                "10 ⭐",
                10,
                10,
                ""
            ),

            (
                "⭐",
                "25 ⭐",
                25,
                15,
                ""
            ),

            (
                "⭐",
                "50 ⭐",
                50,
                15,
                "uncommon"
            ),

            (
                "💎",
                "100 ⭐",
                100,
                15,
                "rare"
            ),

            (
                "💎",
                "200 ⭐",
                200,
                12,
                "rare"
            ),

            (
                "💎",
                "300 ⭐",
                300,
                10,
                "rare"
            ),

            (
                "💎",
                "450 ⭐",
                450,
                7,
                "legendary"
            ),

            (
                "🐕",
                "Snoop Dogg",
                550,
                5,
                "legendary"
            ),

            (
                "🐸",
                "Plush Pepe",
                1000,
                1,
                "legendary"
            )

        ]

    }

}


def weighted_reward(
    items
):

    total = sum(
        float(
            item[3]
        )
        for item in items
    )

    value = (
        secrets.SystemRandom().random()
        * total
    )

    for item in items:

        value -= float(
            item[3]
        )

        if value <= 0:

            return item

    return items[-1]


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    return {
        "ok": True,
        "service": "White Bear Drop API",
        "webapp": WEBAPP_URL
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "ok": True,
        "status": "online",
        "service": "White Bear Drop",
        "api": API_URL,
        "webapp": WEBAPP_URL,
        "time": int(
            time.time()
        )
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
async def status():

    return {
        "ok": True,
        "status": "connected",
        "api": API_URL,
        "webapp": WEBAPP_URL,
        "time": int(
            time.time()
        )
    }


# ============================================================
# ============================================================
# REFERRAL API
# ============================================================
class ReferralRequest(BaseModel):
    user_id: int
    referrer_id: int

@app.post("/api/referral")
async def api_referral(
    req: ReferralRequest,
    x_telegram_init_data: str | None = Header(default=None)
):
    uid, _ = resolve_user(req.user_id, x_telegram_init_data)
    created = register_referral(uid, req.referrer_id)
    return {
        "ok": True,
        "created": bool(created),
        "reward": REFERRAL_REWARD if created else 0
    }

# BALANCE
# ============================================================

@app.get(
    "/api/balance/{user_id}"
)
async def api_balance(
    user_id: int
):

    return {
        "ok": True,
        "balance": get_balance(
            user_id
        )
    }


# ============================================================
# USER
# ============================================================

@app.get(
    "/api/user/{user_id}"
)
async def api_user(
    user_id: int
):

    ensure_user(
        user_id
    )

    with closing(db()) as con:

        row = con.execute(
            """
            SELECT *
            FROM users
            WHERE user_id=?
            """,
            (
                user_id,
            )
        ).fetchone()

    if not row:

        raise HTTPException(
            404,
            "Пользователь не найден"
        )

    return {
        "ok": True,
        "user": dict(row)
    }


# ============================================================
# ADD BALANCE
# ============================================================

@app.post(
    "/api/balance/add"
)
async def add_balance(
    req: BalanceRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    try:

        balance = balance_transaction(
            uid,
            (
                f"add:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "add",
            req.amount,
            req.reason or "balance:add"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    return {
        "ok": True,
        "balance": balance
    }


# ============================================================
# DEDUCT BALANCE
# ============================================================

@app.post(
    "/api/balance/deduct"
)
async def deduct_balance(
    req: BalanceRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    try:

        balance = balance_transaction(
            uid,
            (
                f"deduct:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "deduct",
            req.amount,
            req.reason or "balance:deduct"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    return {
        "ok": True,
        "balance": balance
    }


# ============================================================
# GAME TRANSACTION
# ============================================================

@app.post(
    "/api/game/transaction"
)
async def game_transaction(
    req: TransactionRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    if req.type not in (
        "add",
        "deduct"
    ):

        raise HTTPException(
            400,
            "Неверный тип операции"
        )

    try:

        balance = balance_transaction(
            uid,
            req.operation_id,
            req.type,
            req.amount,
            req.game or "game"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    return {
        "ok": True,
        "balance": balance
    }


# ============================================================
# OPEN CASE
# ============================================================

@app.post(
    "/api/cases/open"
)
async def open_case(
    req: CaseRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    case = CASES.get(
        req.case_id
    )

    if not case:

        raise HTTPException(
            404,
            "Кейс не найден"
        )

    price = float(
        case["price"]
    )

    if abs(
        float(req.price)
        - price
    ) > 0.01:

        raise HTTPException(
            400,
            "Неверная цена кейса"
        )

    # FREE CASE
    if req.case_id == "free":

        with closing(db()) as con:

            row = con.execute(
                """
                SELECT created_at
                FROM transactions
                WHERE
                    user_id=?
                    AND reason='case_reward:free'
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    uid,
                )
            ).fetchone()

        if row:

            elapsed = (
                int(time.time())
                - int(
                    row["created_at"]
                )
            )

            if elapsed < 86400:

                remaining = (
                    86400
                    - elapsed
                )

                hours = (
                    remaining
                    // 3600
                )

                minutes = (
                    remaining
                    % 3600
                ) // 60

                raise HTTPException(
                    400,
                    (
                        "FREE кейс уже открыт. "
                        f"Следующее открытие через "
                        f"{hours}ч {minutes}м."
                    )
                )

    # PAY CASE
    if price > 0:

        try:

            balance_transaction(
                uid,
                (
                    f"case_bet:"
                    f"{uid}:"
                    f"{secrets.token_hex(12)}"
                ),
                "deduct",
                price,
                f"case:{req.case_id}"
            )

        except ValueError as e:

            raise HTTPException(
                400,
                str(e)
            )

    # REWARD
    (
        emoji,
        name,
        value,
        chance,
        rarity
    ) = weighted_reward(
        case["items"]
    )

    balance = balance_transaction(
        uid,
        (
            f"case_reward:"
            f"{uid}:"
            f"{secrets.token_hex(12)}"
        ),
        "add",
        value,
        f"case_reward:{req.case_id}"
    )

    # INVENTORY
    with closing(db()) as con:

        con.execute(
            """
            INSERT INTO inventory
            (
                user_id,
                item_key,
                name,
                emoji,
                value,
                rarity,
                created_at
            )
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                uid,
                secrets.token_hex(12),
                name,
                emoji,
                value,
                rarity,
                int(time.time())
            )
        )

        con.commit()

    return {
        "ok": True,

        "reward": {
            "emoji": emoji,
            "name": name,
            "value": value,
            "rarity": rarity,
            "chance": chance
        },

        "balance": balance
    }


# ============================================================
# BALL
# ============================================================

@app.post(
    "/api/games/ball"
)
async def ball(
    req: BallRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    try:

        balance_transaction(
            uid,
            (
                f"ball_bet:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "deduct",
            req.bet,
            "ball:bet"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    random_value = (
        secrets.SystemRandom().random()
    )

    if random_value < 0.55:

        multiplier = 0

    elif random_value < 0.90:

        multiplier = 1.5

    else:

        multiplier = 2.5

    prize = round(
        req.bet * multiplier,
        2
    )

    balance = get_balance(
        uid
    )

    if prize > 0:

        balance = balance_transaction(
            uid,
            (
                f"ball_reward:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "add",
            prize,
            "ball:reward"
        )

    return {
        "ok": True,
        "bet": req.bet,
        "multiplier": multiplier,
        "prize": prize,
        "balance": balance,
        "result": (
            f"Ставка: {req.bet} ⭐\n"
            f"Множитель: x{multiplier}\n"
            f"Выигрыш: {prize:.2f} ⭐"
        )
    }


# ============================================================
# SCRATCH
# ============================================================

@app.post(
    "/api/games/scratch"
)
async def scratch(
    req: ScratchRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    cost = 10

    try:

        balance_transaction(
            uid,
            (
                f"scratch_bet:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "deduct",
            cost,
            "scratch:bet"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    prize = secrets.choice(
        [
            0,
            0,
            0,
            3,
            5,
            10,
            15,
            20,
            25
        ]
    )

    balance = get_balance(
        uid
    )

    if prize:

        balance = balance_transaction(
            uid,
            (
                f"scratch_reward:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "add",
            prize,
            "scratch:reward"
        )

    return {
        "ok": True,
        "prize": prize,
        "balance": balance,
        "result": (
            f"Скретч-карта: {cost} ⭐\n"
            f"Выигрыш: {prize} ⭐"
        )
    }


# ============================================================
# UPGRADE
# ============================================================

@app.post(
    "/api/games/upgrade"
)
async def upgrade(
    req: UpgradeRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    if not req.item_id.strip():

        raise HTTPException(
            400,
            "Не указан предмет"
        )

    try:

        balance_transaction(
            uid,
            (
                f"upgrade_bet:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "deduct",
            req.bet,
            "upgrade:bet"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    success = (
        secrets.SystemRandom().random()
        < 0.45
    )

    if success:

        prize = req.bet * 2

        balance = balance_transaction(
            uid,
            (
                f"upgrade_reward:"
                f"{uid}:"
                f"{secrets.token_hex(12)}"
            ),
            "add",
            prize,
            "upgrade:reward"
        )

        result = (
            "🎉 Апгрейд успешен!\n"
            f"Выигрыш: +{prize} ⭐"
        )

    else:

        prize = 0

        balance = get_balance(
            uid
        )

        result = (
            "❌ Апгрейд не удался."
        )

    return {
        "ok": True,
        "success": success,
        "prize": prize,
        "balance": balance,
        "result": result
    }


# ============================================================
# PROMOCODES
# ============================================================

PROMOS = {
    "200": 200,
    "met200": 200,
    "Dave": 200000,
    "whitebear5": 5
}

# Максимум активаций whitebear5 на разных аккаунтах.
WHITEBEAR5_MAX_USES = 100

@app.post(
    "/api/promo/activate"
)
async def promo(
    req: PromoRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    code = (
        req.code
        .strip()
        .lower()
    )

    if code not in PROMOS:

        raise HTTPException(
            400,
            "Неверный промокод"
        )

    amount = PROMOS[
        code
    ]

    with closing(db()) as con:

        try:

            con.execute(
                "BEGIN IMMEDIATE"
            )

            used = con.execute(
                """
                SELECT 1
                FROM promo_uses
                WHERE
                    user_id=?
                    AND code=?
                """,
                (
                    uid,
                    code
                )
            ).fetchone()

            if used:

                con.rollback()

                raise HTTPException(
                    400,
                    "Этот промокод уже использован"
                )

            # Общий лимит whitebear5: первые 100 разных аккаунтов.
            if code == "whitebear5":
                total_uses = con.execute(
                    """
                    SELECT COUNT(*)
                    FROM promo_uses
                    WHERE code=?
                    """,
                    (code,)
                ).fetchone()[0]

                if int(total_uses) >= WHITEBEAR5_MAX_USES:
                    con.rollback()
                    raise HTTPException(
                        400,
                        "Промокод whitebear5 больше недоступен: лимит 100 активаций исчерпан"
                    )

            con.execute(
                """
                INSERT INTO promo_uses
                (
                    user_id,
                    code,
                    amount,
                    created_at
                )
                VALUES (?,?,?,?)
                """,
                (
                    uid,
                    code,
                    amount,
                    int(time.time())
                )
            )

            con.commit()

        except HTTPException:

            raise

        except Exception:

            con.rollback()

            raise HTTPException(
                500,
                "Ошибка активации промокода"
            )

    try:

        balance = balance_transaction(
            uid,
            (
                f"promo:"
                f"{uid}:"
                f"{code}:"
                f"{secrets.token_hex(8)}"
            ),
            "add",
            amount,
            f"promo:{code}"
        )

    except Exception:

        with closing(db()) as con:

            con.execute(
                """
                DELETE FROM promo_uses
                WHERE
                    user_id=?
                    AND code=?
                """,
                (
                    uid,
                    code
                )
            )

            con.commit()

        raise

    return {
        "ok": True,
        "message": (
            f"Промокод {code} активирован! "
            f"+{amount} ⭐"
        ),
        "balance": balance
    }


# ============================================================
# INVENTORY
# ============================================================

@app.get(
    "/api/inventory/{user_id}"
)
async def inventory(
    user_id: int
):

    ensure_user(
        user_id
    )

    with closing(db()) as con:

        rows = con.execute(
            """
            SELECT
                id,
                item_key,
                name,
                emoji,
                value,
                rarity,
                created_at
            FROM inventory
            WHERE user_id=?
            ORDER BY id DESC
            """,
            (
                user_id,
            )
        ).fetchall()

    return {
        "ok": True,
        "items": [
            dict(row)
            for row in rows
        ]
    }


# ============================================================
# SELL ITEM
# ============================================================

@app.post(
    "/api/inventory/sell"
)
async def sell(
    req: SellRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    try:

        item_id = int(
            req.item_id
        )

    except ValueError:

        raise HTTPException(
            400,
            "Неверный ID предмета"
        )

    with closing(db()) as con:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        item = con.execute(
            """
            SELECT *
            FROM inventory
            WHERE
                id=?
                AND user_id=?
            """,
            (
                item_id,
                uid
            )
        ).fetchone()

        if not item:

            con.rollback()

            raise HTTPException(
                404,
                "Предмет не найден"
            )

        con.execute(
            """
            DELETE FROM inventory
            WHERE
                id=?
                AND user_id=?
            """,
            (
                item_id,
                uid
            )
        )

        con.commit()

    value = float(
        item["value"]
    )

    try:

        balance = balance_transaction(
            uid,
            (
                f"sell:"
                f"{uid}:"
                f"{item_id}:"
                f"{secrets.token_hex(8)}"
            ),
            "add",
            value,
            f"inventory:sell:{item_id}"
        )

    except Exception:

        with closing(db()) as con:

            con.execute(
                """
                INSERT INTO inventory
                (
                    user_id,
                    item_key,
                    name,
                    emoji,
                    value,
                    rarity,
                    created_at
                )
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    uid,
                    item["item_key"],
                    item["name"],
                    item["emoji"],
                    item["value"],
                    item["rarity"],
                    item["created_at"]
                )
            )

            con.commit()

        raise HTTPException(
            500,
            "Не удалось начислить продажу"
        )

    return {
        "ok": True,
        "message": (
            f"Предмет продан за "
            f"{value:.2f} ⭐"
        ),
        "balance": balance
    }


# ============================================================
# TELEGRAM STARS PAYMENT API
# ============================================================

@app.post(
    "/api/payments/stars"
)
async def create_payment(
    req: PaymentRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    payload = (
        f"stars:"
        f"{uid}:"
        f"{req.amount}:"
        f"{secrets.token_urlsafe(16)}"
    )

    with closing(db()) as con:

        con.execute(
            """
            INSERT INTO payments
            (
                user_id,
                payload,
                amount,
                status,
                created_at
            )
            VALUES (?,?,?,?,?)
            """,
            (
                uid,
                payload,
                req.amount,
                "pending",
                int(time.time())
            )
        )

        con.commit()

    try:

        invoice_link = (
            await bot.create_invoice_link(
                title=(
                    f"Пополнение "
                    f"{req.amount} ⭐"
                ),
                description=(
                    "Пополнение баланса "
                    "White Bear Drop"
                ),
                payload=payload,
                currency="XTR",
                prices=[
                    LabeledPrice(
                        label=(
                            f"{req.amount} Stars"
                        ),
                        amount=req.amount
                    )
                ]
            )
        )

    except Exception as e:

        with closing(db()) as con:

            con.execute(
                """
                DELETE FROM payments
                WHERE payload=?
                """,
                (
                    payload,
                )
            )

            con.commit()

        raise HTTPException(
            500,
            f"Ошибка создания платежа: {e}"
        )

    return {
        "ok": True,
        "invoice_link": invoice_link,
        "amount": req.amount
    }


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🎮 Играть",
                    web_app=WebAppInfo(
                        url=WEBAPP_URL
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    text="💰 Баланс",
                    callback_data="balance"
                ),

                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="profile"
                )
            ],

            [
                InlineKeyboardButton(
                    text="⭐ Пополнить",
                    callback_data="deposit"
                ),

                InlineKeyboardButton(
                    text="📎 Реферал",
                    callback_data="referral"
                )
            ]

        ]
    )


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
# /START
# ============================================================

@dp.message(
    CommandStart()
)
async def start(
    message: Message
):

    # Referral deep-link: /start ref_<Telegram user ID>
    # Reward only a user who did not exist before this /start.
    try:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref_"):
            referrer_id = int(parts[1][4:])
            new_user_id = int(message.from_user.id)

            with closing(db()) as con:
                existed = con.execute(
                    "SELECT 1 FROM users WHERE user_id=?",
                    (new_user_id,)
                ).fetchone()

            if not existed:
                register_referral(new_user_id, referrer_id)
    except Exception:
        log.exception("Referral processing failed")


    if not message.from_user:
        return

    ensure_user(
        message.from_user.id,
        message.from_user
    )

    balance = get_balance(
        message.from_user.id
    )

    await message.answer(
        "🐻‍❄️ <b>White Bear Drop</b>\n\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"
        "🎮 Нажми "
        "<b>«Играть»</b>, "
        "чтобы открыть мини-приложение.",
        reply_markup=main_keyboard()
    )


# ============================================================
# ============================================================
# /REF
# ============================================================
@dp.message(
    Command("ref")
)
async def referral_cmd(
    message: Message
):
    if not message.from_user:
        return

    me = await bot.get_me()
    if not me.username:
        await message.answer("Не удалось получить username бота.")
        return

    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"

    await message.answer(
        "👥 <b>Ваша реферальная ссылка</b>\n\n"
        "Отправьте её другу.\n"
        "После первого входа нового пользователя вы оба получите "
        f"<b>{REFERRAL_REWARD:.0f} ⭐</b>.\n\n"
        f"<code>{link}</code>"
    )

# /GAME
# ============================================================

@dp.message(
    Command("game")
)
async def game_cmd(
    message: Message
):

    await message.answer(
        "🎮 <b>White Bear Drop</b>\n\n"
        "Нажми кнопку ниже.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[

                [
                    InlineKeyboardButton(
                        text="🎮 Играть",
                        web_app=WebAppInfo(
                            url=WEBAPP_URL
                        )
                    )
                ]

            ]
        )
    )


# ============================================================
# /BALANCE
# ============================================================

@dp.message(
    Command("balance")
)
async def balance_cmd(
    message: Message
):

    if not message.from_user:
        return

    ensure_user(
        message.from_user.id,
        message.from_user
    )

    balance = get_balance(
        message.from_user.id
    )

    await message.answer(
        "💰 <b>Ваш баланс</b>\n\n"
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard()
    )


# ============================================================
# /PROFILE
# ============================================================

@dp.message(
    Command("profile")
)
async def profile_cmd(
    message: Message
):

    if not message.from_user:
        return

    ensure_user(
        message.from_user.id,
        message.from_user
    )

    balance = get_balance(
        message.from_user.id
    )

    await message.answer(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: "
        f"<code>{message.from_user.id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard()
    )


# ============================================================
# DEPOSIT
# ============================================================

@dp.callback_query(
    F.data == "deposit"
)
async def deposit_cb(
    callback: types.CallbackQuery
):

    await callback.message.edit_text(
        "⭐ <b>Пополнение баланса</b>\n\n"
        "Выбери количество Stars:",
        reply_markup=deposit_keyboard()
    )

    await callback.answer()


# ============================================================
# BUY STARS
# ============================================================

@dp.callback_query(
    F.data.startswith("buy_")
)
async def buy_cb(
    callback: types.CallbackQuery
):

    try:

        amount = int(
            callback.data.split(
                "_",
                1
            )[1]
        )

        if amount < 1:
            raise ValueError

        if amount > 100000:
            raise ValueError

        ensure_user(
            callback.from_user.id,
            callback.from_user
        )

        payload = (
            f"stars:"
            f"{callback.from_user.id}:"
            f"{amount}:"
            f"{secrets.token_urlsafe(12)}"
        )

        with closing(db()) as con:

            con.execute(
                """
                INSERT INTO payments
                (
                    user_id,
                    payload,
                    amount,
                    status,
                    created_at
                )
                VALUES (?,?,?,?,?)
                """,
                (
                    callback.from_user.id,
                    payload,
                    amount,
                    "pending",
                    int(time.time())
                )
            )

            con.commit()

        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=(
                f"Пополнение "
                f"{amount} ⭐"
            ),
            description=(
                "Пополнение игрового "
                "баланса White Bear Drop"
            ),
            payload=payload,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=(
                        f"{amount} Stars"
                    ),
                    amount=amount
                )
            ]
        )

        await callback.answer(
            "💳 Счёт отправлен"
        )

    except Exception as e:

        log.exception(
            "Invoice error: %s",
            e
        )

        await callback.answer(
            "❌ Не удалось создать оплату",
            show_alert=True
        )


# ============================================================
# BALANCE CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "balance"
)
async def balance_cb(
    callback: types.CallbackQuery
):

    ensure_user(
        callback.from_user.id,
        callback.from_user
    )

    balance = get_balance(
        callback.from_user.id
    )

    await callback.message.edit_text(
        "💰 <b>Ваш баланс</b>\n\n"
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# PROFILE CALLBACK
# ============================================================

@dp.callback_query(
    F.data == "profile"
)
async def profile_cb(
    callback: types.CallbackQuery
):

    ensure_user(
        callback.from_user.id,
        callback.from_user
    )

    balance = get_balance(
        callback.from_user.id
    )

    await callback.message.edit_text(
        "👤 <b>Профиль</b>\n\n"
        f"🆔 Telegram ID: "
        f"<code>{callback.from_user.id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# REFERRAL
# ============================================================

@dp.callback_query(
    F.data == "referral"
)
async def referral_cb(
    callback: types.CallbackQuery
):

    link = (
        "https://t.me/"
        "White_Bear_ROBOT"
        f"?start=ref_{callback.from_user.id}"
    )

    await callback.message.edit_text(
        "📎 <b>Реферальная система</b>\n\n"
        f"🔗 <code>{link}</code>",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# BACK
# ============================================================

@dp.callback_query(
    F.data == "back"
)
async def back_cb(
    callback: types.CallbackQuery
):

    ensure_user(
        callback.from_user.id,
        callback.from_user
    )

    balance = get_balance(
        callback.from_user.id
    )

    await callback.message.edit_text(
        "🐻‍❄️ <b>White Bear Drop</b>\n\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"
        "🎮 Нажми «Играть».",
        reply_markup=main_keyboard()
    )

    await callback.answer()


# ============================================================
# PRE CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    if (
        query.currency != "XTR"
        or query.total_amount <= 0
    ):

        await query.answer(
            ok=False,
            error_message="Неверный платёж"
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

    if not payment:
        return

    if not message.from_user:
        return

    if payment.currency != "XTR":
        return

    uid = (
        message.from_user.id
    )

    payload = (
        payment.invoice_payload
    )

    charge_id = (
        payment.telegram_payment_charge_id
    )

    amount = int(
        payment.total_amount
    )

    with closing(db()) as con:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        row = con.execute(
            """
            SELECT *
            FROM payments
            WHERE payload=?
            """,
            (
                payload,
            )
        ).fetchone()

        if not row:

            con.rollback()

            await message.answer(
                "⚠️ Платёж получен, "
                "но счёт не найден."
            )

            return

        if int(
            row["user_id"]
        ) != uid:

            con.rollback()

            await message.answer(
                "⚠️ Этот платёж "
                "принадлежит другому пользователю."
            )

            return

        if int(
            row["amount"]
        ) != amount:

            con.rollback()

            await message.answer(
                "⚠️ Сумма платежа "
                "не совпадает."
            )

            return

        if row["status"] == "paid":

            con.rollback()

            await message.answer(
                "ℹ️ Этот платёж "
                "уже был зачислен."
            )

            return

        con.execute(
            """
            UPDATE payments
            SET
                status='paid',
                telegram_charge_id=?,
                provider_charge_id=?,
                paid_at=?
            WHERE payload=?
            """,
            (
                charge_id,
                getattr(
                    payment,
                    "provider_payment_charge_id",
                    ""
                ) or "",
                int(time.time()),
                payload
            )
        )

        con.commit()

    try:

        balance = balance_transaction(
            uid,
            f"payment:{charge_id}",
            "add",
            amount,
            "telegram_stars"
        )

    except Exception:

        await message.answer(
            "⚠️ Платёж подтверждён, "
            "но произошла ошибка "
            "зачисления."
        )

        return

    await message.answer(
        "✅ <b>Оплата получена!</b>\n\n"
        f"⭐ Зачислено: <b>+{amount}</b>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>",
        reply_markup=main_keyboard()
    )


# ============================================================
# BOT SETUP
# ============================================================

async def setup_bot():

    # Убираем старый webhook.
    # Это исправляет TelegramConflictError.
    await bot.delete_webhook(
        drop_pending_updates=False
    )

    await bot.set_my_commands(
        [
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
    )

    # Кнопка ИГРАТЬ возле поля ввода
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🎮 Играть",
            web_app=WebAppInfo(
                url=WEBAPP_URL
            )
        )
    )

    log.info(
        "Telegram configured"
    )

    log.info(
        "WEBAPP: %s",
        WEBAPP_URL
    )


# ============================================================
# API SERVER
# ============================================================

async def run_api():

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )

    server = uvicorn.Server(
        config
    )

    await server.serve()


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    log.info(
        "================================="
    )

    log.info(
        "🐻‍❄️ WHITE BEAR DROP"
    )

    log.info(
        "================================="
    )

    log.info(
        "API: %s",
        API_URL
    )

    log.info(
        "WEBAPP: %s",
        WEBAPP_URL
    )

    log.info(
        "PORT: %s",
        PORT
    )

    log.info(
        "================================="
    )

    await setup_bot()

    await asyncio.gather(
        run_api(),
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        log.info(
            "Бот остановлен"
        )