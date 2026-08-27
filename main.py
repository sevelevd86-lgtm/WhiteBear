import asyncio
import logging
import sqlite3
import sys
import secrets
import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl
from datetime import datetime
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
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
# =====================================================
# НАСТРОЙКИ
# =====================================================
# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН ОТ BOTFATHER
BOT_TOKEN = "8918284594:AAFLxOg1eEx4JS6z6V9wHr-t8T3Q9Qwiepg"
BOT_USERNAME = "White_Bear_ROBOT"
WEBAPP_URL = "https://sevelevd86-lgtm.github.io/WhiteBear/"
DB_NAME = "users.db"
# Порт HTTP-сервера.
# Railway/Render/другой хост обычно сам передаёт PORT.
PORT = int(os.getenv("PORT", "8080"))
# =====================================================
# ВАЖНО:
# В HTML укажи публичный HTTPS-адрес этого сервера.
#
# Например:
#
# SERVER_URL = "https://your-server.up.railway.app"
#
# Этот адрес нужен HTML для создания счёта.
# =====================================================
SERVER_URL = os.getenv(
    "SERVER_URL",
    "https://YOUR-SERVER-URL"
)
# =====================================================
# ЛОГИРОВАНИЕ
# =====================================================
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)
# =====================================================
# БАЗА ДАННЫХ
# =====================================================
def get_db():
    return sqlite3.connect(DB_NAME)
def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Пользователи
    cur.execute("""
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
    # Рефералы
    cur.execute("""
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
    # Платежи
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT UNIQUE,
            provider_payment_charge_id TEXT,
            currency TEXT,
            amount REAL,
            payload TEXT,
            status TEXT DEFAULT 'paid',
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
def get_user_by_ref_code(ref_code: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id FROM users WHERE ref_code = ?",
        (ref_code,)
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None
def get_balance(user_id: int) -> float:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()
    return float(result[0]) if result else 0.0
def update_balance(user_id: int, amount: float):
    conn = get_db()
    cursor = conn.cursor()
    amount = round(float(amount), 2)
    cursor.execute("""
        UPDATE users
        SET balance = ?
        WHERE user_id = ?
    """, (
        amount,
        user_id
    ))
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
    current = get_balance(user_id)
    new_balance = round(
        current + float(amount),
        2
    )
    update_balance(
        user_id,
        new_balance
    )
    return new_balance
# =====================================================
# РЕФЕРАЛЫ
# =====================================================
def add_referral(
    referrer_id: int,
    referred_id: int,
    reward: float = 10.0
):
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
    conn = get_db()
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
def get_referral_link(user_id: int) -> str:
    return (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )
# =====================================================
# ПРОВЕРКА TELEGRAM WEBAPP INIT DATA
# =====================================================
def validate_telegram_init_data(
    init_data: str,
    bot_token: str
):
    """
    Проверяет Telegram.WebApp.initData.
    Возвращает данные пользователя,
    если подпись Telegram корректная.
    Иначе возвращает None.
    """
    if not init_data:
        return None
    try:
        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )
        received_hash = parsed.pop(
            "hash",
            None
        )
        if not received_hash:
            return None
        # Telegram требует сортировку параметров
        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
        )
        # secret_key = HMAC-SHA256(bot_token, key="WebAppData")
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
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
            logger.warning(
                "❌ Неверная подпись Telegram WebApp"
            )
            return None
        # Достаём user
        user_json = parsed.get("user")
        if not user_json:
            return None
        user_data = json.loads(user_json)
        return {
            "id": int(user_data["id"]),
            "username": user_data.get("username"),
            "first_name": user_data.get("first_name"),
            "last_name": user_data.get("last_name"),
        }
    except Exception as e:
        logger.error(
            f"Ошибка проверки initData: {e}"
        )
        return None
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
    builder.adjust(
        1,
        2,
        1
    )
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
                        f"Пользователь {first_name} "
                        f"перешёл по вашей ссылке.\n\n"
                        f"💰 Вы получили +10 ⭐\n"
                        f"📊 Всего приглашено: "
                        f"{get_referrals_count(invited_by)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Не удалось уведомить "
                        f"реферера: {e}"
                    )
    balance = get_balance(user_id)
    ref_count = get_referrals_count(
        user_id
    )
    ref_link = get_referral_link(
        user_id
    )
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
        f"🎮 Нажмите кнопку ниже, "
        f"чтобы открыть DROP.",
        reply_markup=get_main_keyboard()
    )
# =====================================================
# GAME
# =====================================================
@dp.message(Command("game"))
async def game_command(message: Message):
    user_id = message.from_user.id
    balance = get_balance(
        user_id
    )
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
    balance = get_balance(
        user_id
    )
    ref_count = get_referrals_count(
        user_id
    )
    await message.answer(
        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n"
        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}"
    )
# =====================================================
# PROFILE
# =====================================================
@dp.message(Command("profile"))
async def profile_command(message: Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    balance = get_balance(
        user_id
    )
    ref_count = get_referrals_count(
        user_id
    )
    ref_link = get_referral_link(
        user_id
    )
    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {first_name}\n"
        f"🆔 ID: "
        f"<code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено: "
        f"{ref_count}\n\n"
        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>"
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
        "/help — Помощь\n\n"
        "💰 Пополнять баланс можно "
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
    balance = get_balance(
        user_id
    )
    ref_count = get_referrals_count(
        user_id
    )
    await callback.message.edit_text(
        f"💰 <b>Ваш баланс:</b> "
        f"{balance:.2f} ⭐\n"
        f"👥 <b>Приглашено друзей:</b> "
        f"{ref_count}",
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
    balance = get_balance(
        user_id
    )
    ref_count = get_referrals_count(
        user_id
    )
    ref_link = get_referral_link(
        user_id
    )
    await callback.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"Имя: {first_name}\n"
        f"🆔 ID: "
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
    await callback.message.edit_text(
        f"📎 <b>Ваша реферальная ссылка:</b>\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"💡 Приглашайте друзей "
        f"и получайте по 10 ⭐ за каждого!",
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
    balance = get_balance(
        user_id
    )
    ref_count = get_referrals_count(
        user_id
    )
    ref_link = get_referral_link(
        user_id
    )
    await callback.message.edit_text(
        f"🐻‍❄️ <b>Добро пожаловать "
        f"в DROP, {first_name}!</b>\n\n"
        f"🆔 Ваш ID: "
        f"<code>{user_id}</code>\n"
        f"💰 Баланс: "
        f"<b>{balance:.2f} ⭐</b>\n"
        f"👥 Приглашено друзей: "
        f"{ref_count}\n\n"
        f"📎 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()
# =====================================================
# TELEGRAM STARS
# =====================================================
def create_payment_payload(
    user_id: int,
    stars: int
) -> str:
    return (
        f"deposit:"
        f"{user_id}:"
        f"{stars}:"
        f"{secrets.token_hex(8)}"
    )
# =====================================================
# HTTP: СОЗДАНИЕ INVOICE
# =====================================================
async def create_invoice(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {
                "ok": False,
                "error": "Invalid JSON"
            },
            status=400
        )
    init_data = body.get(
        "initData"
    )
    stars = body.get(
        "stars"
    )
    # -------------------------------------------------
    # Проверяем Telegram initData
    # -------------------------------------------------
    user = validate_telegram_init_data(
        init_data,
        BOT_TOKEN
    )
    if not user:
        return web.json_response(
            {
                "ok": False,
                "error": "Invalid Telegram session"
            },
            status=401
        )
    # -------------------------------------------------
    # Проверяем сумму
    # -------------------------------------------------
    try:
        stars = int(stars)
    except Exception:
        return web.json_response(
            {
                "ok": False,
                "error": "Invalid amount"
            },
            status=400
        )
    # Разрешаем суммы от 10 до 1000 Stars.
    if stars < 10:
        return web.json_response(
            {
                "ok": False,
                "error": "Minimum deposit is 10 Stars"
            },
            status=400
        )
    if stars > 1000:
        return web.json_response(
            {
                "ok": False,
                "error": "Maximum deposit is 1000 Stars"
            },
            status=400
        )
    user_id = user["id"]
    # -------------------------------------------------
    # Убеждаемся, что пользователь существует
    # -------------------------------------------------
    if not get_user(user_id):
        create_user(
            user_id,
            user.get("username"),
            user.get("first_name")
        )
    # -------------------------------------------------
    # Создаём payload
    # -------------------------------------------------
    payload = create_payment_payload(
        user_id,
        stars
    )
    # -------------------------------------------------
    # Создаём Telegram Stars invoice
    # -------------------------------------------------
    try:
        invoice_link = await bot.create_invoice_link(
            title="Пополнение баланса",
            description=(
                f"Пополнение баланса "
                f"White Bear Drop на {stars} ⭐"
            ),
            payload=payload,
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=f"{stars} Telegram Stars",
                    amount=stars
                )
            ],
            # Для Stars provider_token НЕ нужен.
            provider_token=""
        )
    except Exception as e:
        logger.exception(
            "Ошибка создания invoice"
        )
        return web.json_response(
            {
                "ok": False,
                "error": str(e)
            },
            status=500
        )
    logger.info(
        f"💳 Создан invoice: "
        f"user={user_id}, "
        f"stars={stars}"
    )
    return web.json_response(
        {
            "ok": True,
            "invoice_url": invoice_link,
            "user_id": user_id,
            "stars": stars
        }
    )
# =====================================================
# HTTP: ПРОВЕРКА БАЛАНСА
# =====================================================
async def get_webapp_balance(
    request: web.Request
):
    init_data = request.headers.get(
        "X-Telegram-Init-Data"
    )
    user = validate_telegram_init_data(
        init_data,
        BOT_TOKEN
    )
    if not user:
        return web.json_response(
            {
                "ok": False,
                "error": "Invalid Telegram session"
            },
            status=401
        )
    user_id = user["id"]
    # Если пользователя нет — создаём.
    if not get_user(user_id):
        create_user(
            user_id,
            user.get("username"),
            user.get("first_name")
        )
    balance = get_balance(
        user_id
    )
    return web.json_response(
        {
            "ok": True,
            "user_id": user_id,
            "balance": balance
        }
    )
# =====================================================
# HTTP: CORS
# =====================================================
@web.middleware
async def cors_middleware(
    request,
    handler
):
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        try:
            response = await handler(
                request
            )
        except web.HTTPException as e:
            response = e
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers[
        "Access-Control-Allow-Headers"
    ] = (
        "Content-Type, "
        "X-Telegram-Init-Data"
    )
    response.headers[
        "Access-Control-Allow-Methods"
    ] = "GET,POST,OPTIONS"
    return response
# =====================================================
# HTTP SERVER
# =====================================================
async def start_http_server():
    app = web.Application(
        middlewares=[
            cors_middleware
        ]
    )
    # Создание оплаты
    app.router.add_post(
        "/create-invoice",
        create_invoice
    )
    # Получение баланса
    app.router.add_get(
        "/webapp-balance",
        get_webapp_balance
    )
    # Health check
    async def health(request):
        return web.json_response(
            {
                "ok": True,
                "service": "White Bear Drop"
            }
        )
    app.router.add_get(
        "/",
        health
    )
    runner = web.AppRunner(
        app
    )
    await runner.setup()
    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT
    )
    await site.start()
    logger.info(
        f"🌐 HTTP сервер запущен "
        f"на порту {PORT}"
    )
    return runner
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
        action = data.get(
            "action"
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
        # Получить реферальную ссылку
        # ---------------------------------------------
        elif action == "getReferralLink":
            ref_link = get_referral_link(
                user_id
            )
            await message.answer(
                ref_link
            )
        # ---------------------------------------------
        # Старый updateBalance
        #
        # ОСТОРОЖНО:
        # напрямую менять баланс через WebApp
        # больше нельзя.
        #
        # Поэтому этот action отключён.
        # Баланс меняется только сервером.
        # ---------------------------------------------
        elif action == "updateBalance":
            await message.answer(
                "❌ Прямое изменение баланса "
                "через WebApp запрещено."
            )
        # ---------------------------------------------
        # Старый addReferral
        # ---------------------------------------------
        elif action == "addReferral":
            referrer_id = data.get(
                "referrer_id"
            )
            referred_id = data.get(
                "referred_id"
            )
            # Пользователь не может
            # подставить чужой referred_id.
            if int(referred_id) != user_id:
                await message.answer(
                    "❌ Неверный Telegram ID."
                )
                return
            if referrer_id:
                success = add_referral(
                    int(referrer_id),
                    user_id,
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
            "❌ Ошибка обработки данных"
        )
    except Exception as e:
        logger.exception(
            "Ошибка WebApp data"
        )
        await message.answer(
            "❌ Произошла ошибка"
        )
# =====================================================
# PRE-CHECKOUT
# =====================================================
@dp.pre_checkout_query()
async def process_pre_checkout(
    pre_checkout_query: types.PreCheckoutQuery
):
    try:
        payload = (
            pre_checkout_query.invoice_payload
        )
        # Формат:
        # deposit:user_id:stars:random
        parts = payload.split(":")
        if len(parts) != 4:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный платёж."
            )
            return
        if parts[0] != "deposit":
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный платёж."
            )
            return
        payload_user_id = int(
            parts[1]
        )
        payload_stars = int(
            parts[2]
        )
        # Проверяем пользователя
        if payload_user_id != pre_checkout_query.from_user.id:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Пользователь платежа не совпадает."
            )
            return
        # Проверяем валюту
        if pre_checkout_query.currency != "XTR":
            await pre_checkout_query.answer(
                ok=False,
                error_message="Неверная валюта."
            )
            return
        # Проверяем сумму
        if pre_checkout_query.total_amount != payload_stars:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Неверная сумма платежа."
            )
            return
        await pre_checkout_query.answer(
            ok=True
        )
        logger.info(
            f"✅ PreCheckout подтверждён: "
            f"user={payload_user_id}, "
            f"stars={payload_stars}"
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
    lambda message: (
        message.successful_payment is not None
    )
)
async def successful_payment(
    message: Message
):
    payment = message.successful_payment
    user_id = message.from_user.id
    try:
        # ---------------------------------------------
        # Проверяем валюту
        # ---------------------------------------------
        if payment.currency != "XTR":
            logger.error(
                f"❌ Неизвестная валюта: "
                f"{payment.currency}"
            )
            return
        # ---------------------------------------------
        # Проверяем payload
        # ---------------------------------------------
        payload = payment.invoice_payload
        parts = payload.split(":")
        if len(parts) != 4:
            logger.error(
                f"❌ Неверный payload: {payload}"
            )
            return
        if parts[0] != "deposit":
            logger.error(
                f"❌ Неверный тип payload: {payload}"
            )
            return
        payload_user_id = int(
            parts[1]
        )
        payload_stars = int(
            parts[2]
        )
        # ---------------------------------------------
        # Проверяем Telegram ID
        # ---------------------------------------------
        if payload_user_id != user_id:
            logger.error(
                f"❌ ID не совпадает: "
                f"payload={payload_user_id}, "
                f"telegram={user_id}"
            )
            return
        # ---------------------------------------------
        # Проверяем сумму
        # ---------------------------------------------
        if payment.total_amount != payload_stars:
            logger.error(
                f"❌ Сумма не совпадает"
            )
            return
        # ---------------------------------------------
        # Проверяем, не был ли платёж уже зачислен
        # ---------------------------------------------
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id
            FROM payments
            WHERE telegram_payment_charge_id = ?
        """, (
            payment.telegram_payment_charge_id,
        ))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            logger.warning(
                f"⚠️ Платёж уже обработан: "
                f"{payment.telegram_payment_charge_id}"
            )
            return
        # ---------------------------------------------
        # Записываем платёж
        # ---------------------------------------------
        cursor.execute("""
            INSERT INTO payments (
                user_id,
                telegram_payment_charge_id,
                provider_payment_charge_id,
                currency,
                amount,
                payload,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            payment.telegram_payment_charge_id,
            payment.provider_payment_charge_id,
            payment.currency,
            payment.total_amount,
            payment.invoice_payload,
            "paid"
        ))
        conn.commit()
        conn.close()
        # ---------------------------------------------
        # Начисляем баланс
        # ---------------------------------------------
        new_balance = add_balance(
            user_id,
            payment.total_amount
        )
        logger.info(
            f"💰 ОПЛАТА УСПЕШНА: "
            f"user={user_id}, "
            f"+{payment.total_amount} ⭐, "
            f"balance={new_balance}"
        )
        # ---------------------------------------------
        # Сообщение пользователю
        # ---------------------------------------------
        await message.answer(
            f"✅ <b>Оплата прошла успешно!</b>\n\n"
            f"💰 Зачислено: "
            f"<b>+{payment.total_amount} ⭐</b>\n"
            f"💳 Новый баланс: "
            f"<b>{new_balance:.2f} ⭐</b>"
        )
    except Exception as e:
        logger.exception(
            f"❌ Ошибка обработки оплаты: {e}"
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
        "🚀 Запуск White Bear Drop..."
    )
    # База
    init_db()
    # Команды
    await set_commands_and_menu()
    # HTTP-сервер для WebApp
    http_runner = await start_http_server()
    try:
        logger.info(
            "🤖 Telegram Bot polling запущен"
        )
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        await http_runner.cleanup()
        await bot.session.close()
# =====================================================
# ENTRY POINT
# =====================================================
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