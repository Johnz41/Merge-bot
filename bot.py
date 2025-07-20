import os
import re
import time
import asyncio
import aiofiles
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient
from datetime import datetime

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mongo setup
mongo = MongoClient(MONGO_URI)
db = mongo["MergeDB"]
file_logs = db["FileLogs"]
user_prefs = db["UserPrefs"]

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

DOWNLOADS_DIR = "downloads"
last_edit_time = {}

# Helper: show progress
async def show_progress(current, total, message: Message, prefix, start_time, user_id):
    now = time.time()
    if user_id not in last_edit_time or now - last_edit_time[user_id] > 3:
        last_edit_time[user_id] = now
        elapsed = now - start_time
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0

        percent = current / total * 100
        progress_bar = "●" + "○" * 9
        progress_text = (
            f"{prefix}: `{message.reply_to_message.document.file_name if message.reply_to_message else ''}`\n"
            f"👨 Userid : {user_id}\n"
            f"{progress_bar} {percent:.2f}%\n"
            f"🔄️{current / (1024**2):.2f}MB of {total / (1024**2):.2f}MB\n"
            f"📊Speed: {speed / (1024**2):.2f}MB/s\n"
            f"⏰Estimated: {int(eta)} seconds\n"
            f"⏱️Elapsed: {int(elapsed)} seconds"
        )
        try:
            await message.edit_text(progress_text)
        except:
            pass

@app.on_message(filters.command("start"))
async def start(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Merge 🔁", callback_data="toggle_merge")],
        [InlineKeyboardButton("Thumbnail 🖼️", callback_data="set_thumbnail")],
        [InlineKeyboardButton("Metadata 📝", callback_data="set_metadata")]
    ])
    await message.reply("Choose an option:", reply_markup=keyboard)

@app.on_callback_query()
async def handle_buttons(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id

    if callback_query.data == "toggle_merge":
        prefs = user_prefs.find_one({"user_id": user_id}) or {}
        merge = prefs.get("merge_enabled", False)
        user_prefs.update_one(
            {"user_id": user_id},
            {"$set": {"merge_enabled": not merge}},
            upsert=True
        )
        await callback_query.answer(f"Merge {'Enabled' if not merge else 'Disabled'}")
    elif callback_query.data == "set_thumbnail":
        user_prefs.update_one(
            {"user_id": user_id},
            {"$set": {"awaiting_thumbnail": True}},
            upsert=True
        )
        await callback_query.message.reply("📸 Send a photo within 60 seconds...")
        await asyncio.sleep(60)
        user_prefs.update_one(
            {"user_id": user_id},
            {"$unset": {"awaiting_thumbnail": ""}}
        )
    elif callback_query.data == "set_metadata":
        user_prefs.update_one(
            {"user_id": user_id},
            {"$set": {"awaiting_metadata": True}},
            upsert=True
        )
        await callback_query.message.reply("📝 Send metadata like: `Exclusive By: @hc_filez` (60 sec)")
        await asyncio.sleep(60)
        user_prefs.update_one(
            {"user_id": user_id},
            {"$unset": {"awaiting_metadata": ""}}
        )

@app.on_message(filters.photo)
async def save_thumbnail(client, message: Message):
    user_id = message.from_user.id
    prefs = user_prefs.find_one({"user_id": user_id})
    if prefs and prefs.get("awaiting_thumbnail"):
        file_path = f"{DOWNLOADS_DIR}/{user_id}_thumb.jpg"
        await message.download(file_path)
        user_prefs.update_one({"user_id": user_id}, {"$set": {"thumbnail_path": file_path}})
        await message.reply("✅ Thumbnail saved!")

@app.on_message(filters.text & filters.private)
async def save_metadata(client, message: Message):
    user_id = message.from_user.id
    prefs = user_prefs.find_one({"user_id": user_id})
    if prefs and prefs.get("awaiting_metadata"):
        user_prefs.update_one(
            {"user_id": user_id},
            {"$set": {"custom_metadata": message.text}}
        )
        await message.reply("✅ Metadata saved!")

@app.on_message(filters.command("merge") & filters.reply)
async def handle_merge(client: Client, message: Message):
    try:
        match = re.match(r"/merge\s+-i\s*(\d+)\s+-name\s+(.+)", message.text)
        if not match:
            return await message.reply("❌ Usage: `/merge -i 2 -name movie.mkv`", quote=True)

        count = int(match.group(1))
        output_name = match.group(2).strip()
        if not output_name.endswith(".mkv"):
            return await message.reply("❌ Output filename must end with `.mkv`")

        user_id = message.from_user.id
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        replied_id = message.reply_to_message.id
        chat_id = message.chat.id

        downloaded_files = []
        current_msg_id = replied_id
        await message.reply(f"📥 Downloading {count} files...")

        for i in range(count):
            msg = await client.get_messages(chat_id, current_msg_id + i)
            if msg.document and msg.document.file_name.endswith(".mkv"):
                file_path = os.path.join(DOWNLOADS_DIR, f"{chat_id}_{i+1}.mkv")
                progress = await message.reply(f"⬇️ Downloading {msg.document.file_name}...")
                start_time = time.time()
                await msg.download(file_path, progress=show_progress, progress_args=(progress, "Downloading", start_time, user_id))
                await progress.edit_text(f"✅ Downloaded: {msg.document.file_name}")
                downloaded_files.append(file_path)
            else:
                return await message.reply("❌ Please reply to MKV files only.")

        input_txt = os.path.join(DOWNLOADS_DIR, f"{chat_id}_inputs.txt")
        async with aiofiles.open(input_txt, "w") as f:
            for path in downloaded_files:
                await f.write(f"file '{os.path.abspath(path)}'\n")

        output_file = os.path.join(DOWNLOADS_DIR, output_name)
        merging_msg = await message.reply("⚙️ Merging files...")

        prefs = user_prefs.find_one({"user_id": user_id}) or {}
        metadata = prefs.get("custom_metadata")
        metadata_opts = []
        if metadata:
            metadata_opts = ["-metadata:s:a", f"title={metadata}", "-metadata:s:s", f"title={metadata}", "-metadata", f"author={metadata}"]

        ffmpeg_cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", input_txt,
            "-c", "copy", "-y", *metadata_opts, output_file
        ]

        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        start_merge = time.time()
        while True:
            if process.stderr.at_eof():
                break
            await asyncio.sleep(5)
            try:
                size_now = os.path.getsize(output_file)
                await show_progress(size_now, 1024**3 * 4, merging_msg, "Merging", start_merge, user_id)
            except: pass

        await process.communicate()

        if not os.path.exists(output_file):
            return await merging_msg.edit_text("❌ Merging failed.")

        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        if size_mb > 3990:
            os.remove(output_file)
            return await merging_msg.edit_text(f"❌ Output too large: {size_mb:.2f} MB")

        caption = f"`{output_name}`"
        thumb = prefs.get("thumbnail_path")
        if thumb and os.path.exists(thumb):
            await message.reply_document(output_file, caption=caption, thumb=thumb)
        else:
            await message.reply_document(output_file, caption=caption)

        file_logs.insert_one({
            "user_id": user_id,
            "file_name": output_name,
            "file_size_mb": size_mb,
            "metadata": metadata,
            "date": datetime.utcnow()
        })

        await merging_msg.delete()
        for f in downloaded_files + [output_file, input_txt]:
            os.remove(f)
    except Exception as e:
        logger.exception("Error during merge")
        await message.reply(f"❌ Error: {e}", quote=True)

if __name__ == "__main__":
    print("Bot started.")
    app.run()
    
