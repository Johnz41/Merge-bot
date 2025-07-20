import os
import re
import asyncio
import aiofiles
import subprocess
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# Environment variables
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "MergeBotDB")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot client
app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# MongoDB client
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[DB_NAME]
collection = db["logs"]

# Helpers
async def download_file(bot, message: Message, path: str):
    file_size = message.document.file_size / (1024 * 1024)
    sent = await message.reply_text(f"⬇️ Downloading `{message.document.file_name}` ({file_size:.2f} MB)...")
    await bot.download_media(message, file_name=path)
    await sent.edit(f"✅ Downloaded `{message.document.file_name}` ({file_size:.2f} MB)")
    return path

def merge_files_ffmpeg(file_paths, output_path):
    list_file = "inputs.txt"
    with open(list_file, "w") as f:
        for p in file_paths:
            f.write(f"file '{p}'\n")
    
    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]

    subprocess.run(cmd, check=True)

@app.on_message(filters.command("merge") & filters.private)
async def handle_merge(client, message: Message):
    if not message.reply_to_message:
        return await message.reply("❌ Please reply to the first file you want to merge.")

    match = re.match(r"/merge\s+-i\s+(\d+)\s+-name\s+(.+\.mkv)", message.text)
    if not match:
        return await message.reply("❌ Invalid format. Use `/merge -i 2 -name output.mkv`", quote=True)

    count = int(match.group(1))
    output_name = match.group(2)

    chat_id = message.chat.id
    start_msg = message.reply_to_message
    start_id = start_msg.id

    file_paths = []
    for i in range(count):
        msg_id = start_id + i
        try:
            msg = await client.get_messages(chat_id, msg_id)
            if msg.document and msg.document.file_name.endswith(".mkv"):
                file_path = f"{chat_id}_{msg_id}.mkv"
                downloaded = await download_file(client, msg, file_path)
                file_paths.append(downloaded)
            else:
                await message.reply(f"❌ Message {msg_id} does not contain a valid .mkv file.")
                return
        except Exception as e:
            await message.reply(f"❌ Error fetching message {msg_id}: {e}")
            return

    merged_path = f"merged_{chat_id}_{start_id}.mkv"

    try:
        sent = await message.reply("🛠️ Merging files...")
        merge_files_ffmpeg(file_paths, merged_path)
    except subprocess.CalledProcessError as e:
        return await message.reply(f"❌ Merge failed: {e}")
    
    size_mb = os.path.getsize(merged_path) / (1024 * 1024)
    await sent.edit(f"📤 Uploading `{output_name}` ({size_mb:.2f} MB)...")

    await message.reply_document(merged_path, file_name=output_name, caption=f"Merged by @{app.me.username}")

    collection.insert_one({
        "user_id": chat_id,
        "file_name": output_name,
        "parts": [os.path.basename(p) for p in file_paths],
        "final_size_mb": size_mb
    })

    for p in file_paths:
        os.remove(p)
    os.remove(merged_path)
    os.remove("inputs.txt")

    logger.info(f"✅ Merged and sent {output_name} to {chat_id}")

if __name__ == "__main__":
    app.run()
