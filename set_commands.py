from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from config import ADMIN_ID

async def set_default_commands(bot: Bot):
    await bot.set_my_commands([BotCommand(command="start", description="Меню")], scope=BotCommandScopeDefault())
    await bot.set_my_commands([
        BotCommand(command="create_promo", description="Создать промо"),
        BotCommand(command="stats", description="Статистика")
    ], scope=BotCommandScopeChat(chat_id=ADMIN_ID))
