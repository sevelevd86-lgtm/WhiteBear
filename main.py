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

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://whitebear.bothost.tech"
).strip()


# ============================================================
# ПРОВЕРКА TOKEN
# ============================================================

if not BOT_TOKEN:

    raise RuntimeError(
        "BOT_TOKEN не задан в Secrets."
    )


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="White Bear Drop API",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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


# ============================================================
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

        return float(
            row["balance"]
        )


# ============================================================
# БЕЗОПАСНАЯ ТРАНЗАКЦИЯ БАЛАНСА
# ============================================================

def balance_transaction(
    user_id: int,
    operation_id: str,
    operation_type: str,
    amount: float,
    reason: str = ""
):

    amount = round(
        float(amount),
        2
    )

    if amount <= 0:

        raise ValueError(
            "Сумма должна быть больше 0"
        )

    if operation_type not in (
        "add",
        "deduct"
    ):

        raise ValueError(
            "Неверный тип операции"
        )

    now = int(
        time.time()
    )

    with closing(db()) as con:

        # ----------------------------------------------------
        # ВАЖНО:
        # НЕ вызываем ensure_user() после BEGIN IMMEDIATE.
        # Иначе создаётся второе соединение SQLite и возможен
        # database locked.
        # ----------------------------------------------------

        con.execute(
            "BEGIN IMMEDIATE"
        )

        existing = con.execute(
            """
            SELECT *
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
                VALUES (?,0,'','','',?,?)
                """,
                (
                    user_id,
                    now,
                    now
                )
            )

            balance = 0.0

        else:

            balance = float(
                row["balance"]
            )

        # ----------------------------------------------------
        # DEDUCT
        # ----------------------------------------------------

        if operation_type == "deduct":

            if balance < amount:

                con.rollback()

                raise ValueError(
                    "Недостаточно ⭐"
                )

            new_balance = round(
                balance - amount,
                2
            )

        # ----------------------------------------------------
        # ADD
        # ----------------------------------------------------

        else:

            new_balance = round(
                balance + amount,
                2
            )

        # ----------------------------------------------------
        # UPDATE BALANCE
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # TRANSACTION
        # ----------------------------------------------------

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
                operation_type,
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

        # ----------------------------------------------------
        # INIT DATA ДЕЙСТВУЕТ 24 ЧАСА
        # ----------------------------------------------------

        if (
            int(time.time())
            - auth_date
            > 86400
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

        user_data = data.get(
            "user"
        )

        if not user_data:

            return None

        return json.loads(
            user_data
        )

    except Exception:

        return None


# ============================================================
# RESOLVE USER
# ============================================================

def resolve_user(
    user_id,
    init_data
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
            and int(user_id)
            != verified_id
        ):

            raise HTTPException(
                status_code=403,
                detail="Telegram ID mismatch"
            )

        ensure_user(
            verified_id
        )

        return (
            verified_id,
            verified
        )

    # --------------------------------------------------------
    # Для запросов из Telegram обязательно желательно иметь
    # initData.
    #
    # Оставляем fallback user_id для совместимости.
    # --------------------------------------------------------

    if (
        user_id is None
        or int(user_id) <= 0
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "Открой приложение через Telegram"
            )
        )

    uid = int(
        user_id
    )

    ensure_user(
        uid
    )

    return (
        uid,
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
# HEALTH
# ============================================================

@app.get(
    "/health"
)
async def health():

    return {
        "ok": True,
        "status": "online",
        "service": "White Bear Drop",
        "time": int(
            time.time()
        )
    }


# ============================================================
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
# ADD BALANCE
# ============================================================

@app.post(
    "/api/balance/add"
)
async def api_add_balance(
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
        f"balance_add:"
        f"{uid}:"
        f"{secrets.token_hex(12)}"
    )

    try:

        new_balance = balance_transaction(
            uid,
            operation_id,
            "add",
            req.amount,
            req.reason or "balance:add"
        )

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    return {
        "ok": True,
        "balance": new_balance
    }


# ============================================================
# DEDUCT BALANCE
# ============================================================

@app.post(
    "/api/balance/deduct"
)
async def api_deduct_balance(
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
        f"balance_deduct:"
        f"{uid}:"
        f"{secrets.token_hex(12)}"
    )

    try:

        new_balance = balance_transaction(
            uid,
            operation_id,
            "deduct",
            req.amount,
            req.reason or "balance:deduct"
        )

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    return {
        "ok": True,
        "balance": new_balance
    }


# ============================================================
# UNIVERSAL GAME TRANSACTION
# ============================================================

@app.post(
    "/api/game/transaction"
)
async def api_game_transaction(
    req: TransactionRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    if not req.operation_id:

        raise HTTPException(
            status_code=400,
            detail="operation_id обязателен"
        )

    operation_type = (
        req.type
        .strip()
        .lower()
    )

    if operation_type not in (
        "add",
        "deduct"
    ):

        raise HTTPException(
            status_code=400,
            detail="Неверный тип операции"
        )

    try:

        new_balance = balance_transaction(
            uid,
            req.operation_id,
            operation_type,
            req.amount,
            req.game or "game"
        )

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    return {
        "ok": True,
        "balance": new_balance
    }


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


# ============================================================
# WEIGHTED REWARD
# ============================================================

def weighted_reward(
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


# ============================================================
# OPEN CASE
# ============================================================

@app.post(
    "/api/cases/open"
)
async def api_open_case(
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
            status_code=404,
            detail="Кейс не найден"
        )

    server_price = float(
        case["price"]
    )

    if abs(
        float(req.price)
        - server_price
    ) > 0.01:

        raise HTTPException(
            status_code=400,
            detail="Неверная цена кейса"
        )

    # --------------------------------------------------------
    # FREE CASE COOLDOWN
    # --------------------------------------------------------

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
                - int(row["created_at"])
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
                    status_code=400,
                    detail=(
                        "FREE кейс уже открыт. "
                        f"Следующее открытие через "
                        f"{hours}ч {minutes}м."
                    )
                )

    # --------------------------------------------------------
    # СНИМАЕМ СТОИМОСТЬ
    # --------------------------------------------------------

    if server_price > 0:

        bet_operation = (
            f"case_bet:"
            f"{uid}:"
            f"{secrets.token_hex(12)}"
        )

        try:

            balance_transaction(
                uid,
                bet_operation,
                "deduct",
                server_price,
                f"case:{req.case_id}"
            )

        except ValueError as e:

            raise HTTPException(
                status_code=400,
                detail=str(e)
            )

    # --------------------------------------------------------
    # ВЫБИРАЕМ ПРИЗ
    # --------------------------------------------------------

    (
        emoji,
        name,
        reward_value,
        chance,
        rarity
    ) = weighted_reward(
        case["items"]
    )

    # --------------------------------------------------------
    # НАЧИСЛЯЕМ ПРИЗ
    # --------------------------------------------------------

    reward_operation = (
        f"case_reward:"
        f"{uid}:"
        f"{secrets.token_hex(12)}"
    )

    new_balance = balance_transaction(
        uid,
        reward_operation,
        "add",
        reward_value,
        f"case_reward:{req.case_id}"
    )

    # --------------------------------------------------------
    # ИНВЕНТАРЬ
    # --------------------------------------------------------

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
                reward_operation,
                name,
                emoji,
                reward_value,
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

            "value": reward_value,

            "rarity": rarity,

            "chance": chance

        },

        "balance": new_balance
    }


# ============================================================
# BALL
# ============================================================

@app.post(
    "/api/games/ball"
)
async def api_ball(
    req: BallRequest,
    x_telegram_init_data: str | None = Header(
        default=None
    )
):

    uid, _ = resolve_user(
        req.user_id,
        x_telegram_init_data
    )

    bet_operation = (
        f"ball_bet:"
        f"{uid}:"
        f"{secrets.token_hex(12)}"
    )

    try:

        balance_transaction(
            uid,
            bet_operation,
            "deduct",
            req.bet,
            "ball:bet"
        )

    except ValueError as e:

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    random_value = (
        secrets.SystemRandom()
        .random()
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

    if prize > 0:

        new_balance = balance_transaction(
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

    else:

        new_balance = get_balance(
            uid
        )

    return {

        "ok": True,

        "result": (
            f"Ставка: {req.bet} ⭐\n"
            f"Множитель: x{multiplier}\n"
            f"Выигрыш: {prize:.2f} ⭐"
        ),

        "bet": req.bet,

        "multiplier": multiplier,

        "prize": prize,

        "balance": new_balance

    }


# ============================================================
# SCRATCH
# ============================================================

@app.post(
    "/api/games/scratch"
)
async def api_scratch(
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
            status_code=400,
            detail=str(e)
        )

    prizes = [

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

    prize = secrets.choice(
        prizes
    )

    if prize > 0:

        new_balance = balance_transaction(
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

    else:

        new_balance = get_balance(
            uid
        )

    return {

        "ok": True,

        "result": (
            f"Скретч-карта: {cost} ⭐\n"
            f"Выигрыш: {prize} ⭐"
        ),

        "prize": prize,

        "balance": new_balance

    }


# ============================================================
# UPGRADE
# ============================================================

@app.post(
    "/api/games/upgrade"
)
async def api_upgrade(
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
            status_code=400,
            detail="Не указан предмет"
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
            status_code=400,
            detail=str(e)
        )

    success = (
        secrets.SystemRandom()
        .random()
        < 0.45
    )

    if success:

        prize = req.bet * 2

        new_balance = balance_transaction(
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

        new_balance = get_balance(
            uid
        )

        result = (
            "❌ Апгрейд не удался."
        )

    return {

        "ok": True,

        "result": result,

        "success": success,

        "prize": prize,

        "balance": new_balance

    }


# ============================================================
# PROMOCODES
# ============================================================

PROMOS = {

    "200": 200,

    "met200": 200

}


@app.post(
    "/api/promo/activate"
)
async def api_promo(
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
            status_code=400,
            detail="Неверный промокод"
        )

    amount = PROMOS[
        code
    ]

    # --------------------------------------------------------
    # АТОМАРНО ПРОВЕРЯЕМ ПРОМОКОД
    # --------------------------------------------------------

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
                status_code=400,
                detail=(
                    "Этот промокод уже использован"
                )
            )

        try:

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

        except sqlite3.IntegrityError:

            con.rollback()

            raise HTTPException(
                status_code=400,
                detail=(
                    "Этот промокод уже использован"
                )
            )

    # --------------------------------------------------------
    # НАЧИСЛЯЕМ БАЛАНС
    # --------------------------------------------------------

    operation_id = (
        f"promo:"
        f"{uid}:"
        f"{code}:"
        f"{secrets.token_hex(12)}"
    )

    try:

        new_balance = balance_transaction(
            uid,
            operation_id,
            "add",
            amount,
            f"promo:{code}"
        )

    except Exception:

        # Если начисление не получилось,
        # разрешаем использовать промокод повторно.

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

        "balance": new_balance

    }


# ============================================================
# INVENTORY
# ============================================================

@app.get(
    "/api/inventory/{user_id}"
)
async def api_inventory(
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
# SELL INVENTORY ITEM
# ============================================================

@app.post(
    "/api/inventory/sell"
)
async def api_sell_item(
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

    except Exception:

        raise HTTPException(
            status_code=400,
            detail="Неверный ID предмета"
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
                status_code=404,
                detail="Предмет не найден"
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

    operation_id = (
        f"sell:"
        f"{uid}:"
        f"{item_id}:"
        f"{secrets.token_hex(12)}"
    )

    try:

        new_balance = balance_transaction(
            uid,
            operation_id,
            "add",
            value,
            f"inventory:sell:{item_id}"
        )

    except Exception:

        # Возвращаем предмет, если начисление
        # почему-то не произошло.

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

        raise

    return {

        "ok": True,

        "message": (
            f"Предмет продан за "
            f"{value:.2f} ⭐"
        ),

        "balance": new_balance

    }


# ============================================================
# TELEGRAM STARS PAYMENT
# ============================================================

@app.post(
    "/api/payments/stars"
)
async def api_create_payment(
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

    except Exception:

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

        raise

    return {

        "ok": True,

        "success": True,

        "invoice_link": invoice_link,

        "amount": req.amount

    }


# ============================================================
# TELEGRAM START
# ============================================================

@dp.message(
    CommandStart()
)
async def start_command(
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

        "🐻‍❄️ <b>White Bear Drop</b>\n\n"

        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n\n"

        "🎮 Открой мини-приложение "
        "через кнопку меню бота."

    )


# ============================================================
# PRE CHECKOUT
# ============================================================

@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):

    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message="Неверная валюта"
        )

        return

    if query.total_amount <= 0:

        await query.answer(
            ok=False,
            error_message="Неверная сумма"
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

    user_id = (
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

    # --------------------------------------------------------
    # ПОЛУЧАЕМ ПЛАТЁЖ
    # --------------------------------------------------------

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
        ) != user_id:

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

                int(
                    time.time()
                ),

                payload
            )
        )

        con.commit()

    # --------------------------------------------------------
    # НАЧИСЛЕНИЕ
    # --------------------------------------------------------

    try:

        new_balance = balance_transaction(
            user_id,
            f"payment:{charge_id}",
            "add",
            amount,
            "telegram_stars"
        )

    except Exception as e:

        print(
            "PAYMENT CREDIT ERROR:",
            repr(e)
        )

        await message.answer(
            "⚠️ Платёж подтверждён, "
            "но возникла ошибка зачисления. "
            "Обратись к администратору."
        )

        return

    await message.answer(

        "✅ <b>Оплата получена!</b>\n\n"

        f"⭐ Зачислено: "
        f"<b>+{amount}</b>\n"

        f"💰 Баланс: "
        f"<b>{new_balance:.2f} ⭐</b>"

    )


# ============================================================
# API INFO
# ============================================================

@app.get(
    "/api/status"
)
async def api_status():

    return {

        "ok": True,

        "service": "White Bear Drop",

        "api": "online",

        "games": [

            "cases",
            "ball",
            "scratch",
            "upgrade"

        ],

        "inventory": True,

        "promocodes": [

            "200",
            "met200"

        ],

        "payments": "telegram_stars"

    }


# ============================================================
# START API
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
# TELEGRAM POLLING
# ============================================================

async def run_bot():

    # --------------------------------------------------------
    # САМОЕ ВАЖНОЕ ИСПРАВЛЕНИЕ
    #
    # Если ранее у бота был установлен webhook,
    # start_polling() будет выдавать:
    #
    # TelegramConflictError:
    # can't use getUpdates method while webhook is active
    #
    # Поэтому перед polling удаляем webhook.
    # --------------------------------------------------------

    print(
        "📡 Проверяем Telegram webhook..."
    )

    try:

        webhook_info = (
            await bot.get_webhook_info()
        )

        if webhook_info.url:

            print(
                "⚠️ Найден активный webhook:"
            )

            print(
                webhook_info.url
            )

            print(
                "🧹 Удаляем webhook..."
            )

            await bot.delete_webhook(
                drop_pending_updates=False
            )

            print(
                "✅ Старый webhook удалён."
            )

        else:

            print(
                "✅ Активного webhook нет."
            )

    except Exception as e:

        print(
            "❌ Ошибка при проверке webhook:"
        )

        print(
            repr(e)
        )

        raise

    # --------------------------------------------------------
    # Запускаем polling
    # --------------------------------------------------------

    print(
        "🤖 Запускаем Telegram polling..."
    )

    await dp.start_polling(

        bot,

        allowed_updates=(
            dp.resolve_used_update_types()
        )

    )


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    print(
        "=========================================="
    )

    print(
        "🐻‍❄️ WHITE BEAR DROP"
    )

    print(
        "=========================================="
    )

    print(
        f"🌐 PORT: {PORT}"
    )

    print(
        f"🌐 WEBAPP: {WEBAPP_URL}"
    )

    print(
        f"💾 DATABASE: {DB_PATH}"
    )

    print(
        "🎮 CASES: ON"
    )

    print(
        "🎯 BALL: ON"
    )

    print(
        "🎫 SCRATCH: ON"
    )

    print(
        "⬆️ UPGRADE: ON"
    )

    print(
        "🎒 INVENTORY: ON"
    )

    print(
        "🎟 PROMO 200: ON"
    )

    print(
        "🎟 PROMO met200: ON"
    )

    print(
        "⭐ STARS PAYMENT: ON"
    )

    print(
        "=========================================="
    )

    # --------------------------------------------------------
    # Одновременно:
    #
    # 1. FastAPI
    # 2. Telegram polling
    #
    # Но сначала run_bot() удалит старый webhook.
    # --------------------------------------------------------

    await asyncio.gather(

        run_api(),

        run_bot()

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

        print(
            "🛑 White Bear остановлен."
        )

    except Exception as e:

        print(
            "❌ КРИТИЧЕСКАЯ ОШИБКА:"
        )

        print(
            repr(e)
        )