import os
import shutil
import asyncio
import aiofiles
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_TOKEN

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# user_id: [list of files]
user_files = {}

@app.on_message(filters.video | filters.document)
async def collect_files(client, message: Message):
    user_id = message.from_user.id
    media = message.video or message.document
    if not media or media.file_size > 4 * 1024 * 1024 * 1024:
        return await message.reply("File too large or unsupported.")
    
    os.makedirs(f"downloads/{user_id}", exist_ok=True)
    path = f"downloads/{user_id}/{media.file_name}"
    await message.reply("Downloading...")
    await client.download_media(message, path)
    
    user_files.setdefault(user_id, []).append(path)
    await message.reply(f"File saved. Total files: {len(user_files[user_id])}")

@app.on_message(filters.command("merge"))
async def merge_handler(client, message: Message):
    user_id = message.from_user.id
    args = message.text.split(" ", maxsplit=2)
    
    if len(args) < 3:
        return await message.reply("Usage:\n/merge -i {count} -name rrr movie.mkv")
    
    try:
        count = int(args[1].replace("-i", ""))
        name = args[2].replace("-name", "").strip()
    except Exception as e:
        return await message.reply("Invalid format.")

    files = user_files.get(user_id, [])
    if len(files) < count:
        return await message.reply(f"Expected {count} files, but got {len(files)}.")
    
    merge_dir = f"downloads/{user_id}"
    list_path = os.path.join(merge_dir, "inputs.txt")

    with open(list_path, "w") as f:
        for file in sorted(files)[:count]:
            f.write(f"file '{os.path.abspath(file)}'\n")

    output_path = os.path.join(merge_dir, name)
    cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]

    await message.reply("Merging files...")
    process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    await process.communicate()

    if os.path.exists(output_path):
        await message.reply("Uploading merged file...")
        await message.reply_document(output_path)
    else:
        await message.reply("Merging failed.")

    # Cleanup
    shutil.rmtree(merge_dir, ignore_errors=True)
    user_files[user_id] = []

app.run()
  
