import os
import re
import asyncio
import aiofiles
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from datetime import datetime
from time import time
import subprocess

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Mongo setup
mongo = MongoClient(MONGODB_URI)
db = mongo["mergebot"]
collection = db["merged_files"]

logging.basicConfig(level=logging.INFO)

# Human-readable size
def format_size(bytes_size):
    return f"{bytes_size / (1024 * 1024):.1f} MB"

# Download with progress
async def download_file(app, message, path, progress_msg):
    start = time()
    async def progress(current, total):
        percent = current * 100 / total
        speed = current / (time() - start + 1)
        await progress_msg.edit_text(f"⬇️ Downloading: {percent:.2f}% of {format_size(total)}\nSpeed: {format_size(speed)}/s")
    return await app.download_media(message, file_name=path, progress=progress)

@app.on_message(filters.command("merge") & filters.reply)
async def handle_merge(client, message: Message):
    replied = message.reply_to_message
    match = re.match(r"/merge\s+-i\s+(\d+)\s+-name\s+(.+)", message.text)
    if not match:
        return await message.reply("❌ Invalid format. Use:\n`/merge -i 2 -name movie.mkv`", quote=True)

    try:
        count = int(match.group(1))
        output_name = match.group(2).strip()
    except:
        return await message.reply("❌ Couldn't parse command.")

    merging_msg = await message.reply("📥 Preparing to download files...")

    downloaded_files = []
    chat_id = message.chat.id
    current_id = replied.id
    for i in range(count):
        msg = await client.get_messages(chat_id, current_id + i)
        if not msg or not msg.video and not msg.document:
            await merging_msg.edit_text(f"❌ Message {current_id+i} is not a media file.")
            return
        filename = f"{chat_id}_{i}.mkv"
        progress_msg = await message.reply(f"⬇️ Downloading file {i+1}/{count}...")
        downloaded = await download_file(client, msg, filename, progress_msg)
        if not downloaded:
            await merging_msg.edit_text(f"❌ File {i+1} failed to download.")
            return
        downloaded_files.append(filename)
        await progress_msg.delete()

    await merging_msg.edit_text("🔀 Merging files...")

    file_id = str(message.id)
    input_list = f"{file_id}_inputs.txt"
    async with aiofiles.open(input_list, "w") as f:
        for file in downloaded_files:
            await f.write(f"file '{os.path.abspath(file)}'\n")

    output_path = f"{output_name}"
    ffmpeg_cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", input_list,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-y", output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        await merging_msg.edit_text(f"❌ FFmpeg failed:\n```\n{stderr.decode()[-1500:]}\n```")
        return

    await merging_msg.edit_text(f"📤 Uploading {output_name} ({format_size(os.path.getsize(output_path))})...")

    sent = await message.reply_document(output_path)

    # Log to MongoDB
    collection.insert_one({
        "user_id": message.from_user.id,
        "file_name": output_name,
        "file_size": os.path.getsize(output_path),
        "timestamp": datetime.utcnow(),
        "message_id": sent.id
    })

    await merging_msg.edit_text("✅ Merging and upload complete!")

    # Cleanup
    for f in downloaded_files + [output_path, input_list]:
        if os.path.exists(f):
            os.remove(f)

print("Bot started...")
app.run()
                    
