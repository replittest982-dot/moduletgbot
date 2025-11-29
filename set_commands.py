from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from config import ADMIN_ID # ✅ ИСПРАВЛЕНИЕ 11: ADMIN_ID импортируется

user_commands = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="auth", description="Вход в аккаунт"),
]

admin_commands = [
    BotCommand(command="create_promo", description="Создать промокод"),
]

# ✅ ИСПРАВЛЕНИЕ 40: Функция должна быть асинхронной
async def set_my_commands(bot: Bot, admin_id: int):
    # Установка команд по умолчанию для всех пользователей
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    
    # ✅ ИСПРАВЛЕНИЕ 39: Установка команд администратора только если ADMIN_ID установлен
    if admin_id and admin_id != 0:
        await bot.set_my_commands(user_commands + admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
