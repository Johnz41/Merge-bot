import os
import aiofiles
import asyncio
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from datetime import datetime

# Heroku/Environment Config
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URI = os.environ.get("MONGODB_URI")

# Mongo Setup
mongo = MongoClient(MONGODB_URI)
db = mongo['MergeBot']
collection = db['MergedFiles']

# Bot Setup
app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
logging.basicConfig(level=logging.INFO)

# Progress Helper
async def show_progress(msg, current, total, tag):
    percent = (current / total) * 100
    progress = f"{tag}: {current/1024/1024:.2f}MB / {total/1024/1024:.2f}MB ({percent:.2f}%)"
    try:
        await msg.edit(progress)
    except:
        pass

# Download File
async def download_file(bot, msg, filename, index):
    status = await msg.reply_text(f"📥 Downloading file {index+1}...")
    path = f"{msg.chat.id}_{index+1}.mkv"
    downloaded = 0

    async def progress(current, total):
        nonlocal downloaded
        if current - downloaded >= 2 * 1024 * 1024:
            downloaded = current
            await show_progress(status, current, total, f"Downloading {index+1}")

    file = await msg.download(file_name=path, progress=progress)
    await status.edit(f"✅ Downloaded: {os.path.basename(file)}")
    return path

# Merge Handler
@app.on_message(filters.command("merge") & filters.reply)
async def handle_merge(client, message: Message):
    try:
        args = message.text.split(" ", 2)
        if len(args) < 3 or not args[1].startswith("-i") or not args[2].startswith("-name"):
            return await message.reply("❌ Usage: /merge -i 3 -name output.mkv")

        count = int(args[1].replace("-i", ""))
        output_name = args[2].replace("-name", "").strip()
        if not output_name.endswith(".mkv"):
            return await message.reply("❌ Output file must end with `.mkv`")

        chat_id = message.chat.id
        replied_id = message.reply_to_message.id

        # Fetch N-1 messages before reply
        files = []
        async for msg in client.get_chat_history(chat_id, offset_id=replied_id - 1, limit=100):
            if msg.video or msg.document:
                files.append(msg)
            if len(files) >= count - 1:
                break

        files.reverse()
        files.insert(0, message.reply_to_message)
        if len(files) != count:
            return await message.reply(f"❌ Found {len(files)} files, expected {count}")

        # Download
        paths = []
        for i, msg in enumerate(files):
            path = await download_file(client, msg, output_name, i)
            paths.append(path)

        # Write concat file
        list_path = f"{chat_id}_inputs.txt"
        async with aiofiles.open(list_path, mode="w") as f:
            for path in paths:
                await f.write(f"file '{os.path.abspath(path)}'\n")

        output_path = os.path.join(os.getcwd(), output_name)
        status = await message.reply("🔄 Merging files...")

        # Merge with re-encoding
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-y", output_path
        ]
        process = await asyncio.create_subprocess_exec(*cmd)
        await process.communicate()

        # Check output
        if not os.path.exists(output_path):
            return await status.edit("❌ Merge failed. Output not created.")

        size = os.path.getsize(output_path)
        if size > 4 * 1024 * 1024 * 1024:
            return await status.edit("❌ Merged file exceeds 4GB limit. Aborting.")

        await status.edit(f"📤 Uploading {output_name} ({size/1024/1024:.2f} MB)...")
        await message.reply_document(document=output_path, caption=f"✅ Merged `{output_name}`")

        # Log to MongoDB
        collection.insert_one({
            "filename": output_name,
            "size_MB": round(size / 1024 / 1024, 2),
            "user_id": message.from_user.id,
            "username": message.from_user.username,
            "date": datetime.utcnow()
        })

        # Cleanup
        os.remove(output_path)
        os.remove(list_path)
        for path in paths:
            os.remove(path)

    except Exception as e:
        logging.error(str(e))
        await message.reply(f"❌ Error: {e}")

if __name__ == "__main__":
    app.run()
