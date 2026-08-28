import json
import os
import logging
from flask import Flask, jsonify, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters, ContextTypes

# === НАСТРОЙКИ ===
TOKEN = "ВАШ_ТОКЕН_БОТА"  # Замените на токен от @BotFather
BALANCE_FILE = "balances.json"
WEBHOOK_URL = "https://whitebear.bothost.tech/webhook"  # Ваш домен + /webhook

logging.basicConfig(level=logging.INFO)

# === РАБОТА С БАЛАНСАМИ ===
def load_balances():
    if os.path.exists(BALANCE_FILE):
        with open(BALANCE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_balances(balances):
    with open(BALANCE_FILE, "w") as f:
        json.dump(balances, f, indent=2)

def get_balance(user_id):
    balances = load_balances()
    return balances.get(str(user_id), 0)

def set_balance(user_id, amount):
    balances = load_balances()
    balances[str(user_id)] = amount
    save_balances(balances)

def add_balance(user_id, amount):
    current = get_balance(user_id)
    new_balance = current + amount
    set_balance(user_id, new_balance)
    return new_balance

# === FLASK ПРИЛОЖЕНИЕ (API) ===
app = Flask(__name__)

@app.route('/')
def index():
    return 'OK', 200

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/api/balance/<int:user_id>')
def api_balance(user_id):
    balance = get_balance(user_id)
    return jsonify({'user_id': user_id, 'balance': balance})

# === TELEGRAM БОТ ===
application = Application.builder().token(TOKEN).build()

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    
    keyboard = [
        [InlineKeyboardButton("⭐ Пополнить 10 Stars", callback_data='deposit_10')],
        [InlineKeyboardButton("⭐ Пополнить 50 Stars", callback_data='deposit_50')],
        [InlineKeyboardButton("⭐ Пополнить 100 Stars", callback_data='deposit_100')],
        [InlineKeyboardButton("📊 Мой баланс", callback_data='balance')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Привет! Ваш баланс: {balance} ⭐\n"
        "Выберите сумму для пополнения:",
        reply_markup=reply_markup
    )

# --- Обработка кнопок ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == 'balance':
        balance = get_balance(user_id)
        await query.edit_message_text(f"💰 Ваш баланс: {balance} ⭐")
        return

    if data.startswith('deposit_'):
        amount = int(data.split('_')[1])
        # Создаем инвойс для оплаты Stars
        prices = [LabeledPrice(label=f"Пополнение на {amount} ⭐", amount=amount)]
        try:
            await query.message.reply_invoice(
                title=f"Пополнение баланса",
                description=f"Пополнение на {amount} ⭐",
                payload=f"deposit_{amount}_{user_id}",
                provider_token="",  # Для Stars обязательно пустая строка
                currency="XTR",      # Валюта для Stars
                prices=prices,
                need_name=False,
                need_phone_number=False,
                need_email=False,
                need_shipping_address=False,
                is_flexible=False,
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")

# --- Обработка предварительной проверки платежа ---
async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    # Здесь можно добавить проверку, например, доступности товара
    await query.answer(ok=True)

# --- Обработка успешного платежа ---
async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    payment_info = message.successful_payment
    payload = payment_info.invoice_payload
    
    # Парсим payload: deposit_10_123456789
    parts = payload.split('_')
    if len(parts) == 3 and parts[0] == 'deposit':
        amount = int(parts[1])
        user_id = int(parts[2])
        
        # Начисляем баланс
        new_balance = add_balance(user_id, amount)
        
        # Логируем платеж (можно добавить в отдельный файл или БД)
        logging.info(f"Платеж от {user_id} на {amount} Stars. Новый баланс: {new_balance}")
        
        await message.reply_text(
            f"✅ Оплата прошла успешно!\n"
            f"Пополнено: {amount} ⭐\n"
            f"Новый баланс: {new_balance} ⭐"
        )
    else:
        await message.reply_text("❌ Ошибка обработки платежа")

# --- Обработчик вебхука ---
@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    await application.process_update(update)
    return 'OK', 200

# --- Регистрация обработчиков ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler))
application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

# --- Установка вебхука ---
def set_webhook():
    import requests
    url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={WEBHOOK_URL}"
    resp = requests.get(url)
    logging.info(f"Webhook set: {resp.text}")

# --- ЗАПУСК ---
if __name__ == '__main__':
    set_webhook()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)