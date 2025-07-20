import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
import subprocess
from datetime import datetime
from pyrogram.errors import FloodWait

# Environment variables
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

# Pyrogram Client
app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# MongoDB setup
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["merge_bot_db"]
logs_collection = db["merge_logs"]

# Size formatter
def sizeof_fmt(num, suffix="B"):
    for unit in ["", "K", "M", "G"]:
        if abs(num) < 1024.0:
            return f"{num:.2f} {unit}{suffix}"
        num /= 1024.0
    return f"{num:.2f} T{suffix}"

# Download progress callback
async def progress(current, total, message: Message, status_msg: Message, prefix="⬇️ Downloading"):
    try:
        percent = (current / total) * 100
        bar = "█" * int(percent // 10) + "░" * (10 - int(percent // 10))
        await status_msg.edit_text(
            f"{prefix} `{bar}` {percent:.1f}% of {sizeof_fmt(total)}"
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)

@app.on_message(filters.command("start"))
async def start(_, message: Message):
    await message.reply("👋 Send your `.mkv` files and reply to the first one with:\n\n`/merge -i <count> -name <filename.mkv>`")

@app.on_message(filters.command("merge"))
async def handle_merge(_, message: Message):
    if not message.reply_to_message:
        return await message.reply("❌ Please reply to the first file with the /merge command.")

    # Parse command
    args = message.text.split()
    try:
        count = int(args[args.index("-i") + 1])
        filename = " ".join(args[args.index("-name") + 1:])
        if not filename.endswith(".mkv"):
            return await message.reply("❌ Output filename must end with `.mkv`")
    except Exception:
        return await message.reply("❌ Usage: `/merge -i <count> -name <filename.mkv>`")

    user_id = message.from_user.id
    chat_id = message.chat.id
    replied_id = message.reply_to_message.id

    # Fetch files
    files = []
    async for msg in app.get_chat_history(chat_id, offset_id=replied_id - 1):
        if msg.video or msg.document:
            files.append(msg)
            if len(files) == count:
                break
    files.reverse()
    files.insert(0, message.reply_to_message)

    if len(files) < count:
        return await message.reply(f"❌ Only found {len(files)} files. Please send all {count} files.")

    downloaded_files = []

    for i, file_msg in enumerate(files, start=1):
        media = file_msg.document or file_msg.video
        ext = os.path.splitext(media.file_name or "")[1] or ".mkv"
        tmp_name = f"{user_id}_{i}{ext}"

        d_msg = await message.reply(f"⬇️ Downloading file {i}/{count} ({sizeof_fmt(media.file_size)})...")

        try:
            downloaded_path = await app.download_media(
                media,
                progress=progress,
                progress_args=(message, d_msg)
            )
            os.rename(downloaded_path, tmp_name)
        except Exception as e:
            return await message.reply(f"❌ Failed to download file {i}.\n\n`{str(e)}`")

        downloaded_files.append(tmp_name)
        await d_msg.edit(f"✅ Downloaded `{tmp_name}`")

    # Create concat list
    list_path = f"{user_id}_inputs.txt"
    with open(list_path, "w") as f:
        for file in downloaded_files:
            f.write(f"file '{os.path.abspath(file)}'\n")

    # Start merging
    output_path = filename
    m_msg = await message.reply("🔄 Merging files...")

    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    while True:
        line = await process.stderr.readline()
        if not line:
            break
        decoded = line.decode("utf-8").strip()
        if "time=" in decoded:
            await m_msg.edit(f"🔄 Merging...\n`{decoded}`")

    await process.wait()

    if not os.path.exists(output_path):
        return await m_msg.edit("❌ Merging failed. Output file not found.")

    await m_msg.edit(f"📤 Uploading `{filename}` ({sizeof_fmt(os.path.getsize(output_path))})...")

    await message.reply_document(
        document=output_path,
        caption=f"✅ Merged `{filename}` successfully!"
    )

    # Log to MongoDB
    logs_collection.insert_one({
        "user_id": user_id,
        "username": message.from_user.username,
        "file_count": count,
        "file_names": downloaded_files,
        "output_name": filename,
        "output_size": os.path.getsize(output_path),
        "chat_id": chat_id,
        "timestamp": datetime.utcnow()
    })

    # Cleanup
    for f in downloaded_files:
        os.remove(f)
    os.remove(list_path)
    os.remove(output_path)

@app.on_message(filters.command("recent"))
async def recent_merges(_, message: Message):
    user_id = message.from_user.id
    entries = logs_collection.find({"user_id": user_id}).sort("timestamp", -1).limit(5)

    text = "📝 Your Recent Merges:\n\n"
    found = False
    for e in entries:
        found = True
        text += f"• {e['output_name']} ({sizeof_fmt(e['output_size'])})\n"

    if not found:
        text += "No merges found."

    await message.reply(text)

# Run the bot
app.run()
        
