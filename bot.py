import os
import shutil
import asyncio
import aiofiles
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from config import API_ID, API_HASH, BOT_TOKEN

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_tasks = {}

@app.on_message(filters.command("merge"))
async def handle_merge_command(client, message: Message):
    if not message.reply_to_message:
        return await message.reply("Reply to the **first file** with:\n`/merge -i 5 -name movie.mkv`")

    user_id = message.from_user.id
    try:
        args = message.text.split(" ", maxsplit=2)
        count = int(args[1].replace("-i", ""))
        name = args[2].replace("-name", "").strip()
    except:
        return await message.reply("Invalid format.\nUse: `/merge -i 5 -name movie.mkv`")

    user_tasks[user_id] = {
        "expected": count,
        "received": 0,
        "paths": [],
        "filename": name,
        "dir": f"downloads/{user_id}"
    }

    os.makedirs(user_tasks[user_id]["dir"], exist_ok=True)

    # Download the first file (the one replied to)
    media = message.reply_to_message.video or message.reply_to_message.document
    if not media or media.file_size > 4 * 1024 * 1024 * 1024:
        return await message.reply("First file is missing or too large (<4GB allowed).")

    first_path = os.path.join(user_tasks[user_id]["dir"], media.file_name)
    await message.reply("Downloading first file...")
    await client.download_media(message.reply_to_message, first_path)
    user_tasks[user_id]["paths"].append(first_path)
    user_tasks[user_id]["received"] += 1

    await message.reply(f"First file downloaded. Waiting for {count - 1} more files...")

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
    await message.reply(f"Downloading file {task['received'] + 1} of {task['expected']}...")
    await client.download_media(message, path)
    task["paths"].append(path)
    task["received"] += 1

    if task["received"] == task["expected"]:
        await message.reply("All files received. Merging...")

        list_path = os.path.join(task["dir"], "inputs.txt")
        with open(list_path, "w") as f:
            for file in sorted(task["paths"]):
                f.write(f"file '{os.path.abspath(file)}'\n")

        output_path = os.path.join(task["dir"], task["filename"])
        cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]

        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        await process.communicate()

        if os.path.exists(output_path):
            await message.reply("Uploading merged file...")
            await message.reply_document(output_path)
        else:
            await message.reply("Merging failed.")

        # Cleanup
        shutil.rmtree(task["dir"], ignore_errors=True)
        user_tasks.pop(user_id, None)

app.run()
    
