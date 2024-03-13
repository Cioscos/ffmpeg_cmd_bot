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
    filters,
    ConversationHandler,
    PicklePersistence
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%y-%m-%d %H:%M:%S',
    filename='ffmpeg_cmd_bot.log',
    filemode='w'
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

TEMP_DOWNLOAD_PATH = './temp_download_path/'

# Key to access to the stored file names in the context
MEDIAGROUP_FILE_NAMES_KEY = 'mediagroup_file_names'
# Key to access to the pre-input parts
PRE_INPUT_PARTS_KEY = 'pre_input'
# Key to access to the post-input parts
POST_INPUT_PARTS_KEY = 'post_input'
# Key to access to the command
COMMAND_KEY = 'command'
# Key to access to the output path
OUTPUT_PATH_KEY = 'output_path'

# states definitions for top-level conv handler
DOCUMENT_SENDING, COMMAND_WAITING, PRE_INPUT_STATE, POST_INPUT_STATE = map(chr, range(4))


def parse_ffmpeg_command(pre_input_parts: List[str], post_input_parts: List[str], input_file_names: List[str]) -> tuple[list[str], Optional[str]]:
    """
    Constructs an FFmpeg command string from pre-input parts, input file names, and post-input parts.

    Args:
        pre_input_parts (List[str]): FFmpeg options to place before the input files.
        post_input_parts (List[str]): FFmpeg options to place after the input files.
        input_file_names (List[str]): The input file names.

    Returns:
        A tuple containing the constructed FFmpeg command parts and the output file name.
    """
    # Initialize effective_command_parts with 'ffmpeg'
    effective_command_parts = ['ffmpeg']

    # Add pre-input parts (removing double quotes)
    effective_command_parts.extend([part.replace('"', '') for part in pre_input_parts])

    # Extend the list with '-i' followed by the input file names (removing double quotes from file names)
    for input_file_name in input_file_names:
        effective_command_parts.extend(['-i', input_file_name.replace('"', '')])

    # Add post-input parts (removing double quotes)
    effective_command_parts.extend([part.replace('"', '') for part in post_input_parts])

    output_file = None

    # The output file is typically the last argument in the command,
    # but ensure it is not a parameter or an option.
    # Basic check: not starting with '-' and contains a dot (.)
    if effective_command_parts[-1] and not effective_command_parts[-1].startswith('-') and '.' in effective_command_parts[-1]:
        output_file = TEMP_DOWNLOAD_PATH + effective_command_parts[-1]  # Ensure TEMP_DOWNLOAD_PATH is defined elsewhere
        effective_command_parts[-1] = output_file

    return effective_command_parts, output_file


def delete_temp_files(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Deletes temporary files from the file system based on lists stored in the context.

    Args:
        context (ContextTypes.DEFAULT_TYPE): The context object containing user data.
    """
    # Retrieve file lists from context, defaulting to empty lists if not found
    media_group_file_names = context.user_data.get(MEDIAGROUP_FILE_NAMES_KEY, [])
    output_path = context.user_data.get(OUTPUT_PATH_KEY, [])

    # Concatenate the lists to get a combined list of files to delete
    all_files_to_delete = media_group_file_names.append(output_path)

    # Deleting files from the file system
    for input_file in all_files_to_delete:
        if os.path.exists(input_file):
            os.remove(input_file)


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
    welcome_text = ("Use the /init command to start a conversation with the bot!\n"
                    "Please don't insert arguments after the output file name. Only one output is currently supported.\n"
                    "Use the /init command to start the command conversation.\n"
                    "The input file/s name is automatically retrieved from the file.")
    await update.message.reply_text(welcome_text, ParseMode.MARKDOWN)


async def init_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.effective_message.reply_text("Send one image or video as document or in a normal way (compressed).\n\n"
                                              "Take care that telegram's bots accept files up to 20MB only")

    # initialize the user_data dictionary
    context.user_data.setdefault(MEDIAGROUP_FILE_NAMES_KEY, [])

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
    context.user_data[MEDIAGROUP_FILE_NAMES_KEY].append(input_file_name)

    # download the file
    file_path = await file.download_to_drive(input_file_name)
    logger.info(f"File {input_file_name} temporarily saved in the filepath: {file_path}")

    if message.media_group_id is None:
        await update.effective_message.reply_text('Send me other files or send the /pre or /post command')
    else:
        await update.effective_message.reply_text('File from media group received.\n'
                                                  'Send other files or use /pre or /post command.')

    return COMMAND_WAITING


async def command_waiting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_plural = 'file' if len(context.user_data[MEDIAGROUP_FILE_NAMES_KEY]) == 1 else 'files'
    if update.effective_message.text == '/pre':
        await update.effective_message.reply_text(f"Send me the ffmpeg pre-input command part to apply to the {file_plural}.\n"
                                                  f"It is the command part before the input files.")
        return PRE_INPUT_STATE
    elif update.effective_message.text == '/post':
        await update.effective_message.reply_text(f"Send me the ffmpeg post-input command part to apply to the {file_plural}.\n"
                                                  f"It is the command part after the input files.")
        return POST_INPUT_STATE
    else:
        await update.effective_message.reply_text('Command deleted, use /pre or /post command again or /stop .')
        context.user_data.setdefault(PRE_INPUT_PARTS_KEY, None)
        context.user_data.setdefault(POST_INPUT_PARTS_KEY, None)
        return COMMAND_WAITING


async def pre_input_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text
    parts = text.split()
    file_plural = 'file' if len(context.user_data[MEDIAGROUP_FILE_NAMES_KEY]) == 1 else 'files'

    context.user_data[PRE_INPUT_PARTS_KEY] = parts

    await update.effective_message.reply_text(f'Now send the post command (the part of the command after the input {file_plural}.')

    return POST_INPUT_STATE


async def post_input_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text
    parts = text.split()

    context.user_data[POST_INPUT_PARTS_KEY] = parts
    file_plural = 'file' if len(context.user_data[MEDIAGROUP_FILE_NAMES_KEY]) == 1 else 'files'

    # reconstruct the command
    effective_command_parts, output_file = parse_ffmpeg_command(
        context.user_data.get(PRE_INPUT_PARTS_KEY, []),
        context.user_data[POST_INPUT_PARTS_KEY],
        context.user_data[MEDIAGROUP_FILE_NAMES_KEY]
    )

    context.user_data[COMMAND_KEY] = effective_command_parts
    context.user_data[OUTPUT_PATH_KEY] = output_file

    await update.effective_message.reply_text(f"This is the command that will be applied to the {file_plural}:\n"
                                              f"`{' '.join(effective_command_parts)}`", ParseMode.MARKDOWN)

    await update.effective_message.reply_text('Send:\n- /process command to generate the output.\n'
                                              '- /reset to delete the command inserted.\n'
                                              '- /stop to close the conversation.')

    return POST_INPUT_STATE


async def command_processing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Create subprocess, redirect the standard output and error to subprocess.PIPE
    process = await asyncio.create_subprocess_exec(
        *context.user_data[COMMAND_KEY],
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
    logger.info(f"FFmpeg output: {stderr.decode()}")

    output_file = context.user_data[OUTPUT_PATH_KEY]

    # sending back the processed photo if exists
    if output_file and os.path.exists(output_file) and os.path.getsize(output_file) != 0:
        if (os.path.getsize(output_file) / (1024 * 1024)) > 50:
            await update.effective_message.reply_text('The output file is bigger than 50MB so it can\'t be sent from a bot.')
        else:
            with open(output_file, 'rb') as file:
                await update.effective_message.reply_document(document=file)
            os.remove(output_file)  # Clean up after sending
    else:
        await update.effective_message.reply_text('There is a problem with the output file, try to change files or command.')

    # Deleting files from the file system
    delete_temp_files(context)

    # Wiping user_data
    context.user_data.clear()

    # No need to delete the BytesIO object, it will be cleaned up by Python's garbage collector
    return ConversationHandler.END


async def stop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.debug('Stop callback called!')

    # Deleting files from the file system
    delete_temp_files(context)
    # Wiping user_data
    context.user_data.clear()

    await update.effective_message.reply_text("You have stopped the /init command!")

    return ConversationHandler.END


async def other_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Please in order to use this BOT, use the /init command.")


def main() -> None:
    # Initialize the keyring
    if not keyring_initialize():
        exit(0xFF)

    # Initialize the Pickle database
    persistence = PicklePersistence(filepath='DB.pkl')

    # Initialize Application
    application = Application.builder().token(keyring_get('Telegram')).persistence(persistence).build()

    # Assign an error handler
    application.add_error_handler(error_handler)

    # Configure the commands dispatcher
    application.add_handler(CommandHandler('start', start_callback))

    ffmpeg_command_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('init', init_callback)],
        name='ffmpeg_command_conv_handler_v1',
        states={
            DOCUMENT_SENDING: [
                MessageHandler(filters.Document.IMAGE | filters.Document.VIDEO | filters.PHOTO | filters.VIDEO, document_sending_callback),
                CommandHandler('stop', stop_callback)
            ],
            COMMAND_WAITING: [
                MessageHandler(filters.Document.IMAGE | filters.Document.VIDEO | filters.PHOTO | filters.VIDEO, document_sending_callback),
                CommandHandler('pre', command_waiting_callback),
                CommandHandler('post', command_waiting_callback),
                CommandHandler('reset', command_waiting_callback),
                CommandHandler('stop', stop_callback)
            ],
            PRE_INPUT_STATE: [
                MessageHandler(filters.TEXT, pre_input_command_callback),
                CommandHandler('stop', stop_callback)
            ],
            POST_INPUT_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, post_input_command_callback),
                CommandHandler('stop', stop_callback),
                CommandHandler('reset', command_waiting_callback),
                CommandHandler('process', command_processing_callback)
            ]
        },
        fallbacks=[CommandHandler('stop', stop_callback)]
    )
    application.add_handler(ffmpeg_command_conv_handler)
    # Handles all the other types of messages
    application.add_handler(MessageHandler(filters.TEXT, other_messages_handler))

    # Start the bot polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
