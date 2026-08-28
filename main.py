import os
import json
import time
import hmac
import hashlib
import secrets
import sqlite3
import asyncio
from contextlib import closing
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, LabeledPrice, PreCheckoutQuery


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://whitebear.bothost.tech"
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "whitebear.db"
)

HOST = os.getenv(
    "HOST",
    "0.0.0.0"
)

PORT = int(
    os.getenv(
        "PORT",
        "8000"
    )
)

WEBAPP_ORIGIN = os.getenv(
    "WEBAPP_ORIGIN",
    "*"
).strip()


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Добавь BOT_TOKEN в переменные окружения Replit."
    )


# =========================================================
# TELEGRAM + FASTAPI
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()

app = FastAPI(
    title="White Bear Drop API",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        ["*"]
        if WEBAPP_ORIGIN == "*"
        else [WEBAPP_ORIGIN]
    ),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# DATABASE
# =========================================================

def db():
    con = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    con.row_factory = sqlite3.Row

    con.execute(
        "PRAGMA journal_mode=WAL"
    )

    con.execute(
        "PRAGMA foreign_keys=ON"
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
                status TEXT NOT NULL,
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


def ensure_user(
    user_id: int,
    telegram_user=None
):

    now = int(
        time.time()
    )

    with closing(db()) as con:

        row = con.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
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
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    0,
                    getattr(
                        telegram_user,
                        "username",
                        ""
                    ) or "",
                    getattr(
                        telegram_user,
                        "first_name",
                        ""
                    ) or "",
                    getattr(
                        telegram_user,
                        "last_name",
                        ""
                    ) or "",
                    now,
                    now
                )
            )

        elif telegram_user is not None:

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
                    getattr(
                        telegram_user,
                        "username",
                        ""
                    ) or "",
                    getattr(
                        telegram_user,
                        "first_name",
                        ""
                    ) or "",
                    getattr(
                        telegram_user,
                        "last_name",
                        ""
                    ) or "",
                    now,
                    user_id
                )
            )

        con.commit()


def get_balance(
    user_id: int
) -> float:

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
            (user_id,)
        ).fetchone()

        if not row:
            return 0.0

        return float(
            row["balance"]
        )


# =========================================================
# ATOMIC BALANCE TRANSACTIONS
# =========================================================

def transaction(
    user_id: int,
    operation_id: str,
    transaction_type: str,
    amount: float,
    reason: str
):

    amount = round(
        float(amount),
        2
    )

    if amount <= 0:
        raise ValueError(
            "Amount must be positive"
        )

    with closing(db()) as con:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        existing = con.execute(
            """
            SELECT *
            FROM transactions
            WHERE operation_id=?
            """,
            (operation_id,)
        ).fetchone()

        if existing:

            row = con.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            con.commit()

            return (
                float(row["balance"])
                if row
                else 0.0
            )

        ensure_user(
            user_id
        )

        row = con.execute(
            """
            SELECT balance
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()

        balance = float(
            row["balance"]
        )

        if transaction_type == "deduct":

            if balance < amount:

                con.rollback()

                raise ValueError(
                    "Недостаточно ⭐"
                )

            new_balance = round(
                balance - amount,
                2
            )

        elif transaction_type == "add":

            new_balance = round(
                balance + amount,
                2
            )

        else:

            con.rollback()

            raise ValueError(
                "Unknown transaction type"
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
                int(time.time()),
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
                transaction_type,
                amount,
                reason[:200],
                int(time.time())
            )
        )

        con.commit()

        return new_balance


# =========================================================
# TELEGRAM WEB APP AUTH
# =========================================================

def verify_init_data(
    init_data: str
):

    if not init_data:
        return None

    try:

        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = pairs.pop(
            "hash",
            None
        )

        auth_date = int(
            pairs.get(
                "auth_date",
                "0"
            )
        )

        if not received_hash:
            return None

        if not auth_date:
            return None

        # initData is accepted for up to 24 hours.
        if (
            abs(
                int(time.time()) -
                auth_date
            )
            > 86400
        ):
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                pairs.items()
            )
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

        raw_user = pairs.get(
            "user"
        )

        if not raw_user:
            return None

        return json.loads(
            raw_user
        )

    except Exception:
        return None


def resolve_user(
    user_id: int | None,
    init_data: str | None
):

    verified = verify_init_data(
        init_data or ""
    )

    if verified:

        verified_id = int(
            verified["id"]
        )

        if (
            user_id is not None
            and int(user_id) != verified_id
        ):
            raise HTTPException(
                403,
                "Telegram ID mismatch"
            )

        ensure_user(
            verified_id
        )

        return (
            verified_id,
            verified
        )

    # Compatibility with the current HTML.
    # Once X-Telegram-Init-Data is added to the HTML,
    # this fallback is no longer needed for normal use.
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


# =========================================================
# REQUEST MODELS
# =========================================================

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




class AddInventoryRequest(BaseModel):

    user_id: int

    item_key: str

    name: str

    emoji: str

    value: float = Field(
        ge=0,
        le=100000000
    )

    rarity: str = ""


# =========================================================
# HEALTH
# =========================================================

@app.get(
    "/health"
)
async def health():

    return {
        "ok": True,
        "service": "whitebear",
        "time": int(
            time.time()
        )
    }


# =========================================================
# BALANCE
# =========================================================

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


@app.post(
    "/api/balance/add"
)
async def balance_add(
    req: BalanceRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    operation_id = (
        f"manual_add_"
        f"{uid}_"
        f"{secrets.token_hex(8)}"
    )

    new_balance = transaction(
        uid,
        operation_id,
        "add",
        req.amount,
        req.reason or "manual:add"
    )

    return {
        "ok": True,
        "balance": new_balance
    }


@app.post(
    "/api/balance/deduct"
)
async def balance_deduct(
    req: BalanceRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    operation_id = (
        f"manual_deduct_"
        f"{uid}_"
        f"{secrets.token_hex(8)}"
    )

    try:

        new_balance = transaction(
            uid,
            operation_id,
            "deduct",
            req.amount,
            req.reason or "manual:deduct"
        )

    except ValueError as e:

        raise HTTPException(
            400,
            str(e)
        )

    return {
        "ok": True,
        "balance": new_balance
    }


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

    if (
        not req.operation_id
        or len(req.operation_id) > 120
    ):
        raise HTTPException(
            400,
            "Invalid operation_id"
        )

    if req.type not in {
        "add",
        "deduct"
    }:
        raise HTTPException(
            400,
            "Invalid transaction type"
        )

    try:

        new_balance = transaction(
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
        "balance": new_balance
    }


# =========================================================
# CASES
# =========================================================

CASES = {

    "free": {
        "price": 0,
        "items": [
            (
                "3 ⭐",
                "3 Stars",
                3,
                23,
                ""
            ),
            (
                "5 ⭐",
                "5 Stars",
                5,
                22,
                ""
            ),
            (
                "7 ⭐",
                "7 Stars",
                7,
                27,
                ""
            ),
            (
                "🐻",
                "Bear",
                15,
                12,
                ""
            ),
            (
                "🎁",
                "Gift",
                25,
                6,
                ""
            ),
            (
                "🚀",
                "Rocket",
                50,
                5,
                "uncommon"
            ),
            (
                "🍦",
                "Vice Cream",
                450,
                0.00001,
                "rare"
            )
        ]
    },

    "pepe": {
        "price": 0,
        "items": [
            (
                "💰",
                "0.5 на баланс",
                0.5,
                24.009997,
                ""
            ),
            (
                "💰",
                "1 на баланс",
                1,
                25,
                ""
            ),
            (
                "💰",
                "3 на баланс",
                3,
                15,
                ""
            ),
            (
                "💰",
                "5 на баланс",
                5,
                15,
                ""
            ),
            (
                "💰",
                "7 на баланс",
                7,
                10,
                ""
            ),
            (
                "💘",
                "15 ⭐",
                15,
                6,
                ""
            ),
            (
                "🎁",
                "25",
                25,
                4.999,
                ""
            ),
            (
                "🐸",
                "Plush Pepe",
                800000,
                0.001,
                "legendary"
            )
        ]
    },

    "case50": {
        "price": 50,
        "items": [
            (
                "🐻",
                "Toy Bear",
                5000,
                0.0001,
                "legendary"
            ),
            (
                "🖊️",
                "Fine Pen",
                1000,
                0.0001,
                "legendary"
            ),
            (
                "💎",
                "Diamond",
                100,
                15,
                "rare"
            ),
            (
                "🏆",
                "Trophy",
                100,
                15,
                "rare"
            ),
            (
                "🎂",
                "Cake",
                50,
                20,
                "uncommon"
            ),
            (
                "🎁",
                "Gift",
                25,
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
                "7 ⭐",
                "7 Stars",
                7,
                15,
                ""
            )
        ]
    },

    "case250": {
        "price": 250,
        "items": [
            (
                "💰",
                "10 на баланс",
                10,
                3,
                ""
            ),
            (
                "🐻",
                "15 ⭐",
                15,
                2.5,
                ""
            ),
            (
                "🎁",
                "25 ⭐",
                25,
                7.5,
                ""
            ),
            (
                "💐",
                "50 ⭐",
                50,
                10,
                "uncommon"
            ),
            (
                "💍",
                "100 ⭐",
                100,
                11.5,
                "rare"
            ),
            (
                "💎",
                "100 ⭐",
                100,
                11.5,
                "rare"
            ),
            (
                "💰",
                "200 на баланс",
                200,
                15,
                "rare"
            ),
            (
                "💰",
                "250 на баланс",
                250,
                7,
                "rare"
            ),
            (
                "💰",
                "300 на баланс",
                300,
                6,
                "rare"
            ),
            (
                "💰",
                "350 на баланс",
                350,
                3,
                "legendary"
            ),
            (
                "💰",
                "450 на баланс",
                450,
                3,
                "legendary"
            ),
            (
                "🍦",
                "Vice Cream",
                450,
                0.0005,
                "rare"
            ),
            (
                "🐕",
                "Snoop Dogg",
                550,
                0.00045,
                "legendary"
            )
        ]
    }
}


def choose_weighted(
    items
):

    total = sum(
        float(item[3])
        for item in items
    )

    random_value = (
        secrets.SystemRandom().random()
        * total
    )

    for item in items:

        random_value -= float(
            item[3]
        )

        if random_value <= 0:
            return item

    return items[-1]


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

    server_price = float(
        case["price"]
    )

    if abs(
        float(req.price)
        - server_price
    ) > 0.001:

        raise HTTPException(
            400,
            "Цена кейса не совпадает с серверной"
        )

    now = int(
        time.time()
    )

    # FREE: once per 24 hours.
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
                (uid,)
            ).fetchone()

        if row:

            if (
                now -
                int(row["created_at"])
                < 86400
            ):

                raise HTTPException(
                    400,
                    "FREE кейс можно открыть раз в сутки"
                )

    # The endpoint itself performs the purchase.
    # The HTML must NOT deduct the case price separately.
    if server_price > 0:

        try:

            transaction(
                uid,
                (
                    f"case_bet_"
                    f"{uid}_"
                    f"{secrets.token_hex(10)}"
                ),
                "deduct",
                server_price,
                f"case:{req.case_id}"
            )

        except ValueError as e:

            raise HTTPException(
                400,
                str(e)
            )

    emoji, name, value, chance, rarity = (
        choose_weighted(
            case["items"]
        )
    )

    reward_operation_id = (
        f"case_reward_"
        f"{uid}_"
        f"{secrets.token_hex(10)}"
    )

    new_balance = transaction(
        uid,
        reward_operation_id,
        "add",
        float(value),
        f"case_reward:{req.case_id}"
    )

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
                reward_operation_id,
                name,
                emoji,
                float(value),
                rarity,
                now
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
        "balance": new_balance
    }


# =========================================================
# BALL
# =========================================================

@app.post(
    "/api/games/ball"
)
async def game_ball(
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

        transaction(
            uid,
            (
                f"ball_bet_"
                f"{uid}_"
                f"{secrets.token_hex(10)}"
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

    rng = (
        secrets.SystemRandom()
        .random()
    )

    if rng < 0.55:
        multiplier = 0

    elif rng < 0.90:
        multiplier = 1.5

    else:
        multiplier = 2.5

    prize = round(
        req.bet * multiplier,
        2
    )

    if prize > 0:

        new_balance = transaction(
            uid,
            (
                f"ball_reward_"
                f"{uid}_"
                f"{secrets.token_hex(10)}"
            ),
            "add",
            prize,
            "ball:reward"
        )

    else:

        new_balance = get_balance(
            uid
        )

    return {
        "ok": True,
        "result": (
            f"Ставка: {req.bet} ⭐\n"
            f"Выигрыш: {prize:.2f} ⭐"
        ),
        "bet": req.bet,
        "prize": prize,
        "balance": new_balance
    }


# =========================================================
# SCRATCH
# =========================================================

@app.post(
    "/api/games/scratch"
)
async def game_scratch(
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

        transaction(
            uid,
            (
                f"scratch_bet_"
                f"{uid}_"
                f"{secrets.token_hex(10)}"
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

    prizes = [
        0,
        0,
        3,
        5,
        10,
        15,
        20,
        25
    ]

    prize = secrets.choice(
        prizes
    )

    if prize > 0:

        new_balance = transaction(
            uid,
            (
                f"scratch_reward_"
                f"{uid}_"
                f"{secrets.token_hex(10)}"
            ),
            "add",
            prize,
            "scratch:reward"
        )

    else:

        new_balance = get_balance(
            uid
        )

    return {
        "ok": True,
        "result": (
            f"Билет за {cost} ⭐\n"
            f"Выигрыш: {prize} ⭐"
        ),
        "prize": prize,
        "balance": new_balance
    }


# =========================================================
# UPGRADE
# =========================================================

@app.post(
    "/api/games/upgrade"
)
async def game_upgrade(
    req: UpgradeRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    try:

        transaction(
            uid,
            (
                f"upgrade_bet_"
                f"{uid}_"
                f"{secrets.token_hex(10)}"
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
        secrets.SystemRandom()
        .random()
        < 0.45
    )

    prize = (
        req.bet * 2
        if success
        else 0
    )

    if prize:

        new_balance = transaction(
            uid,
            (
                f"upgrade_reward_"
                f"{uid}_"
                f"{secrets.token_hex(10)}"
            ),
            "add",
            prize,
            "upgrade:reward"
        )

    else:

        new_balance = get_balance(
            uid
        )

    return {
        "ok": True,
        "result": (
            f"Успешный апгрейд: +{prize} ⭐"
            if success
            else "Апгрейд не удался"
        ),
        "success": success,
        "prize": prize,
        "balance": new_balance
    }


# =========================================================
# PROMOCODES
# =========================================================

PROMOS = {
    "200": 200,
    "met200": 200
}


@app.post(
    "/api/promo/activate"
)
async def activate_promo(
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

    # Reserve the promo atomically.
    with closing(db()) as con:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        used = con.execute(
            """
            SELECT id
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

    try:

        new_balance = transaction(
            uid,
            (
                f"promo_"
                f"{uid}_"
                f"{code}_"
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
            f"Промокод активирован: "
            f"+{amount} ⭐"
        ),
        "balance": new_balance
    }


# =========================================================
# INVENTORY
# =========================================================

@app.post(
    "/api/inventory/add"
)
async def add_inventory(
    req: AddInventoryRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    item_key = str(
        req.item_key
    ).strip()

    name = str(
        req.name
    ).strip()[:200]

    emoji = str(
        req.emoji
    ).strip()[:50]

    rarity = str(
        req.rarity
    ).strip()[:50]

    if not item_key or not name:
        raise HTTPException(
            400,
            "Некорректные данные предмета"
        )

    now = int(
        time.time()
    )

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
                item_key[:200],
                name,
                emoji,
                float(req.value),
                rarity,
                now
            )
        )

        item_id = con.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        con.commit()

    return {
        "ok": True,
        "item": {
            "id": int(item_id),
            "item_key": item_key[:200],
            "name": name,
            "emoji": emoji,
            "value": float(req.value),
            "rarity": rarity,
            "created_at": now
        }
    }


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


@app.post(
    "/api/inventory/sell"
)
async def sell_inventory(
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
            "Некорректный ID предмета"
        )

    with closing(db()) as con:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        row = con.execute(
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

        if not row:

            con.rollback()

            raise HTTPException(
                404,
                "Предмет не найден"
            )

        con.execute(
            """
            DELETE FROM inventory
            WHERE id=?
            """,
            (
                row["id"],
            )
        )

        con.commit()

    new_balance = transaction(
        uid,
        (
            f"sell_"
            f"{uid}_"
            f"{row['id']}_"
            f"{secrets.token_hex(8)}"
        ),
        "add",
        float(row["value"]),
        f"inventory:sell:{row['id']}"
    )

    return {
        "ok": True,
        "message": (
            f"Продано за "
            f"{float(row['value']):.2f} ⭐"
        ),
        "balance": new_balance
    }


# =========================================================
# TELEGRAM STARS INVOICES
# =========================================================

@app.post(
    "/api/payments/stars"
)
async def create_stars_invoice(
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

    invoice_link = (
        await bot.create_invoice_link(
            title=(
                f"Пополнение "
                f"на {req.amount} ⭐"
            ),
            description=(
                "Пополнение игрового "
                "баланса White Bear Drop."
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

    return {
        "ok": True,
        "success": True,
        "invoice_link": invoice_link,
        "amount": req.amount
    }


# =========================================================
# TELEGRAM BOT
# =========================================================

@dp.message(
    CommandStart()
)
async def start_handler(
    message: Message
):

    if not message.from_user:
        return

    ensure_user(
        message.from_user.id,
        message.from_user
    )

    await message.answer(
        "🐻‍❄️ White Bear Drop\n\n"
        "Открой приложение и играй за ⭐."
    )


@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    await query.answer(
        ok=True
    )


@dp.message(
    F.successful_payment
)
async def successful_payment(
    message: Message
):

    payment = (
        message.successful_payment
    )

    user = (
        message.from_user
    )

    if not payment or not user:
        return

    payload = (
        payment.invoice_payload
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
                "но счёт не найден. "
                "Обратись к администратору."
            )

            return

        if row["status"] == "paid":

            con.rollback()

            await message.answer(
                "✅ Этот платёж уже обработан."
            )

            return

        if (
            int(row["user_id"])
            != int(user.id)
        ):

            con.rollback()

            await message.answer(
                "⚠️ Платёж принадлежит "
                "другому пользователю."
            )

            return

        if (
            int(row["amount"])
            != int(payment.total_amount)
        ):

            con.rollback()

            await message.answer(
                "⚠️ Сумма платежа "
                "не совпала."
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
                payment.telegram_payment_charge_id,
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

        new_balance = transaction(
            user.id,
            (
                "payment_reward:"
                f"{payment.telegram_payment_charge_id}"
            ),
            "add",
            int(
                payment.total_amount
            ),
            f"telegram_stars:{payload}"
        )

    except Exception as error:

        print(
            "PAYMENT CREDIT ERROR:",
            repr(error)
        )

        await message.answer(
            "⚠️ Платёж подтверждён Telegram, "
            "но зачисление не завершилось. "
            "Обратись к администратору."
        )

        return

    await message.answer(
        "✅ Оплата получена!\n"
        f"Зачислено: +{payment.total_amount} ⭐\n"
        f"Игровой баланс: {new_balance:.2f} ⭐"
    )


# =========================================================
# START API + BOT
# =========================================================

async def run_api():

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info"
    )

    server = uvicorn.Server(
        config
    )

    await server.serve()


async def main():

    init_db()

    print(
        f"White Bear API: "
        f"http://{HOST}:{PORT}"
    )

    print(
        f"WebApp URL: "
        f"{WEBAPP_URL}"
    )

    await asyncio.gather(
        run_api(),
        dp.start_polling(
            bot
        )
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
