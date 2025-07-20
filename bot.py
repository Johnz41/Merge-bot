import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient

# Load from environment
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_DB_URI = os.environ.get("MONGO_URI")

# Constants
DEFAULT_THUMB = "https://envs.sh/e3P.jpg"
DEFAULT_META = "HC_Filez"
DOWNLOAD_DIR = "downloads"

# Logging
logging.basicConfig(level=logging.INFO)

# Mongo
mongo_client = AsyncIOMotorClient(MONGO_DB_URI)
db = mongo_client.MergeBot
settings_col = db.user_settings
pending_col = db.pending_files

# Bot init
app = Client("MergeBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Utilities
async def get_user_settings(user_id):
    settings = await settings_col.find_one({"_id": user_id})
    if not settings:
        return {"thumbnail": DEFAULT_THUMB, "metadata": DEFAULT_META}
    return {
        "thumbnail": settings.get("thumbnail", DEFAULT_THUMB),
        "metadata": settings.get("metadata", DEFAULT_META)
    }

# Start
@app.on_message(filters.command("start"))
async def start(_, message: Message):
    await message.reply_text(
        "👋 Welcome! Send me at least 2 videos and I’ll merge them.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")]
        ])
    )

# Settings Menu
@app.on_callback_query(filters.regex("settings"))
async def settings_menu(_, query):
    user_id = query.from_user.id
    settings = await get_user_settings(user_id)
    thumb = settings["thumbnail"]
    meta = settings["metadata"]

    await query.message.edit(
        f"<b>⚙️ Settings</b>\n\n📷 <b>Thumbnail:</b> <code>{thumb}</code>\n✍️ <b>Metadata:</b> <code>{meta}</code>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Set Thumbnail", callback_data="set_thumb"),
                InlineKeyboardButton("Set Metadata", callback_data="set_meta")
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="back"),
                InlineKeyboardButton("❌ Close", callback_data="close")
            ]
        ]),
        parse_mode="html"
    )

# Set metadata
@app.on_callback_query(filters.regex("set_meta"))
async def ask_metadata(_, query):
    await query.message.edit("✍️ Send me the text you want as your metadata.")
    app.set_state(query.from_user.id, "set_meta")

@app.on_message(filters.private & filters.text)
async def set_meta_text(_, message):
    if await app.get_state(message.from_user.id) == "set_meta":
        await settings_col.update_one({"_id": message.from_user.id}, {"$set": {"metadata": message.text}}, upsert=True)
        await message.reply("✅ Metadata saved!")
        await app.set_state(message.from_user.id, None)

# Set thumbnail
@app.on_callback_query(filters.regex("set_thumb"))
async def ask_thumb(_, query):
    await query.message.edit("📤 Send a photo to set as thumbnail.")
    app.set_state(query.from_user.id, "set_thumb")

@app.on_message(filters.private & filters.photo)
async def save_thumb(_, message):
    if await app.get_state(message.from_user.id) == "set_thumb":
        path = os.path.join(DOWNLOAD_DIR, f"{message.from_user.id}_thumb.jpg")
        await message.download(file_name=path)
        await settings_col.update_one({"_id": message.from_user.id}, {"$set": {"thumbnail": path}}, upsert=True)
        await message.reply("✅ Thumbnail saved!")
        await app.set_state(message.from_user.id, None)

# Close and back buttons
@app.on_callback_query(filters.regex("close"))
async def close_cb(_, query):
    await query.message.delete()

@app.on_callback_query(filters.regex("back"))
async def back_cb(_, query):
    await start(_, query.message)

# Receive videos
@app.on_message(filters.private & filters.video)
async def collect_videos(_, message):
    user_id = message.from_user.id
    file_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_{message.video.file_unique_id}.mp4")
    await message.download(file_path)
    await pending_col.update_one({"_id": user_id}, {"$push": {"files": file_path}}, upsert=True)

    data = await pending_col.find_one({"_id": user_id})
    files = data.get("files", [])

    if len(files) >= 2:
        await message.reply("🔁 Merging your videos, please wait...")
        merged_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_merged.mp4")
        await merge_files(files, merged_path, user_id)

        settings = await get_user_settings(user_id)
        await app.send_video(
            chat_id=message.chat.id,
            video=merged_path,
            caption=f"🎬 Merged by <b>{settings['metadata']}</b>",
            thumb=settings["thumbnail"] if os.path.exists(settings["thumbnail"]) else DEFAULT_THUMB,
            parse_mode="html",
            supports_streaming=True
        )

        await pending_col.delete_one({"_id": user_id})
        for f in files:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(merged_path):
            os.remove(merged_path)
    else:
        await message.reply("✅ Video saved. Send one more to merge.")

# Merge using ffmpeg
async def merge_files(file_list, output_path, user_id):
    list_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_inputs.txt")
    with open(list_path, "w") as f:
        for file in file_list:
            f.write(f"file '{file}'\n")

    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", output_path
    ]
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await process.communicate()
    os.remove(list_path)

# Run
if __name__ == "__main__":
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    app.run()
    
