import logging
import asyncio
import os
import qrcode
from io import BytesIO
import textwrap
from dateutil import parser

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# УБРАНЫ ТОЧКИ ПЕРЕД ИМЕНАМИ МОДУЛЕЙ
from config import ADMIN_ID, SUPPORT_BOT_USERNAME, TARGET_CHANNEL_URL, TEMP_DIR, QR_TIMEOUT, MOSCOW_TZ
from utils import TelethonAuth, AdminState, UserState, DropUserState, GlobalStorage, check_valid_phone, format_drop_report
from db import AsyncDatabase
from telethon_manager import TelethonManager

logger = logging.getLogger(__name__)

user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# --- MENUS ---
def get_main_menu_kb(is_subscribed: bool, is_telethon_active: bool, is_worker_running: bool, has_progress: bool, is_admin: bool) -> InlineKeyboardMarkup:
    kb = []
    status_text = "🟢 Активна" if is_subscribed else "🔴 Не активна"
    kb.append([
        InlineKeyboardButton(text=f"Подписка: {status_text}", callback_data="info_sub"),
        InlineKeyboardButton(text="Справка", callback_data="info_help"),
        InlineKeyboardButton(text="Задать вопрос", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")
    ])
    
    # Логика для пользователей с подпиской (включая админа)
    if is_subscribed or is_admin:
        
        # Кнопка промокода доступна всегда, если есть подписка
        if not is_telethon_active:
            # Если нет активного Telethon аккаунта: показываем кнопки входа
            kb.append([
                InlineKeyboardButton(text="📱 Вход по QR-коду", callback_data="auth_qr"),
                InlineKeyboardButton(text="🔑 Вход по Номеру", callback_data="auth_phone")
            ])
            # Кнопка промокода
            kb.append([InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="user_promo")])
        else:
            # Если есть активный Telethon аккаунт: показываем Worker и Выход
            worker_row = []
            if is_worker_running:
                worker_row.append(InlineKeyboardButton(text="Worker Активен", callback_data="info_worker"))
                worker_row.append(InlineKeyboardButton(text="🛑 Остановить", callback_data="worker_stop"))
                if has_progress:
                    worker_row.append(InlineKeyboardButton(text="Прогресс", callback_data="worker_status"))
            else:
                worker_row.append(InlineKeyboardButton(text="Запустить Worker", callback_data="worker_start"))
                worker_row.append(InlineKeyboardButton(text="Worker Остановлен", callback_data="info_worker"))
            
            kb.append(worker_row)
            # Кнопка промокода и Выход
            kb.append([InlineKeyboardButton(text="🎁 Промокод", callback_data="user_promo"), InlineKeyboardButton(text="❌ Выход", callback_data="auth_logout")])
        
        # Админ-Панель только для админа
        if is_admin: kb.append([InlineKeyboardButton(text="👑 Админ-Панель", callback_data="admin_panel")])
    
    else:
        # Если нет подписки и это не админ: показываем только заглушку
        kb.append([InlineKeyboardButton(text="Доступ к боту закрыт (Подписка)", callback_data="info_sub")])

    return InlineKeyboardMarkup(inline_keyboard=kb)

async def send_start_menu(chat_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, is_initial_check: bool = False):
    uid = chat_id
    is_subscribed_bool, sub_status_text = await db.get_subscription_status(uid, ADMIN_ID)
    user_data = await db.get_user(uid)
    is_telethon_active = user_data['telethon_active'] if user_data else False
    is_worker_running = uid in tm.store.active_workers
    has_progress = uid in tm.store.process_progress
    is_admin = uid == ADMIN_ID
    
    # Проверка подписки на канал (если не админ)
    if is_initial_check and not is_subscribed_bool and not is_admin:
        try:
            member = await bot.get_chat_member(TARGET_CHANNEL_URL, uid)
            if member.status not in ['member', 'creator', 'administrator']:
                return await bot.send_message(chat_id, f"⚠️ Подпишитесь на: **{TARGET_CHANNEL_URL}**", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{TARGET_CHANNEL_URL.lstrip('@')}")], [InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")]]), parse_mode='Markdown')
        except Exception: 
            # Если канал приватный или ошибка API, просто продолжаем
            pass

    await bot.send_message(chat_id, f"🤖 Привет!\n**Подписка:** {sub_status_text}", parse_mode='Markdown', reply_markup=get_main_menu_kb(is_subscribed_bool, is_telethon_active, is_worker_running, has_progress, is_admin))

@user_router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await send_start_menu(message.from_user.id, bot, db, tm, is_initial_check=True)

@user_router.callback_query(F.data == "check_subscription")
async def cb_check_sub(callback: CallbackQuery, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await callback.answer()
    # Отправляем меню заново с проверкой подписки
    await send_start_menu(callback.from_user.id, bot, db, tm, is_initial_check=True)
    await callback.message.delete()

# --- Auth ---
@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await state.set_state(TelethonAuth.PHONE)
    await callback.message.edit_text("📞 Введите номер телефона (`+7...`):", parse_mode='Markdown')

@user_router.message(StateFilter(TelethonAuth.PHONE))
async def auth_get_phone(message: Message, state: FSMContext, tm: TelethonManager, **kwargs):
    phone = check_valid_phone(message.text)
    if not phone: return await message.answer("❌ Неверный формат.")
    
    # Проверяем, существует ли уже активный worker для этого пользователя
    if message.from_user.id in tm.store.active_workers:
        return await message.answer("⚠️ Уже есть активная сессия. Сначала выполните выход.")

    msg = await tm.send_code(message.from_user.id, phone)
    if "❌" in msg: return await message.answer(msg)
    
    await state.set_state(TelethonAuth.CODE)
    await message.answer(f"{msg} Введите код:")

@user_router.message(StateFilter(TelethonAuth.CODE))
async def auth_get_code(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg, _ = await tm.sign_in(message.from_user.id, message.text.strip())
    
    if success:
        await state.clear()
        await message.answer(msg)
        await send_start_menu(message.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.PASSWORD)
        await message.answer(msg)
    else:
        await message.answer(msg)

@user_router.message(StateFilter(TelethonAuth.PASSWORD))
async def auth_get_pass(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr(callback: CallbackQuery, state: FSMContext, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await callback.answer("Генерация...")
    
    if callback.from_user.id in tm.store.active_workers:
        return await callback.message.answer("⚠️ Уже есть активная сессия. Сначала выполните выход.")
        
    try:
        # Ожидаем только URL (именно так работает start_qr_login в Telethon без image)
        url = await tm.start_qr_login(callback.from_user.id) 
    except Exception as e: 
        logger.error(f"QR Login start error: {e}")
        return await callback.message.answer(f"❌ Ошибка: {e}")
    
    bio = BytesIO()
    try:
        # Генерируем QR-код с помощью библиотеки qrcode, используя полученный URL
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(bio, 'PNG')
        bio.seek(0)
    except Exception as e:
        logger.error(f"Error generating QR code image: {e}")
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        return await callback.message.answer("❌ Ошибка при создании QR-кода. Попробуйте войти по номеру.")
    
    await callback.message.answer_photo(bio, caption=f"Отсканируйте код. Действует {QR_TIMEOUT}с.")
    await state.set_state(TelethonAuth.WAITING_FOR_QR_LOGIN)
    
    data = tm.store.temp_data.get(callback.from_user.id)
    if not data:
        await state.clear()
        return await callback.message.answer("❌ Сессия QR-авторизации утеряна. Попробуйте снова.")

    # Ожидаем завершения QR-авторизации с таймаутом
    try:
        success, msg = await asyncio.wait_for(
            tm.check_qr_login(callback.from_user.id, data['qr_login_data'], data['client']), 
            timeout=QR_TIMEOUT + 5 # 5 секунд запас
        )
    except asyncio.TimeoutError:
        await state.clear()
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        return await callback.message.answer("❌ Время ожидания QR-кода истекло.")
    except Exception as e:
        await state.clear()
        await tm.stop_worker(callback.from_user.id, delete_session=True)
        return await callback.message.answer(f"❌ Произошла ошибка при проверке QR: {e}")


    if success:
        await state.clear()
        await callback.message.answer(msg)
        await send_start_menu(callback.from_user.id, bot, db, tm)
    elif "⚠️" in msg:
        await state.set_state(TelethonAuth.QR_PASSWORD)
        await callback.message.answer(msg)
    else:
        await state.clear()
        await callback.message.answer(msg)

@user_router.message(StateFilter(TelethonAuth.QR_PASSWORD))
async def auth_get_qr_pass(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    # Используем ту же функцию sign_in_password, т.к. логика одинакова
    success, msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    await message.answer(msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)


# --- Worker ---
@user_router.callback_query(F.data == "worker_start")
async def cb_work_start(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    if await tm.start_client_task(callback.from_user.id): 
        await callback.answer("Запущен.")
    else: 
        await callback.answer("Ошибка запуска. Попробуйте войти заново.", show_alert=True)
        # Если не смогли запустить, сбрасываем состояние телетона в БД
        await db.update_user(callback.from_user.id, telethon_active=0)
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "worker_stop")
async def cb_work_stop(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id)
    await callback.answer("Остановлен.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "worker_status")
async def cb_work_status(callback: CallbackQuery, tm: TelethonManager, **kwargs):
    progress = tm.store.process_progress.get(callback.from_user.id)
    if progress:
        if progress['type'] == 'flood': 
            status_text = f"Флуд: {progress['sent']}/{progress['total']}"
        elif progress['type'] == 'checkgroup': 
            status_text = f"Скан `{progress['peer_name']}`: {progress['processed']} сообщ, {progress['total_users']} юзеров"
        else:
            status_text = "Неизвестный процесс."
    else:
        status_text = "Нет активных задач."
        
    await callback.answer(f"Статус: {status_text}", show_alert=True)


@user_router.callback_query(F.data == "auth_logout")
async def cb_logout(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, state: FSMContext, **kwargs):
    await state.clear()
    await tm.stop_worker(callback.from_user.id, delete_session=True)
    await callback.answer("Сессия удалена.")
    await send_start_menu(callback.from_user.id, bot, db, tm)


# --- Info ---
@user_router.callback_query(F.data.startswith("info_"))
async def cb_info(callback: CallbackQuery, db: AsyncDatabase, tm: TelethonManager, bot: Bot, **kwargs):
    key = callback.data.split('_')[1]
    text = "Информация:\n"
    
    if key == 'sub':
        _, status_text = await db.get_subscription_status(callback.from_user.id, ADMIN_ID)
        text = f"**Статус подписки:** {status_text}\n"
        text += "Для получения доступа приобретите подписку."
        # Короткий ответ: используем show_alert
        await callback.answer(text, show_alert=True)
        return
        
    elif key == 'worker':
        if callback.from_user.id in tm.store.active_workers:
            text = "Worker активен. Он слушает ваши исходящие сообщения в Телеграме, начиная с символа `.` (например, `.флуд`)."
        else:
            text = "Worker остановлен. Чтобы начать использовать команды, нажмите 'Запустить Worker'."
        # Короткий ответ: используем show_alert
        await callback.answer(text, show_alert=True)
        return
        
    elif key == 'help':
        # Длинный ответ: отправляем как обычное сообщение
        text = textwrap.dedent("""
        **Доступные команды в Телеграме:**
        
        * `.флуд <кол-во> <текст> <задержка> [цель]`
            - `кол-во`: количество сообщений (0 для бесконечности).
            - `задержка`: в секундах (например, 0.1).
            - `цель`: `@username`, `ID` или `название чата` (опционально, по умолчанию текущий чат).
        * `.стопфлуд` - останавливает все активные флуды.
        * `.лс` - отправляет сообщение нескольким пользователям. Формат:
            `.лс Привет!`
            `@user1`
            `@user2`
        * `.чекгруппу [цель]` - сканирует группу на пользователей (долгий процесс).
        * `.статус` - показывает текущий прогресс задач.
        
        **Команды для дропов:**
        * `.пкстарт <НазваниеПК>` - привязывает ПК к текущему чату/топику.
        """)
        await callback.message.answer(text, parse_mode='Markdown')
        await callback.answer() # Закрываем уведомление

    else:
        await callback.answer("Неизвестная информация.")


# --- Promo ---
@user_router.callback_query(F.data == "user_promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.set_state(UserState.WAITING_FOR_PROMO_CODE)
    await callback.message.edit_text("Введи промокод:")

@user_router.message(StateFilter(UserState.WAITING_FOR_PROMO_CODE))
async def promo_proc(message: Message, state: FSMContext, db: AsyncDatabase, bot: Bot, tm: TelethonManager, **kwargs):
    success, msg = await db.apply_promo_code(message.from_user.id, message.text.strip())
    await message.answer(msg, parse_mode='Markdown')
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)
    else:
        # Если неудача, остаемся в состоянии для повторного ввода
        await message.answer("Попробуйте другой промокод или нажмите /start для выхода в меню.")

# --- Admin ---
@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery, **kwargs):
    if callback.from_user.id != ADMIN_ID: return
    # 💡 ИСПРАВЛЕНИЕ: Убираем излишнюю разметку Markdown для текста инструкций
    text = (
        "👑 **Админ-Панель**\n\n"
        "**Создание промокода:** /create_promo\n"
        "Формат: `КОД ДНИ МАКС_ЮЗЕРОВ`\n"
        "Пример: `/create_promo TEST 30 10`"
    )
    await callback.message.answer(text, parse_mode='Markdown')
    await callback.answer()

@admin_router.message(Command("create_promo"))
async def cmd_mk_promo(message: Message, state: FSMContext, **kwargs):
    if message.from_user.id != ADMIN_ID: return
    
    parts = message.text.split()
    if len(parts) != 4: 
        return await message.answer("❌ Неверный формат. Ожидался: `/create_promo КОД ДНИ МАКС_ЮЗЕРОВ` (пример: `/create_promo TEST 30 10`)", parse_mode='Markdown')
    
    try:
        code = parts[1].strip().upper()
        days = int(parts[2])
        max_uses = int(parts[3])
    except ValueError:
        return await message.answer("❌ Дни и Макс_юзеров должны быть числами.")
    except Exception:
        return await message.answer("❌ Неверный формат команды.")

    db = kwargs.get('db') # Получаем DB из зависимостей
    if not db: return await message.answer("❌ Ошибка базы данных.")

    if await db.create_promo_code(code, days, max_uses):
        await message.answer(f"✅ Промокод **{code}** создан: {days} дней, {max_uses} использований.", parse_mode='Markdown')
    else: 
        await message.answer(f"❌ Ошибка: промокод **{code}** уже существует?", parse_mode='Markdown')


# --- Drop System ---
@drop_router.message(Command("numb"))
async def d_numb(message: Message, db: AsyncDatabase, store: GlobalStorage, **kwargs):
    # Определяем имя ПК через привязку к чату/топику
    pc = store.drop_mapping.get((message.chat.id, message.message_thread_id or 0))
    if not pc: return await message.answer("❌ ПК не привязан к этому чату/топику. Используйте `.пкстарт <НазваниеПК>` из Telethon-аккаунта.")
    
    # Создаем новую сессию
    drop_id = message.from_user.id
    phone_placeholder = await db.create_drop_session(pc, drop_id)
    
    await message.answer(f"✅ **{pc}**: Жду номер. Введите команду `/num <номер>` (например: `/num +79001234567`). \n\n**ID сессии:** `{phone_placeholder}`", parse_mode='Markdown')

@drop_router.message(Command("num"))
async def d_num(message: Message, db: AsyncDatabase, **kwargs):
    args = message.text.split()
    if len(args) < 2: return await message.answer("❌ Неверный формат. Используйте `/num <номер>`.")
    
    new_phone = check_valid_phone(args[1])
    if not new_phone: return await message.answer("❌ Неверный формат номера телефона.")
    
    sess = await db.get_latest_drop_session(message.from_user.id)
    if not sess: return await message.answer("❌ Нет активной сессии для обновления. Начните с `/numb`.")
    
    success, msg = await db.update_drop_session(sess['phone'], new_phone=new_phone, status="дайте номер")
    
    if success:
        await message.answer(f"✅ Номер **{new_phone}** привязан к **{sess['pc_name']}**. Статус: `дайте номер`", parse_mode='Markdown')
    else:
        await message.answer(f"❌ Ошибка: {msg}")

async def d_status(msg: Message, st: str, db: AsyncDatabase):
    sess = await db.get_latest_drop_session(msg.from_user.id)
    if not sess: return await msg.answer("❌ Нет активной сессии для обновления. Начните с `/numb`.")
    
    success, _ = await db.update_drop_session(sess['phone'], status=st)
    if success:
        # Получаем обновленные данные, чтобы показать актуальное "Время в работе"
        updated_sess = await db.get_latest_drop_session(msg.from_user.id)
        # Использование format_drop_report для создания отчета
        report_msg = format_drop_report(dict(updated_sess))
        
        await msg.answer(f"✅ **{sess['pc_name']}**: Статус `{st.upper()}` обновлен.", parse_mode='Markdown')
    else:
        await msg.answer("❌ Ошибка обновления статуса.")

@drop_router.message(Command("vstal"))
async def d_vstal(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "в работе", db)
@drop_router.message(Command("error"))
async def d_error(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "error", db)
@drop_router.message(Command("slet"))
async def d_slet(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "slet", db)
@drop_router.message(Command("povt"))
async def d_povt(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "повтор", db)
@drop_router.message(Command("zamena"))
async def d_zamena(m: Message, db: AsyncDatabase, **kwargs): await d_status(m, "замена", db) 

@drop_router.message(Command("report_last"))
async def d_rep(message: Message, db: AsyncDatabase, **kwargs):
    sess = await db.get_latest_drop_session(message.from_user.id)
    if sess: 
        # Использование format_drop_report для создания отчета
        await message.answer(format_drop_report(dict(sess)), parse_mode='Markdown')
    else:
        await message.answer("❌ Нет активной сессии для отчета. Начните с `/numb`.")
