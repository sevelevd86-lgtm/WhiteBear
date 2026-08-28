import json
import os
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Настройки ---
TOKEN = "ВАШ_ТОКЕН_БОТА"  # замените на реальный токен
BALANCE_FILE = "balances.json"

# --- Работа с балансом ---
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

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_balance(user_id)
    
    # Кнопка с WebApp
    webapp_url = "https://ваш-домен.vercel.app"  # сюда вставьте URL вашего мини-приложения
    keyboard = [[KeyboardButton("💰 Открыть кошелёк", web_app=WebAppInfo(url=webapp_url))]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"👋 Привет! Ваш баланс: {balance} ⭐\n"
        "Нажмите кнопку ниже, чтобы пополнить или управлять балансом.",
        reply_markup=reply_markup
    )

# --- Обработка данных от WebApp ---
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.message.web_app_data
    if not data:
        return
    
    try:
        payload = json.loads(data.data)
        action = payload.get("action")
        user_id = update.effective_user.id
        
        if action == "deposit":
            amount = int(payload.get("amount", 0))
            current = get_balance(user_id)
            new_balance = current + amount
            set_balance(user_id, new_balance)
            await update.message.reply_text(
                f"✅ Пополнение на {amount} ⭐ успешно!\n"
                f"Новый баланс: {new_balance} ⭐"
            )
        elif action == "withdraw":
            amount = int(payload.get("amount", 0))
            current = get_balance(user_id)
            if current >= amount:
                new_balance = current - amount
                set_balance(user_id, new_balance)
                await update.message.reply_text(
                    f"✅ Вывод {amount} ⭐ выполнен!\n"
                    f"Новый баланс: {new_balance} ⭐"
                )
            else:
                await update.message.reply_text("❌ Недостаточно средств!")
        else:
            await update.message.reply_text("⚠️ Неизвестное действие.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

# --- Запуск бота ---
def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    
    print("Бот запущен (Long Polling)...")
    app.run_polling()

if __name__ == "__main__":
    main()