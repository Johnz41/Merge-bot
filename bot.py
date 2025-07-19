import os
import shutil
import asyncio
import subprocess
import re
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_TOKEN

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_tasks = {}

@app.on_message(filters.command("merge"))
async def handle_merge_command(client, message: Message):
    if not message.reply_to_message:
        return await message.reply("Reply to the **first file** with:\n`/merge -i 2 -name movie.mkv`")

    user_id = message.from_user.id

    # Parse command
    match = re.search(r"-i\s*(\d+)\s*-name\s+(.+)", message.text)
    if not match:
        return await message.reply("Invalid format. Use:\n`/merge -i 2 -name movie.mkv`")

    count = int(match.group(1))
    name = match.group(2).strip()

    user_tasks[user_id] = {
        "expected": count,
        "received": 0,
        "paths": [],
        "filename": name,
        "dir": f"downloads/{user_id}"
    }

    os.makedirs(user_tasks[user_id]["dir"], exist_ok=True)

    # Download first file (the one replied to)
    media = message.reply_to_message.video or message.reply_to_message.document
    if not media or media.file_size > 4 * 1024 * 1024 * 1024:
        return await message.reply("First file is missing or too large (<4GB allowed).")

    first_path = os.path.join(user_tasks[user_id]["dir"], media.file_name)
    info = await message.reply(f"Downloading 1 of {count}...\nSize: {media.file_size // (1024 * 1024)} MB")
    await client.download_media(message.reply_to_message, first_path)
    await info.edit("Downloaded ✅")

    user_tasks[user_id]["paths"].append(first_path)
    user_tasks[user_id]["received"] += 1

    await message.reply(f"Waiting for {count - 1} more files...")

@app.on_message(filters.video | filters.document)
async def handle_file_upload(client, message: Message):
    user_id = message.from_user.id
    task = user_tasks.get(user_id)

    if not task or task["received"] >= task["expected"]:
        return

    media = message.video or message.document
    if not media or media.file_size > 4 * 1024 * 1024 * 1024:
        return await message.reply("File too large or unsupported.")

    path = os.path.join(task["dir"], media.file_name)
    index = task["received"] + 1
    info = await message.reply(f"Downloading {index} of {task['expected']}...\nSize: {media.file_size // (1024 * 1024)} MB")
    await client.download_media(message, path)
    await info.edit("Downloaded ✅")

    task["paths"].append(path)
    task["received"] += 1

    if task["received"] == task["expected"]:
        await message.reply("All files received. Merging...")

        list_path = os.path.join(task["dir"], "inputs.txt")
        with open(list_path, "w") as f:
            for file in sorted(task["paths"]):
                f.write(f"file '{os.path.abspath(file)}'\n")

        output_path = os.path.join(task["dir"], task["filename"])
        merging_msg = await message.reply("Merging in progress...")

        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        await process.communicate()

        if os.path.exists(output_path):
            file_size = os.path.getsize(output_path) // (1024 * 1024)
            await merging_msg.edit(f"Merging done ✅\nUploading `{task['filename']}` ({file_size} MB)...")
            await message.reply_document(output_path)
        else:
            await merging_msg.edit("Merging failed ❌")

        shutil.rmtree(task["dir"], ignore_errors=True)
        user_tasks.pop(user_id, None)

app.run()
        
