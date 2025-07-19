import os
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatAction
from aiofiles import open as aioopen
import subprocess

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_sessions = {}

def sizeof_fmt(num, suffix="B"):
    for unit in ["", "K", "M", "G"]:
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f} T{suffix}"

@app.on_message(filters.private & filters.command("start"))
async def start(_, message: Message):
    await message.reply("Hi! Send me multiple .mkv files, then reply to the **first one** with:\n\n`/merge -i 3 -name movie.mkv`", quote=True)

@app.on_message(filters.private & filters.command("merge"))
async def handle_merge(_, message: Message):
    if not message.reply_to_message or not message.reply_to_message.video and not message.reply_to_message.document:
        return await message.reply("⚠️ Please **reply to a video or file** to start merging.")

    match = re.match(r"/merge\s+-i\s+(\d+)\s+-name\s+(.+)", message.text)
    if not match:
        return await message.reply("❌ Invalid format.\nUse: `/merge -i 3 -name output.mkv`", quote=True)

    count = int(match.group(1))
    filename = match.group(2).strip()
    user_id = message.from_user.id

    await message.reply(f"🧩 Collecting {count} files...")

    # Collect file message_ids after the replied file
    replied_id = message.reply_to_message.id
    chat_id = message.chat.id

    files = []
    async for msg in app.get_chat_history(chat_id, offset_id=replied_id - 1, reverse=True):
        if msg.from_user and msg.from_user.id == user_id:
            if msg.document or msg.video:
                files.append(msg)
                if len(files) == count:
                    break

    if len(files) < count:
        return await message.reply(f"❌ Only found {len(files)} files. Expected {count}.", quote=True)

    await message.reply("📥 Downloading files...")

    downloaded_files = []
    for i, file_msg in enumerate(files, start=1):
        media = file_msg.document or file_msg.video
        path = f"{user_id}_{i}.mkv"
        await message.reply_chat_action(ChatAction.UPLOAD_VIDEO)
        d_msg = await message.reply(f"⬇️ Downloading file {i}/{count} ({sizeof_fmt(media.file_size)})...")
        await app.download_media(media, file_name=path)
        downloaded_files.append(path)
        await d_msg.edit(f"✅ Downloaded file {i} ({sizeof_fmt(media.file_size)})")

    list_path = f"{user_id}_inputs.txt"
    async with aioopen(list_path, "w") as f:
        for path in downloaded_files:
            await f.write(f"file '{os.path.abspath(path)}'\n")

    await message.reply("🎬 Merging files, please wait...")

    output_path = os.path.abspath(f"{user_id}_{filename}")
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        return await message.reply("❌ Failed to merge. Make sure files are valid .mkv and ffmpeg is working.")

    size = os.path.getsize(output_path)
    await message.reply(f"📤 Uploading `{filename}` ({sizeof_fmt(size)})...")

    await app.send_document(chat_id, output_path, caption=f"🎉 Merged `{filename}` successfully!")

    # Cleanup
    for f in downloaded_files:
        os.remove(f)
    os.remove(list_path)
    os.remove(output_path)

@app.on_message(filters.private & (filters.video | filters.document))
async def store_file(_, message: Message):
    # Just acknowledge receipt; the real handler is /merge
    await message.reply("✅ File received. Now reply to this file with `/merge -i X -name file.mkv`")

app.run()
