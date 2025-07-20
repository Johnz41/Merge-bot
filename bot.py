import os
import re
import asyncio
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from datetime import datetime
from functools import partial
import subprocess

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

mongo_client = MongoClient(MONGO_URI)
db = mongo_client["merge_bot_db"]
logs_collection = db["merge_logs"]

# Human readable size
def sizeof_fmt(num, suffix="B"):
    for unit in ["", "K", "M", "G"]:
        if abs(num) < 1024:
            return f"{num:.1f} {unit}{suffix}"
        num /= 1024
    return f"{num:.1f} T{suffix}"

# Progress bar
async def progress(current, total, message: Message, msg, prefix="⬇️ Downloading"):
    percent = (current / total) * 100
    bar = "█" * int(percent // 10) + "░" * (10 - int(percent // 10))
    text = f"{prefix} [{bar}] {percent:.1f}% of {sizeof_fmt(total)}"
    try:
        await msg.edit(text)
    except Exception:
        pass

@app.on_message(filters.private & filters.command("start"))
async def start_cmd(_, message: Message):
    await message.reply("👋 Send me multiple MKV files, then reply to the first one with:\n\n`/merge -i <count> -name <output>.mkv`")

@app.on_message(filters.private & filters.command("recent"))
async def recent_merges(_, message: Message):
    user_id = message.from_user.id
    entries = logs_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(5)

    text = "📝 Your Recent Merges:\n\n"
    found = False
    for e in entries:
        found = True
        text += f"• `{e['output_name']}` ({sizeof_fmt(e['size_bytes'])})\n"

    if not found:
        text += "No merges found."

    await message.reply(text)

@app.on_message(filters.private & filters.regex(r"^/merge"))
async def handle_merge(_, message: Message):
    if not message.reply_to_message:
        return await message.reply("❗️Reply to the first MKV file.")

    match = re.match(r"/merge\s+-i\s+(\d+)\s+-name\s+(.+\.mkv)", message.text)
    if not match:
        return await message.reply("❌ Invalid format.\n\nUse: `/merge -i <count> -name filename.mkv`")

    count = int(match.group(1))
    filename = match.group(2)
    chat_id = message.chat.id
    replied_id = message.reply_to_message.id
    user_id = message.from_user.id
    downloaded_files = []

    await message.reply("📥 Fetching files...")
    history = await app.get_chat_history(chat_id, limit=100)
    files = []
    
    for msg in history.messages:
        if msg.id >= replied_id:
            continue
        if msg.video or msg.document:
            files.append(msg)
        if len(files) == count - 1:
            break
    
    files.reverse()
    files.insert(0, message.reply_to_message)

    # Downloading
    for i, msg in enumerate(files, start=1):
        media = msg.document or msg.video
        d_msg = await message.reply(f"⬇️ Downloading file {i}/{count}...")
        downloaded_path = await app.download_media(
            media,
            file_name=f"{user_id}_{i}.mkv",
            progress=progress,
            progress_args=(message, d_msg)
        )
        await d_msg.edit(f"✅ Downloaded: {os.path.basename(downloaded_path)}")
        downloaded_files.append(downloaded_path)

    # Prepare concat list file
    list_path = f"{user_id}_inputs.txt"
    async with aiofiles.open(list_path, mode="w") as f:
        for path in downloaded_files:
            await f.write(f"file '{path}'\n")

    # Merge using ffmpeg
    output_path = f"{user_id}_merged.mkv"
    m_msg = await message.reply("🔄 Starting merge...")

    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Show raw ffmpeg progress
    while True:
        line = await process.stderr.readline()
        if not line:
            break
        decoded = line.decode().strip()
        if "time=" in decoded:
            await m_msg.edit(f"🔄 Merging...\n`{decoded}`")

    await process.wait()

    # Upload result
    if os.path.exists(output_path):
        size = os.path.getsize(output_path)
        u_msg = await message.reply(f"📤 Uploading {filename} ({sizeof_fmt(size)})...")
        await app.send_document(chat_id, document=output_path, file_name=filename)
        await u_msg.edit("✅ Done!")

        # Save to MongoDB
        logs_collection.insert_one({
            "user_id": user_id,
            "username": message.from_user.username,
            "file_count": count,
            "file_names": [os.path.basename(f) for f in downloaded_files],
            "output_name": filename,
            "size_bytes": size,
            "chat_id": chat_id,
            "timestamp": datetime.utcnow().isoformat()
        })

    else:
        await message.reply("❌ Merging failed.")

    # Clean up
    try:
        os.remove(list_path)
        os.remove(output_path)
        for f in downloaded_files:
            os.remove(f)
    except:
        pass

if __name__ == "__main__":
    print("🚀 Bot started.")
    app.run()
