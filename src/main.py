import logging
import os
import io
import traceback
import html
import json
import asyncio
from typing import Optional, List
from env_manager import keyring_get, keyring_initialize
from telegram import Update, Document, Video
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters, ConversationHandler
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

# Key to access to the stored file names in the context
MEDIAGROUP_FILE_NAMES = 'mediagroup_file_names'

# states definitions for top-level conv handler
DOCUMENT_SENDING, COMMAND_WAITING, GENERATING_RESULT = map(chr, range(3))


def parse_ffmpeg_command(command_string: str, input_file_names: List[str]) -> tuple[list[str], Optional[str]]:
    """
    Parses an FFmpeg command string to find the input and output file names.

    Args:
        command_string (str): The FFmpeg command as a single string.
        input_file_names (List[str]): The input file names

    Returns:
        A tuple containing the command parts converted from string and the output file name.
    """
    # Split the command string into parts
    parts = command_string.split()

    # Initialize effective_command_parts with 'ffmpeg'
    effective_command_parts = ['ffmpeg']

    # Extend the list with '-i' followed by the input file names
    for input_file_name in input_file_names:
        effective_command_parts.extend(['-i', input_file_name.replace('"', '')])

    # Extend effective_command_parts with the additional command parts
    effective_command_parts.extend([part.replace('"', '') for part in parts])

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
    welcome_text = ("Use the /init command to start a conversation with the bot\n"
                    "Please don't insert arguments after the output file name. Only one output is currently supported.\n"
                    "Example usage: -vf scale=1280x720 resized.jpg\n"
                    "The input file name is automatically retrieved from the file.")
    await update.message.reply_text(welcome_text, ParseMode.MARKDOWN)


async def init_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Send one image or video as document or in a normal way (compressed).\n\n"
                                              "Take care that telegram's bots accept files up to 20MB only")

    # initialize the user_data dictionary
    context.user_data.setdefault(MEDIAGROUP_FILE_NAMES, [])

    return DOCUMENT_SENDING


async def document_sending_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # retrieve message
    message = update.effective_message

    # sanity checks on the file
    attachment = message.effective_attachment

    # check if the attachment is a photo or a document
    # if it's a photo
    if isinstance(attachment, tuple):
        attachment = attachment[-1]

    file_size = attachment.file_size
    if file_size == 0:
        await update.message.reply_text("The size of the file can't be 0")
        return DOCUMENT_SENDING

    file = await attachment.get_file()
    input_file_name = TEMP_DOWNLOAD_PATH
    if isinstance(attachment, Document):
        input_file_name += attachment.file_name
    elif isinstance(attachment, Video):
        input_file_name += f"{attachment.file_unique_id}.{attachment.mime_type.split('/')[1]}"
    else:
        input_file_name += f"{attachment.file_unique_id}.jpg"

    # add the file name to the MEDIAGROUP_FILE_NAMES list in user_data dictionary
    context.user_data[MEDIAGROUP_FILE_NAMES].append(input_file_name)

    # download the file
    file_path = await file.download_to_drive(input_file_name)
    logger.info(f"File {input_file_name} temporarily saved in the filepath: {file_path}")

    if message.media_group_id is None:
        await update.effective_message.reply_text('Send me other files or send the /cmd command')
    else:
        await update.effective_message.reply_text('File from media group received.\n'
                                                  'Send other files or use /cmd command.')

    return COMMAND_WAITING


async def command_waiting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text(f'Send me the ffmpeg command to apply to the {'file' if len(context.user_data[MEDIAGROUP_FILE_NAMES]) == 1 else 'files'}')

    return GENERATING_RESULT


async def command_processing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # retrieve the message
    message = update.effective_message
    text = message.text

    if 'ffmpeg' in text:
        await update.message.reply_text("Please do not insert ffmpeg in the command.\n"
                                        "Example usage: -vf scale=1280x720 resized.jpg")
        return GENERATING_RESULT

    # split the text into a list representing the command
    command, output_file = parse_ffmpeg_command(text, context.user_data[MEDIAGROUP_FILE_NAMES])
    # log parsed command
    logger.info(command)

    # Create subprocess, redirect the standard output and error to subprocess.PIPE
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    # Wait for the subprocess to finish, and get the output and error
    stdout, stderr = await process.communicate()

    # Convert stderr to a BytesIO object
    stderr_bytesio = io.BytesIO(stderr)

    # Send the output as a document directly from memory
    stderr_bytesio.name = "ffmpeg_output.txt"  # Telegram requires a filename to send as a document
    await update.message.reply_document(document=stderr_bytesio, caption="FFmpeg output")

    # Log the FFmpeg output
    logger.debug(f"FFmpeg output: {stderr.decode()}")

    # sending back the processed photo if exists
    if output_file and os.path.exists(output_file) and os.path.getsize(output_file) != 0:
        if (os.path.getsize(output_file) / (1024 * 1024)) > 50:
            await update.effective_message.reply_text('The output file is bigger than 50MB so it can\'t be sent from a bot')
            return ConversationHandler.END
        with open(output_file, 'rb') as file:
            await update.effective_message.reply_document(document=file)
        os.remove(output_file)  # Clean up after sending
    else:
        await update.effective_message.reply_text('There is a problem with the output file, try to change files or command.')
        return ConversationHandler.END

    # Deleting files from the file system
    for input_file in context.user_data.get(MEDIAGROUP_FILE_NAMES, []):
        if os.path.exists(input_file):
            os.remove(input_file)

    # Wiping MEDIAGROUP_FILE_NAMES list
    context.user_data[MEDIAGROUP_FILE_NAMES].clear()

    # No need to delete the BytesIO object, it will be cleaned up by Python's garbage collector
    return ConversationHandler.END


async def stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.debug('Stop callback called!')
    context.user_data[MEDIAGROUP_FILE_NAMES] = None

    await update.effective_message.reply_text("You have stopped the /init command!")

    return ConversationHandler.END


async def other_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Please in order to use this BOT, use the /init command")


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

    ffmpeg_command_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('init', init_callback)],
        states={
            DOCUMENT_SENDING: [
                MessageHandler(filters.Document.IMAGE | filters.Document.VIDEO | filters.PHOTO | filters.VIDEO, document_sending_callback),
                CommandHandler('stop', stop_callback)
            ],
            COMMAND_WAITING: [
                MessageHandler(filters.Document.IMAGE | filters.Document.VIDEO | filters.PHOTO | filters.VIDEO, document_sending_callback),
                CommandHandler('cmd', command_waiting_callback),
                CommandHandler('stop', stop_callback)
            ],
            GENERATING_RESULT: [
                MessageHandler(filters.TEXT, command_processing_callback),
                CommandHandler('stop', stop_callback)
            ]
        },
        fallbacks=[]
    )
    application.add_handler(ffmpeg_command_conv_handler)
    # Handles all the other types of messages
    application.add_handler(MessageHandler(filters.TEXT, other_messages_handler))

    # Start the bot polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
