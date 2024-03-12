# FFmpeg Telegram Bot

This Telegram bot allows users to process multimedia files using FFmpeg directly through Telegram. Users can send images or videos to the bot, specify FFmpeg command parameters, and receive the processed files. The bot supports various FFmpeg commands, making it a flexible tool for multimedia file manipulation.

## Features

- Process images and videos using FFmpeg commands.
- Support for pre-input and post-input FFmpeg command parts.
- Error handling with detailed traceback information.
- Temporary file handling for security and performance.

## Prerequisites

Before you can use this bot, you need to have:

- Python 3.8 or newer.
- FFmpeg installed on the server hosting the bot.
- A Telegram bot token (obtained through BotFather on Telegram).
- Python packages: `python-telegram-bot`, `asyncio`, and others listed in `requirements.txt`.

## Installation

1. Clone the repository:
```
git clone [<repository-url>](https://github.com/Cioscos/ffmpeg_cmd_bot)https://github.com/Cioscos/ffmpeg_cmd_bot
```
2. Install the required Python packages:
```
pip install -r requirements.txt
```
3. Set up your environment variables or modify the `env_manager.py` to include your Telegram Bot API key and other necessary configurations.

## Usage

1. Start the bot by running the `main.py` script:
```
python main.py
```
2. Interact with your bot on Telegram:
- Send `/start` to receive a welcome message and instructions.
- Use `/init` to start processing commands.
- Follow the bot's instructions to send files and specify FFmpeg commands.

## Commands

- `/start`: Show a welcome message and instructions.
- `/init`: Initialize the file processing session.
- `/pre`: Set the pre-input part of the FFmpeg command.
- `/post`: Set the post-input part of the FFmpeg command.
- `/process`: Process the provided files with the specified FFmpeg command.
- `/reset`: Reset the current session and clear any set commands.
- `/stop`: Stop the current session and clear all temporary data.

## Error Handling

The bot includes comprehensive error handling, which logs errors and sends detailed information to a specified developer Telegram ID. Ensure to set this ID correctly in your configuration.

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues for bugs, feature requests, or other suggestions.
