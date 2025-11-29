from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from .config import ADMIN_ID

async def set_default_commands(bot: Bot):
    # Команды для всех пользователей (в ЛС бота)
    commands = [
        BotCommand(command="start", description="Главное меню"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    
    # Дополнительные команды для админа
    admin_commands = [
        BotCommand(command="create_promo", description="Создать промокод (Админ)"),
        BotCommand(command="stats", description="Показать статистику (Админ)"),
    ]
    await bot.set_my_commands(commands + admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

# Команды DROP-системы (для использования в чате, не регистрируются явно)
# /numb, /num, /vstal, /error, /slet, /povt, /zm, /report_last, /report
