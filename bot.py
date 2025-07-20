import os
import re
import asyncio
import aiofiles
import logging
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient

# ENV variables
API_ID = int(os.environ.get("API_ID", 123456))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
MONGO_URI = os.environ.get("MONGO_URI", "your_mongodb_uri")

# Init bot and Mongo
app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = MongoClient(MONGO_URI)
db = mongo["mergebot"]
log_collection = db["logs"]

logging.basicConfig(level=logging.INFO)

# File download progress
async def progress(current, total, message: Message, prefix: str):
    mb = lambda x: round(x / (1024 * 1024), 2)
    await message.edit_text(f"{prefix} {mb(current)} / {mb(total)} MB")

# Merge command handler
@app.on_message(filters.command("merge") & filters.reply)
async def handle_merge(client: Client, message: Message):
    cmd = message.text.split()
    if len(cmd) < 4 or cmd[1] != "-i" or cmd[3] != "-name":
        return await message.reply("❌ Invalid format!\nUse: `/merge -i 2 -name movie.mkv`", quote=True)

    try:
        count = int(cmd[2])
        output_name = cmd[4]
    except Exception:
        return await message.reply("❌ Invalid format!\nUse: `/merge -i 2 -name movie.mkv`", quote=True)

    replied = message.reply_to_message
    chat_id = message.chat.id
    replied_id = replied.id

    await message.reply("📥 Downloading files...")

    file_paths = []
    for i in range(count):
        msg = await client.get_messages(chat_id, replied_id + i)
        if not msg or not msg.document:
            return await message.reply(f"❌ Missing file at index {i}.", quote=True)

        filename = msg.document.file_name
        if not filename.endswith(".mkv"):
            return await message.reply(f"❌ Only `.mkv` files allowed. Skipped: {filename}", quote=True)

        path = f"{msg.id}_{i}.mkv"
        file_path = await client.download_media(
            msg,
            file_name=path,
            progress=progress,
            progress_args=(message, f"⬇️ Downloading {i+1}/{count}:")
        )
        file_paths.append(file_path)

    # Save to MongoDB
    log_collection.insert_one({
        "user_id": message.from_user.id,
        "file_count": count,
        "output_name": output_name,
        "file_paths": file_paths
    })

    # Write inputs.txt
    input_txt = f"{message.from_user.id}_inputs.txt"
    async with aiofiles.open(input_txt, "w") as f:
        for path in file_paths:
            await f.write(f"file '{os.path.abspath(path)}'\n")

    await message.reply("🛠 Merging started...")

    output_file = output_name
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", input_txt,
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", output_file
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    _, stderr = await process.communicate()

    if process.returncode != 0:
        await message.reply(f"❌ Merge failed:\n```{stderr.decode()}```", quote=True)
        return

    # Show output file size
    size_mb = round(os.path.getsize(output_file) / (1024 * 1024), 2)
    await message.reply(f"📤 Uploading `{output_file}` ({size_mb} MB)...")

    await client.send_document(
        chat_id=chat_id,
        document=output_file,
        caption=f"✅ Merged: `{output_file}`\nSize: {size_mb} MB"
    )

    # Cleanup
    os.remove(output_file)
    os.remove(input_txt)
    for path in file_paths:
        os.remove(path)

    await message.reply("✅ Merging complete!")

# Start bot
app.run()
