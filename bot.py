import os
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

app = Client("merge-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

mongo = MongoClient(MONGO_URI)
db = mongo["MergeDB"]
users = db["UserSettings"]

@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    await message.reply_text("👋 Hi, I am a merge bot that can merge files for you.\nUse /settings to configure options.")

@app.on_message(filters.command("settings"))
async def settings_handler(client, message: Message):
    user_id = message.from_user.id
    user_data = users.find_one({"_id": user_id}) or {"merge": True, "thumbnail": None, "metadata": None}
    users.update_one({"_id": user_id}, {"$setOnInsert": user_data}, upsert=True)

    merge_state = "ON" if user_data["merge"] else "OFF"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔀 Merge: {merge_state}", callback_data="toggle_merge")],
        [InlineKeyboardButton("🖼️ Thumbnail", callback_data="set_thumbnail")],
        [InlineKeyboardButton("📝 Metadata", callback_data="set_metadata")]
    ])
    await message.reply("⚙️ Settings:", reply_markup=keyboard)

@app.on_callback_query()
async def callback_handler(client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    if data == "toggle_merge":
        current = users.find_one({"_id": user_id})["merge"]
        users.update_one({"_id": user_id}, {"$set": {"merge": not current}})
        new_state = "ON" if not current else "OFF"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔀 Merge: {new_state}", callback_data="toggle_merge")],
            [InlineKeyboardButton("🖼️ Thumbnail", callback_data="set_thumbnail")],
            [InlineKeyboardButton("📝 Metadata", callback_data="set_metadata")]
        ])
        await query.message.edit_text("⚙️ Settings:", reply_markup=keyboard)

    elif data == "set_thumbnail":
        await query.message.edit_text("📸 Please send a photo within 60 seconds.")
        try:
            response = await app.listen(query.message.chat.id, timeout=60)
            if response.photo:
                file_path = f"thumbs/{user_id}.jpg"
                os.makedirs("thumbs", exist_ok=True)
                await response.download(file_path)
                users.update_one({"_id": user_id}, {"$set": {"thumbnail": file_path}})
                await response.reply("✅ Thumbnail saved.")
            else:
                await response.reply("❌ No photo received.")
        except Exception:
            await query.message.reply("⏰ Time expired. Please try again.")

    elif data == "set_metadata":
        await query.message.edit_text("📝 Send metadata (e.g., `Exclusive By: @hc_filez`) within 60 seconds.")
        try:
            response = await app.listen(query.message.chat.id, timeout=60)
            if response.text:
                users.update_one({"_id": user_id}, {"$set": {"metadata": response.text}})
                await response.reply("✅ Metadata saved.")
            else:
                await response.reply("❌ No text received.")
        except Exception:
            await query.message.reply("⏰ Time expired. Please try again.")

if __name__ == "__main__":
    print("Bot started.")
    app.run()
