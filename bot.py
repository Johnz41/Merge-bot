import os
import re
import time
import asyncio
import aiofiles
import logging
import math
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from datetime import datetime

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
USER_SESSION_STRING = os.environ.get("USER_SESSION_STRING")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo = MongoClient(MONGO_URI)
db = mongo["MergeDB"]["FileLogs"]

bot = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
userbot = Client("user-uploader", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION_STRING)

DOWNLOADS_DIR = "downloads"
last_edit_time = {}

async def show_progress(current, total, message: Message, prefix, start_time):
    now = time.time()
    elapsed = now - start_time
    if elapsed == 0:
        elapsed = 0.1

    key = message.chat.id
    if key not in last_edit_time or now - last_edit_time[key] > 3:
        last_edit_time[key] = now

        percent = current * 100 / total
        filled = int(percent // 10)
        bar = "●" * filled + "○" * (10 - filled)

        speed = current / elapsed
        eta = (total - current) / speed if speed != 0 else 0

        speed_mb = speed / (1024 * 1024)
        current_gb = current / (1024 ** 3)
        total_gb = total / (1024 ** 3)

        eta_minutes = int(eta // 60)
        elapsed_minutes = int(elapsed // 60)

        text = (
            f"👨 Userid : {message.from_user.id}\n"
            f"{bar} {percent:.1f}%\n"
            f"🔄️{current_gb:.2f}GB of {total_gb:.2f}GB\n"
            f"📊Speed: {speed_mb:.2f}MB/s\n"
            f"⏰Estimated: {eta_minutes} minutes\n"
            f"🌱Seeders: 17 | 🐒Leechers: 18\n"
            f"⏱️Elapsed: {elapsed_minutes} minutes"
        )

        try:
            await message.edit_text(f"{prefix}\n{text}")
        except Exception:
            pass
            

# Get codec from file
def detect_codec(filepath):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return result.stdout.strip()
    except Exception:
        return None

@bot.on_message(filters.command("merge") & filters.reply)
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
        codecs_used = set()
        current_msg_id = replied_id

        await message.reply(f"📥 Starting download of {count} files...", quote=True)

        for i in range(count):
            msg = await client.get_messages(chat_id, current_msg_id + i)
            if msg.document and msg.document.file_name.endswith(".mkv"):
                file_path = os.path.join(DOWNLOADS_DIR, f"{chat_id}_{i+1}.mkv")
                progress = await message.reply(f"⬇️ Downloading {msg.document.file_name}...", quote=True)
                await msg.download(file_path, progress=show_progress, progress_args=(progress, "⬇️ Downloading"))
                await progress.edit_text(f"✅ Downloaded: {msg.document.file_name}")
                codec = detect_codec(file_path)
                if codec:
                    codecs_used.add(codec)
                downloaded_files.append(file_path)
            else:
                return await message.reply("❌ Expected .mkv files only.", quote=True)

        input_txt = os.path.join(DOWNLOADS_DIR, f"{chat_id}_inputs.txt")
        async with aiofiles.open(input_txt, "w") as f:
            for path in downloaded_files:
                await f.write(f"file '{os.path.abspath(path)}'\n")

        output_file = os.path.join(DOWNLOADS_DIR, output_name)
        merging_msg = await message.reply("⚙️ Merging files...", quote=True)

        # Re-encode to x265 (avoids low MB bug and codec mismatch)
        ffmpeg_cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", input_txt,
            "-c:v", "libx265", "-crf", "28", "-preset", "veryfast",
            "-y", output_file
        ]

        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Show live ffmpeg progress
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            try:
                line = line.decode("utf-8")
                if "time=" in line:
                    await merging_msg.edit_text(f"⚙️ Merging files...\n`{line.strip()}`")
            except Exception:
                pass

        await process.wait()

        if not os.path.exists(output_file):
            await merging_msg.edit_text("❌ Merging failed.")
            return

        size_mb = os.path.getsize(output_file) / (1024 * 1024)

        # Upload with bot if <= 2GB
        if size_mb <= 1990:
            await merging_msg.edit_text(f"📤 Uploading with bot ({size_mb:.2f} MB)...")
            sent = await message.reply_document(output_file, caption=f"`{output_name}`", quote=True)

        # Upload with userbot if > 2GB
        else:
            await merging_msg.edit_text(f"📤 Uploading via userbot ({size_mb:.2f} MB)...")
            await userbot.send_document(
                chat_id=message.chat.id,
                document=output_file,
                caption=f"`{output_name}`"
            )

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

if __name__ == "__main__":
    print("Bot started.")
    userbot.start()
    bot.run()
    userbot.stop()
    
