import json
import os
import logging
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# === Настройки ===
TOKEN = "ВАШ_ТОКЕН_БОТА"  # замените на реальный
BALANCE_FILE = "balances.json"
WEBHOOK_URL = "https://whitebear.bothost.tech/webhook"  # ваш домен

logging.basicConfig(level=logging.INFO)

# === Работа с балансами ===
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

# === Flask приложение ===
app = Flask(__name__)

@app.route('/health')
def health():
    return 'OK', 200

@app.route('/api/balance/<int:user_id>')
def api_balance(user_id):
    balance = get_balance(user_id)
    return jsonify({'user_id': user_id, 'balance': balance})

# === Telegram бот ===
application = Application.builder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    keyboard = [
        [InlineKeyboardButton("💰 Пополнить 10⭐", callback_data='deposit_10')],
        [InlineKeyboardButton("💰 Пополнить 50⭐", callback_data='deposit_50')],
        [InlineKeyboardButton("💰 Пополнить 100⭐", callback_data='deposit_100')],
        [InlineKeyboardButton("📊 Мой баланс", callback_data='balance')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"👋 Привет! Ваш баланс: {balance} ⭐\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

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
        current = get_balance(user_id)
        new_balance = current + amount
        set_balance(user_id, new_balance)
        await query.edit_message_text(
            f"✅ Пополнение на {amount} ⭐ успешно!\n"
            f"Новый баланс: {new_balance} ⭐"
        )

# === Обработчик вебхука ===
@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    await application.process_update(update)
    return 'OK', 200

# === Регистрация хендлеров ===
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button_handler))

# === Установка вебхука при запуске ===
def set_webhook():
    import requests
    url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={WEBHOOK_URL}"
    resp = requests.get(url)
    logging.info(f"Webhook set: {resp.text}")

# === Запуск Flask ===
if __name__ == '__main__':
    set_webhook()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)