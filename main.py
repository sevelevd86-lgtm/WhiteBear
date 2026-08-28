from flask import Flask, request, jsonify
import os
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# === Обработчики для проверки работоспособности ===

@app.route('/')
@app.route('/health')
def health_check():
    return 'OK', 200

# === (Опционально) Обработчик вебхука для Telegram ===
# Если вы хотите принимать обновления от Telegram, раскомментируйте этот блок
# и укажите свой токен.

# TOKEN = "ваш_токен_бота"
# 
# @app.route('/webhook', methods=['POST'])
# def webhook():
#     update = request.get_json()
#     # Здесь ваша логика обработки входящих сообщений
#     # Например, просто логируем:
#     logging.info(f"Получено обновление: {update}")
#     return 'OK', 200

# === Запуск сервера ===

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    # Важно: слушаем все интерфейсы (0.0.0.0), чтобы Bothost мог достучаться
    app.run(host='0.0.0.0', port=port)