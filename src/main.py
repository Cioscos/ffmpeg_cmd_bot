import logging
import os
import traceback
import html
import json
import asyncio
from typing import Optional

from env_manager import keyring_get, keyring_initialize
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%y-%m-%d %H:%M:%S',
    filename='ffmpeg_cmd_bot.log',
    filemode='a'
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TEMP_DOWNLOAD_PATH = './temp_download_path/'


def parse_ffmpeg_command(command_string: str, input_file_name: str) -> tuple[list[str], Optional[str]]:
    """
    Parses an FFmpeg command string to find the input and output file names.

    Args:
        command_string (str): The FFmpeg command as a single string.
        input_file_name (str): The input file name

    Returns:
        A tuple containing the command parts converted from string and the output file name.
    """
    # Split the command string into parts
    parts = command_string.split()

    effective_command_parts = ['ffmpeg', '-i', input_file_name]

    # Add command string converted to parts into effective command parts
    effective_command_parts.extend(parts)

    output_file = None

    # The output file is typically the last argument in the command,
    # but we should ensure it is not a parameter or an option.
    # Basic check: not starting with '-' and contains a dot (.)
    if effective_command_parts[-1] and not effective_command_parts[-1].startswith('-') and '.' in effective_command_parts[-1]:
        output_file = TEMP_DOWNLOAD_PATH + effective_command_parts[-1]
        effective_command_parts[-1] = output_file

    return effective_command_parts, output_file


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    The error callback function.
    This function is used to handle possible Telegram API errors that aren't handled.

    :param update: The Telegram update.
    :param context: The Telegram context.
    """
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error("Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    # Split the traceback into smaller parts
    tb_parts = [tb_string[i: i + 4096] for i in range(0, len(tb_string), 4096)]

    # Build the message with some markup and additional information about what happened.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    base_message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}"
        "</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
    )

    # Send base message
    await context.bot.send_message(
        chat_id=keyring_get('DevId'), text=base_message, parse_mode=ParseMode.HTML
    )

    # Send each part of the traceback as a separate message
    for part in tb_parts:
        await context.bot.send_message(
            chat_id=keyring_get('DevId'), text=f"<pre>{html.escape(part)}</pre>", parse_mode=ParseMode.HTML
        )


async def start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    The callback called when the bot receive the classical start command on a new conversation.
    It calls db_get_chat from dbjson file to read the chat or initialize it
    """
    welcome_text = ("Send me a photo or video with a FFmpeg command as caption and I will execute it on the sent file!\n"
                    "Please don't insert arguments after the output file name. Only one output is currently supported.\n"
                    "Example usage: -vf scale=1280x720 resized.jpg\n"
                    "The input file name is automatically retrieved from the file.")
    await update.message.reply_text(welcome_text, ParseMode.MARKDOWN)


async def multimedia_file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # retrieve message
    message = update.effective_message

    # check for caption
    caption = message.caption
    if not caption:
        await update.message.reply_text("Please send again the file with a FFmpeg command as file caption")
        return

    if 'ffmpeg' in caption.lower():
        await update.message.reply_text("Please do not insert ffmpeg in the caption.\n"
                                        "Example usage: -vf scale=1280x720 resized.jpg")

    # sanity checks on the file
    file_size = message.document.file_size
    if file_size == 0:
        await update.message.reply_text("The size of the file can't be 0")
        return

    file = await message.effective_attachment.get_file()
    input_file_name = message.document.file_name
    input_file_path = TEMP_DOWNLOAD_PATH + input_file_name

    file_path = await file.download_to_drive(TEMP_DOWNLOAD_PATH + message.document.file_name)
    logger.info(f"File {input_file_name} temporarily saved in the filepath: {file_path}")
    logger.info(f"FFmpeg command: {caption}")

    # split the text into a list representing the command
    command, output_file = parse_ffmpeg_command(caption, input_file_path)

    # Create subprocess, redirect the standard output and error to subprocess.PIPE
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    # Wait for the subprocess to finish, and get the output and error
    stdout, stderr = await process.communicate()

    # Convert bytes to string
    output = stderr.decode()

    await update.effective_message.reply_text(f"Command executed!\nOutput:\n\n\n{output}")
    logger.info(f"FFmpegg output: {output}")

    # sending back the processed photo
    await update.effective_message.reply_document(output_file)

    # deleting files
    os.remove(input_file_path)
    os.remove(output_file)


async def photos_and_videos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Please send videos or photos as file (no compression)")


async def other_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info(f"Mime type: {update.message.document.mime_type}")
    await update.effective_message.reply_text("Please in order to use this BOT, send a non compressed file with a "
                                              "caption as FFmpeg command.")


def main() -> None:
    # Initialize the keyring
    if not keyring_initialize():
        exit(0xFF)

    # Initialize Application
    application = Application.builder().token(keyring_get('Telegram')).concurrent_updates(True).build()

    # Assign an error handler
    application.add_error_handler(error_handler)

    # Configure the commands dispatcher
    application.add_handler(CommandHandler('start', start_callback))
    # Handles the interested files
    application.add_handler(MessageHandler(filters.Document.IMAGE | filters.Document.VIDEO, multimedia_file_callback))
    # Handles the normal photos and videos
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, photos_and_videos_callback))
    # Handles all the other types of messages
    application.add_handler(MessageHandler(filters.ALL, other_messages_handler))

    # Start the bot polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
