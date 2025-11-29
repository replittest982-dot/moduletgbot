from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

async def set_default_commands(bot: Bot, admin_id: int):
    """
    Регистрирует команды для пользователей и администратора.
    """
    
    # 1. КОМАНДЫ ДЛЯ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ
    user_commands = [
        BotCommand(command="start", description="👋 Начало работы и статус"),
        BotCommand(command="login", description="🔑 Авторизация Telethon-аккаунта"),
        BotCommand(command="logout", description="🛑 Остановка Telethon-воркера"),
        BotCommand(command="promo", description="🎁 Активировать промокод"),
    ]
    
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    
    # 2. КОМАНДЫ ДЛЯ АДМИНА
    admin_commands = user_commands + [
        BotCommand(command="admin", description="👑 Админ-панель"),
        BotCommand(command="create_promo", description="🔑 Создать промокод"),
        # Здесь можно добавить другие команды, видимые только админу
    ]
    
    # Регистрируем команды только для ADMIN_ID
    if admin_id:
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))

    print("✅ Команды бота успешно зарегистрированы.")
