import os
import re
import time
import asyncio
import aiofiles
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# Default settings
DEFAULT_THUMB = "https://envs.sh/e3P.jpg"
DEFAULT_META = "HC_Filez"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo = MongoClient(MONGO_URI)
db = mongo["MergeDB"]
log_col = db["FileLogs"]
settings_col = db["UserSettings"]

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

DOWNLOADS_DIR = "downloads"
last_progress = {}

# Progress display
async def progress_bar(current, total, message: Message, prefix: str, start: float, filename: str):
    now = time.time()
    elapsed = now - start
    key = message.chat.id
    if key not in last_progress or now - last_progress[key] > 3:
        last_progress[key] = now
        percent = current / total * 100
        done_blocks = int(percent // 10)
        bar = "●" * done_blocks + "○" * (10 - done_blocks)
        speed = current / elapsed
        eta = (total - current) / speed if speed > 0 else 0
        text = (
            f"{prefix}: {filename}\n"
            f"👤 Userid: {message.chat.id}\n"
            f"{bar} {percent:.2f}%\n"
            f"🔄 {current / (1024*1024):.2f}MB of {total / (1024*1024):.2f}MB\n"
            f"📊 Speed: {speed / (1024*1024):.2f}MB/s\n"
            f"⏰ ETA: {int(eta)}s | ⏱ Elapsed: {int(elapsed)}s"
        )
        try:
            await message.edit_text(text)
        except:
            pass

# Continue your full bot logic below here...
