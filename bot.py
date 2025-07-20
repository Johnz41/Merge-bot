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
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
last_progress = {}

# Progress bar
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

# Start command
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("Hi, I am a merge bot that can merge files for you.\nUse /settings to configure merge options.")

# Settings command
@app.on_message(filters.command("settings"))
async def settings(client, message):
    keyboard = [
        [InlineKeyboardButton("Merge", callback_data="merge_toggle")],
        [InlineKeyboardButton("Thumbnail", callback_data="thumbnail_prompt")],
        [InlineKeyboardButton("Metadata", callback_data="metadata_prompt")]
    ]
    await message.reply("⚙️ Settings Menu:", reply_markup=InlineKeyboardMarkup(keyboard))

# Handle callback queries
@app.on_callback_query()
async def callbacks(client, callback):
    data = callback.data
    user_id = callback.from_user.id

    if data == "merge_toggle":
        keyboard = [
            [InlineKeyboardButton("ON", callback_data="merge_on"), InlineKeyboardButton("OFF", callback_data="merge_off")],
            [InlineKeyboardButton("Back", callback_data="back_to_settings")]
        ]
        await callback.message.edit_text("🔀 Toggle merging:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data in ("merge_on", "merge_off"):
        settings_col.update_one({"_id": user_id}, {"$set": {"merge_enabled": data == "merge_on"}}, upsert=True)
        await callback.message.edit_text(f"Merging is now {'enabled' if data == 'merge_on' else 'disabled'}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_to_settings")]]))

    elif data == "thumbnail_prompt":
        await callback.message.edit_text("🖼️ Send me a thumbnail image within 60 seconds.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_to_settings")]]))
        msg = await app.listen(callback.message.chat.id, timeout=60)
        if msg.photo:
            thumb_path = os.path.join(DOWNLOADS_DIR, f"{user_id}_thumb.jpg")
            await msg.download(thumb_path)
            settings_col.update_one({"_id": user_id}, {"$set": {"thumbnail": thumb_path}}, upsert=True)
            await msg.reply("✅ Thumbnail saved.")
        else:
            await msg.reply("❌ No valid image received.")

    elif data == "metadata_prompt":
        await callback.message.edit_text("📝 Send metadata text (e.g., `Exclusive By: @hc_filez`) within 60 seconds.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_to_settings")]]))
        msg = await app.listen(callback.message.chat.id, timeout=60)
        if msg.text:
            settings_col.update_one({"_id": user_id}, {"$set": {"metadata": msg.text}}, upsert=True)
            await msg.reply("✅ Metadata saved.")
        else:
            await msg.reply("❌ No valid text received.")

    elif data == "back_to_settings":
        await settings(client, callback.message)

# Merge command
@app.on_message(filters.command("merge") & filters.reply)
async def merge_command(client, message: Message):
    try:
        match = re.match(r"/merge\s+-i\s*(\d+)\s+-name\s+(.+)", message.text)
        if not match:
            return await message.reply("❌ Usage: `/merge -i 2 -name movie.mkv`")

        count = int(match.group(1))
        output_name = match.group(2).strip()
        chat_id = message.chat.id
        user_id = message.from_user.id
        replied_id = message.reply_to_message.id

        if not output_name.endswith(".mkv"):
            return await message.reply("❌ Output filename must end with `.mkv`")

        downloaded = []
        for i in range(count):
            msg = await client.get_messages(chat_id, replied_id + i)
            if msg.document:
                filename = msg.document.file_name
                path = os.path.join(DOWNLOADS_DIR, f"{user_id}_{i}.mkv")
                status = await message.reply(f"⬇️ Downloading {filename}")
                start_time = time.time()
                await msg.download(path, progress=progress_bar, progress_args=(status, "Downloading", start_time, filename))
                await status.edit_text(f"✅ Downloaded {filename}")
                downloaded.append(path)

        list_file = os.path.join(DOWNLOADS_DIR, f"{user_id}_inputs.txt")
        async with aiofiles.open(list_file, "w") as f:
            for path in downloaded:
                await f.write(f"file '{os.path.abspath(path)}'\n")

        out_file = os.path.join(DOWNLOADS_DIR, output_name)
        status = await message.reply("⚙️ Merging files...")
        start_time = time.time()

        user_settings = settings_col.find_one({"_id": user_id}) or {}
        meta_args = []
        if user_settings.get("metadata"):
            meta_args = ["-metadata", f"author={user_settings['metadata']}"]
        thumb = user_settings.get("thumbnail")

        ffmpeg_cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", *meta_args, "-y", out_file
        ]

        process = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            try:
                text = line.decode()
                match = re.search(r"time=(\d+:\d+:\d+\.\d+)", text)
                if match:
                    await progress_bar(1, 100, status, "Merging", start_time, output_name)
            except:
                pass

        await process.wait()

        if not os.path.exists(out_file):
            return await status.edit_text("❌ Merging failed.")

        size_mb = os.path.getsize(out_file) / (1024 * 1024)
        if size_mb > 3990:
            await status.edit_text("❌ File exceeds Telegram limit.")
            return

        await status.edit_text("📤 Uploading...")
        await message.reply_document(out_file, caption=f"`{output_name}`", thumb=thumb if thumb else None)

        log_col.insert_one({"user_id": user_id, "file_name": output_name, "size": size_mb, "time": datetime.utcnow()})

        for f in downloaded + [out_file, list_file]:
            os.remove(f)
    except Exception as e:
        logger.exception("Merge error")
        await message.reply(f"❌ Error: {e}")

if __name__ == "__main__":
    app.run()
