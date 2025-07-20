import os
import re
import asyncio
import aiofiles
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
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
db = mongo["MergeDB"]["FileLogs"]

# Create bot app
app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

DOWNLOADS_DIR = "downloads"

# Helper: show progress
async def show_progress(current, total, message: Message, prefix):
    mb_current = current / (1024 * 1024)
    mb_total = total / (1024 * 1024)
    percent = (current / total) * 100
    await message.edit_text(f"{prefix} {mb_current:.2f} MB / {mb_total:.2f} MB ({percent:.1f}%)")

# Command handler
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

        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        replied_id = message.reply_to_message.id
        chat_id = message.chat.id

        downloaded_files = []
        current_msg_id = replied_id

        await message.reply(f"📥 Starting download of {count} files...", quote=True)

        for i in range(count):
            msg = await client.get_messages(chat_id, current_msg_id + i)
            if msg.document and msg.document.file_name.endswith(".mkv"):
                file_path = os.path.join(DOWNLOADS_DIR, f"{chat_id}_{i+1}.mkv")
                progress = await message.reply(f"⬇️ Downloading {msg.document.file_name}...", quote=True)
                await msg.download(file_path, progress=show_progress, progress_args=(progress, "⬇️ Downloading"))
                await progress.edit_text(f"✅ Downloaded: {msg.document.file_name}")
                downloaded_files.append(file_path)
            else:
                return await message.reply("❌ Expected .mkv files only.", quote=True)

        # Create input file list for FFmpeg
        input_txt = os.path.join(DOWNLOADS_DIR, f"{chat_id}_inputs.txt")
        async with aiofiles.open(input_txt, "w") as f:
            for path in downloaded_files:
                await f.write(f"file '{os.path.abspath(path)}'\n")

        output_file = os.path.join(DOWNLOADS_DIR, output_name)
        merging_msg = await message.reply("⚙️ Merging files...", quote=True)

        ffmpeg_cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", input_txt,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-y", output_file
        ]

        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()

        if not os.path.exists(output_file):
            await merging_msg.edit_text("❌ Merging failed.")
            return

        size_mb = os.path.getsize(output_file) / (1024 * 1024)
        if size_mb > 3990:
            await merging_msg.edit_text(f"❌ Output file is too large: {size_mb:.2f} MB (limit is 4GB).")
            os.remove(output_file)
            return

        await merging_msg.edit_text(f"📤 Uploading {output_name} ({size_mb:.2f} MB)...")
        sent = await message.reply_document(output_file, caption=f"`{output_name}`", quote=True)

        # MongoDB log
        db.insert_one({
            "user_id": message.from_user.id,
            "file_name": output_name,
            "file_size_mb": size_mb,
            "date": datetime.utcnow()
        })

        await merging_msg.delete()
        for f in downloaded_files + [output_file, input_txt]:
            os.remove(f)

    except Exception as e:
        logger.exception("Error during merge")
        await message.reply(f"❌ Error: {e}", quote=True)

# Start bot
if __name__ == "__main__":
    print("Bot started.")
    app.run()
                
