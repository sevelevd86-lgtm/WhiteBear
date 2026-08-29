import os
import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в Secrets")

PHOTO_PATH = Path(__file__).with_name("white_bear_blocked.jpeg")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
TEXT = "🐻‍❄️🚫 <b>WHITE BEAR BLOCKED</b> 🐻‍❄️🚫"

@dp.message(CommandStart())
async def start(message: Message):
    if PHOTO_PATH.exists():
        await message.answer_photo(photo=FSInputFile(PHOTO_PATH), caption=TEXT)
    else:
        await message.answer(TEXT)

async def main():
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
