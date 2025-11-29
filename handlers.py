import logging
import asyncio
import os
import qrcode
from io import BytesIO
import textwrap
import shutil

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage 

from .config import ADMIN_ID, SUPPORT_BOT_USERNAME, TARGET_CHANNEL_URL, TEMP_DIR
from .utils import TelethonAuth, AdminState, UserState, DropUserState, GlobalStorage, check_valid_phone, format_drop_report
from .db import AsyncDatabase
from .telethon_manager import TelethonManager

logger = logging.getLogger(__name__)

# --- Роутеры ---
user_router = Router(name="user_router")
admin_router = Router(name="admin_router")
drop_router = Router(name="drop_router")

# =========================================================================
# I. МЕНЮ И СТАРТ
# =========================================================================

def get_main_menu_kb(is_subscribed: bool, is_telethon_active: bool, is_worker_running: bool, has_progress: bool, is_admin: bool) -> InlineKeyboardMarkup:
    # ... (логика клавиатуры, как в монолите) ...
    kb = []
    
    status_text = "🟢 Активна" if is_subscribed else "🔴 Не активна"
    links_row = [
        InlineKeyboardButton(text=f"Подписка: {status_text}", callback_data="info_sub"),
        InlineKeyboardButton(text="Справка", callback_data="info_help"),
        InlineKeyboardButton(text="Задать вопрос", url=f"https://t.me/{SUPPORT_BOT_USERNAME}"),
    ]
    kb.append(links_row)
    
    if not is_subscribed and not is_admin:
        kb.append([InlineKeyboardButton(text="Доступ к боту закрыт (Подписка)", callback_data="info_sub")])
    else:
        if not is_telethon_active:
            kb.append([
                InlineKeyboardButton(text="📱 Вход по QR-коду", callback_data="auth_qr"),
                InlineKeyboardButton(text="🔑 Вход по Номеру", callback_data="auth_phone")
            ])
            kb.append([InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="user_promo")])
        else:
            worker_row = []
            if is_worker_running:
                worker_row.append(InlineKeyboardButton(text="Worker Активен", callback_data="info_worker"))
                worker_row.append(InlineKeyboardButton(text="🛑 Остановить", callback_data="worker_stop"))
                if has_progress:
                    worker_row.append(InlineKeyboardButton(text="Прогресс задачи", callback_data="worker_status"))
            else:
                worker_row.append(InlineKeyboardButton(text="Запустить Worker", callback_data="worker_start"))
                worker_row.append(InlineKeyboardButton(text="Worker Остановлен", callback_data="info_worker"))
            
            kb.append(worker_row)
            
            kb.append([
                InlineKeyboardButton(text="🎁 Активировать Промокод", callback_data="user_promo"),
                InlineKeyboardButton(text="❌ Выход (Удалить сессию)", callback_data="auth_logout")
            ])
            
            if is_admin:
                kb.append([InlineKeyboardButton(text="👑 Админ-Панель", callback_data="admin_panel")])


    return InlineKeyboardMarkup(inline_keyboard=kb)

async def send_start_menu(chat_id: int, bot: Bot, db: AsyncDatabase, tm: TelethonManager, is_initial_check: bool = False):
    
    uid = chat_id
    is_subscribed_bool, sub_status_text = await db.get_subscription_status(uid, ADMIN_ID)
    user_data = await db.get_user(uid)
    is_telethon_active = user_data['telethon_active'] if user_data else False
    is_worker_running = uid in tm.store.active_workers
    has_progress = uid in tm.store.process_progress
    is_admin = uid == ADMIN_ID
    
    # 4. Проверка подписки на канал
    if is_initial_check and not is_subscribed_bool and not is_admin:
        try:
            member = await bot.get_chat_member(TARGET_CHANNEL_URL, uid)
            is_member_of_channel = member.status in ['member', 'creator', 'administrator']
        except Exception as e:
            logger.error(f"Error checking channel subscription for {uid}: {e}")
            is_member_of_channel = False
            
        if not is_member_of_channel:
            channel_name = TARGET_CHANNEL_URL.lstrip('@')
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{channel_name}")],
                [InlineKeyboardButton(text="Я подписался", callback_data="check_subscription")]
            ])
            await bot.send_message(
                chat_id, 
                f"⚠️ Для использования бота, пожалуйста, подпишитесь на наш канал: **{TARGET_CHANNEL_URL}**",
                reply_markup=kb, parse_mode='Markdown'
            )
            return

    # Отправка основного меню
    text = (
        f"🤖 Привет, **{chat_id}**!\n\n"
        f"**Статус подписки:** {sub_status_text}\n"
        f"**Статус Telethon:** {'🟢 Активен' if is_worker_running else '🔴 Остановлен' if is_telethon_active else '⚪️ Нет сессии'}\n\n"
        f"Выберите действие ниже:"
    )
    
    kb = get_main_menu_kb(is_subscribed_bool, is_telethon_active, is_worker_running, has_progress, is_admin)
    await bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=kb)


@user_router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await send_start_menu(message.from_user.id, bot, db, tm, is_initial_check=True)

@user_router.callback_query(F.data == "check_subscription")
async def cb_check_subscription(callback: CallbackQuery, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await callback.answer("Проверяю подписку...")
    await send_start_menu(callback.from_user.id, bot, db, tm, is_initial_check=True)
    await callback.message.delete()

# =========================================================================
# II. TELETHON AUTH FLOW
# =========================================================================

@user_router.callback_query(F.data == "auth_phone")
async def cb_auth_phone(callback: CallbackQuery, state: FSMContext, db: AsyncDatabase, **kwargs):
    tm: TelethonManager = kwargs['tm']
    uid = callback.from_user.id
    
    is_subscribed = await db.check_subscription(uid, ADMIN_ID)
    user = await db.get_user(uid)
    if user and user['telethon_active'] and not uid == ADMIN_ID:
        await callback.answer("⚠️ Сессия уже привязана. Сначала сделайте Выход.", show_alert=True)
        return
        
    if not is_subscribed and not uid == ADMIN_ID:
        await callback.answer("⚠️ Требуется активная подписка для входа.", show_alert=True)
        return

    await state.clear()
    await state.set_state(TelethonAuth.PHONE)
    await callback.message.edit_text("📞 **Введите номер телефона** для авторизации (например, `+79xxxxxxxxxx`):", parse_mode='Markdown')

# ... (остальные обработчики авторизации Telethon - PHONE, CODE, PASSWORD, QR) ...
@user_router.message(StateFilter(TelethonAuth.PHONE))
async def auth_get_phone(message: Message, state: FSMContext, **kwargs):
    tm: TelethonManager = kwargs['tm']
    phone = check_valid_phone(message.text)
    
    if not phone: return await message.answer("❌ Неверный формат. Введите номер, начиная с **+** и кодом страны.")
    result_msg = await tm.send_code(message.from_user.id, phone)
    
    if result_msg.startswith("❌"):
        await state.clear()
        return await message.answer(result_msg)
        
    await state.set_state(TelethonAuth.CODE)
    await message.answer(f"{result_msg} на **{phone}**. Введите его:")

@user_router.message(StateFilter(TelethonAuth.CODE))
async def auth_get_code(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, result_msg, client = await tm.sign_in(message.from_user.id, message.text.strip())
    
    if success:
        await state.clear()
        await message.answer(result_msg)
        await send_start_menu(message.from_user.id, bot, db, tm)
    elif result_msg.startswith("⚠️"):
        await state.set_state(TelethonAuth.PASSWORD)
        await message.answer(result_msg)
    else:
        await state.clear()
        await message.answer(result_msg)

@user_router.message(StateFilter(TelethonAuth.PASSWORD))
async def auth_get_password(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, result_msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    
    await message.answer(result_msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)
    else:
        await state.clear()
        await message.answer("Попробуйте начать /start заново.")

@user_router.callback_query(F.data == "auth_qr")
async def cb_auth_qr_start(callback: CallbackQuery, state: FSMContext, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    uid = callback.from_user.id
    is_subscribed = await db.check_subscription(uid, ADMIN_ID)
    if not is_subscribed and not uid == ADMIN_ID:
        await callback.answer("⚠️ Требуется активная подписка для входа.", show_alert=True)
        return
        
    await callback.answer("Генерация QR-кода...")
    
    try:
        qr_url, qr_image_pil = await tm.start_qr_login(uid)
    except Exception as e:
        logger.error(f"Failed to start QR login for {uid}: {e}")
        return await callback.message.answer("❌ Не удалось начать QR-авторизацию. Попробуйте по номеру.")

    message_text = textwrap.dedent(f"""
    ✅ **QR-авторизация запущена!**
    ...
    ⚠️ Код действует **{tm.store.temp_data.get(uid, {}).get('qr_login_data').expires} секунд**.
    """)
    
    # Генерация или использование готового QR
    bio = BytesIO()
    if qr_image_pil:
        qr_image_pil.save(bio, 'PNG')
    else:
        qr_img = qrcode.make(qr_url)
        qr_img.save(bio, 'PNG')
    
    bio.name = 'qr_code.png'
    bio.seek(0)
    
    await callback.message.answer_photo(
        bio, 
        caption=f"{message_text}\n\n[**URL для ручного ввода**]({qr_url})" if not qr_image_pil else message_text, 
        parse_mode='Markdown'
    )
    
    await state.set_state(TelethonAuth.WAITING_FOR_QR_LOGIN)

    data = tm.store.temp_data.get(uid)
    client = data['client']
    qr_login_data = data['qr_login_data']
    
    success, result_msg = await tm.check_qr_login(uid, qr_login_data, client)

    if success:
        await state.clear()
        await send_start_menu(uid, kwargs['bot'], db, tm)
    elif result_msg.startswith("⚠️"):
        await state.set_state(TelethonAuth.QR_PASSWORD)
        await callback.message.answer(result_msg)
    else:
        await state.clear()
        await callback.message.answer(result_msg)

@user_router.message(StateFilter(TelethonAuth.QR_PASSWORD))
async def auth_get_qr_password(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    success, result_msg = await tm.sign_in_password(message.from_user.id, message.text.strip())
    
    await message.answer(result_msg)
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)
    else:
        await state.clear()
        await message.answer("Попробуйте начать /start заново.")

# =========================================================================
# III. WORKER И СЕССИЯ
# =========================================================================

@user_router.callback_query(F.data == "worker_stop")
async def cb_worker_stop(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id)
    await callback.answer("Worker остановлен.", show_alert=True)
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "worker_start")
async def cb_worker_start(callback: CallbackQuery, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    success = await tm.start_client_task(callback.from_user.id)
    
    if success:
        await callback.answer("Worker запущен.", show_alert=True)
    else:
        await callback.answer("Ошибка запуска. Сессия не найдена или недействительна.", show_alert=True)
        
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "auth_logout")
async def cb_auth_logout(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить сессию", callback_data="logout_confirm")],
        [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="logout_cancel")]
    ])
    await state.set_state(UserState.WAITING_FOR_LOGOUT_CONFIRM)
    await callback.message.edit_text("⚠️ **Подтвердите удаление сессии!**\nВыход удалит файл сессии, и вам потребуется повторная авторизация.", reply_markup=kb, parse_mode='Markdown')

@user_router.callback_query(F.data == "logout_confirm", StateFilter(UserState.WAITING_FOR_LOGOUT_CONFIRM))
async def cb_logout_confirm(callback: CallbackQuery, state: FSMContext, tm: TelethonManager, bot: Bot, db: AsyncDatabase, **kwargs):
    await tm.stop_worker(callback.from_user.id, delete_session=True)
    await state.clear()
    await callback.answer("Сессия удалена.", show_alert=True)
    await send_start_menu(callback.from_user.id, bot, db, tm)

@user_router.callback_query(F.data == "logout_cancel", StateFilter(UserState.WAITING_FOR_LOGOUT_CONFIRM))
async def cb_logout_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    await state.clear()
    await callback.answer("Отменено.")
    await send_start_menu(callback.from_user.id, bot, db, tm)

# =========================================================================
# IV. ПРОМОКОДЫ
# =========================================================================

@user_router.callback_query(F.data == "user_promo")
async def cb_user_promo(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.WAITING_FOR_PROMO_CODE)
    await callback.message.edit_text("🔑 **Введите ваш промокод** для активации подписки:")

@user_router.message(StateFilter(UserState.WAITING_FOR_PROMO_CODE))
async def process_promo_code(message: Message, state: FSMContext, bot: Bot, db: AsyncDatabase, tm: TelethonManager, **kwargs):
    code = message.text.strip().upper() 
    success, result_msg = await db.apply_promo_code(message.from_user.id, code)
    
    await message.answer(result_msg, parse_mode='Markdown')
    
    if success:
        await state.clear()
        await send_start_menu(message.from_user.id, bot, db, tm)
    else:
        await message.answer("Попробуйте ввести другой код или введите /start для отмены.")

# =========================================================================
# V. ОТЧЕТЫ CHECKGROUP
# =========================================================================

@user_router.callback_query(F.data == "report_send")
async def cb_report_send(callback: CallbackQuery, store: GlobalStorage, **kwargs):
    uid = callback.from_user.id
    progress = store.process_progress.get(uid)
    
    if not progress or 'report_data' not in progress:
        return await callback.answer("❌ Отчет не найден. Запустите .чекгруппу снова.", show_alert=True)
        
    report_data = progress['report_data']
    peer_name = progress['peer_name']
    
    os.makedirs(TEMP_DIR, exist_ok=True)
    temp_filename = os.path.join(TEMP_DIR, f"report_{uid}_{peer_name.replace(' ', '_')}_{int(asyncio.get_event_loop().time())}.txt")
    
    with open(temp_filename, 'w', encoding='utf-8') as f:
        f.write(report_data)

    try:
        await callback.message.answer_document(
            document=temp_filename, 
            caption=f"✅ Отчет по сканированию `{peer_name}` готов.",
            parse_mode='Markdown'
        )
        del store.process_progress[uid] 
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка отправки файла: {e}")
    finally:
        os.remove(temp_filename)
        
    await callback.message.delete()


@user_router.callback_query(F.data == "report_delete")
async def cb_report_delete(callback: CallbackQuery, store: GlobalStorage, **kwargs):
    uid = callback.from_user.id
    
    if uid in store.process_progress:
        del store.process_progress[uid]
        await callback.answer("Отчет удален.", show_alert=True)
    else:
        await callback.answer("Отчет не найден.", show_alert=True)
        
    await callback.message.delete()
    
# =========================================================================
# VI. АДМИН-ПАНЕЛЬ
# =========================================================================

@admin_router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("❌ Доступ запрещен.")
    
    text = textwrap.dedent("""
    👑 **Админ-панель**
    Выберите действие:
    `/create_promo` - Создать новый промокод
    `/stats` - Показать статистику (заглушка)
    """)
    await callback.message.answer(text, parse_mode='Markdown')
    await callback.answer()

@admin_router.message(Command("create_promo"))
async def cmd_create_promo(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    await state.set_state(AdminState.CREATING_PROMO_CODE)
    await message.answer("🔑 Введите данные для нового промокода в формате:\n"
                         "`КОД ДНИ_СУБС. МАКС_ИСПОЛЬЗОВАНИЙ`\n"
                         "Пример: `TEST20 20 50`", parse_mode='Markdown')

@admin_router.message(StateFilter(AdminState.CREATING_PROMO_CODE))
async def process_create_promo(message: Message, state: FSMContext, db: AsyncDatabase, **kwargs):
    if message.from_user.id != ADMIN_ID: return
    
    parts = message.text.split()
    if len(parts) != 3:
        return await message.answer("❌ Неверный формат. Используйте: `КОД ДНИ_СУБС. МАКС_ИСПОЛЬЗОВАНИЙ`")
        
    code, days_str, max_uses_str = parts
    code = code.upper() 
    
    try:
        days = int(days_str)
        max_uses = int(max_uses_str)
        if days <= 0 or max_uses <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Дни и макс. использования должны быть положительными числами.")
        
    success = await db.create_promo_code(code, days, max_uses)
    
    if success:
        await state.clear()
        await message.answer(f"✅ Промокод **`{code}`** успешно создан!\n"
                             f"Дней: {days}, Лимит: {max_uses}.", parse_mode='Markdown')
    else:
        await message.answer("❌ Промокод с таким кодом уже существует. Попробуйте другой код.")
        
@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("📈 **Статистика (Заглушка)**: Функционал статистики будет добавлен позже.")
    
# =========================================================================
# VII. DROP-СИСТЕМА (ОБРАБОТЧИКИ В ЧАТЕ)
# =========================================================================

@drop_router.message(Command("numb"))
async def cmd_numb(message: Message, db: AsyncDatabase, store: GlobalStorage, **kwargs):
    chat_id = message.chat.id
    thread_id = message.message_thread_id or 0
    drop_id = message.from_user.id
    
    pc_name = store.drop_mapping.get((chat_id, thread_id))
    if not pc_name:
        return await message.answer("❌ **Ошибка:** ПК не привязан к этой теме. Используйте `.пкстарт <НазваниеПК>` из Telethon-аккаунта.")
        
    phone_flag = await db.create_drop_session(pc_name, drop_id)
    
    await message.answer(f"✅ **ПК: {pc_name}**. Начало сессии.\n**Ожидаю номер** от айтишника. (Телефон: `{phone_flag}`)", parse_mode='Markdown')

@drop_router.message(Command("num"))
async def cmd_num(message: Message, db: AsyncDatabase, **kwargs):
    args = message.text.split()
    if len(args) < 2: return await message.answer("Формат: `/num <номер>`")
    new_phone = check_valid_phone(args[1])
    if not new_phone: return await message.answer("❌ Неверный формат номера.")
    
    drop_id = message.from_user.id
    latest_session = await db.get_latest_drop_session(drop_id)
    if not latest_session: return await message.answer("❌ Не найдена активная сессия. Начните с `/numb`.")
    
    current_phone = latest_session['phone']
    await db.update_drop_session(current_phone, new_phone=new_phone, status="дайте номер")
    
    await message.answer(f"✅ Номер **{new_phone}** записан для **{latest_session['pc_name']}**.")

# --- Статусные команды DROP ---
async def drop_status_handler(message: Message, status_key: str, db: AsyncDatabase):
    drop_id = message.from_user.id
    latest_session = await db.get_latest_drop_session(drop_id)
    if not latest_session: return await message.answer("❌ Не найдена активная сессия. Начните с `/numb`.")
    
    await db.update_drop_session(latest_session['phone'], status=status_key)
    
    await message.answer(f"✅ Статус для **{latest_session['pc_name']}** обновлен на **{status_key}**.")

@drop_router.message(Command("vstal"))
async def cmd_vstal(message: Message, **kwargs): return await drop_status_handler(message, "в работе", kwargs['db'])

@drop_router.message(Command("error"))
async def cmd_error(message: Message, **kwargs): return await drop_status_handler(message, "error", kwargs['db'])

@drop_router.message(Command("slet"))
async def cmd_slet(message: Message, **kwargs): return await drop_status_handler(message, "slet", kwargs['db'])

@drop_router.message(Command("povt"))
async def cmd_povt(message: Message, **kwargs): return await drop_status_handler(message, "повтор", kwargs['db'])

@drop_router.message(Command("zm"))
async def cmd_zm(message: Message, db: AsyncDatabase, **kwargs):
    args = message.text.split()
    if len(args) < 2: return await message.answer("Формат: `/zm <новый_номер>`")
    new_phone = check_valid_phone(args[1])
    if not new_phone: return await message.answer("❌ Неверный формат номера.")
    
    drop_id = message.from_user.id
    latest_session = await db.get_latest_drop_session(drop_id)
    if not latest_session: return await message.answer("❌ Не найдена активная сессия. Начните с `/numb`.")
    
    await db.update_drop_session(latest_session['phone'], new_phone=new_phone, status="замена")
    
    await message.answer(f"✅ Замена на **{new_phone}** для **{latest_session['pc_name']}**.")

# --- Отчет DROP ---

@drop_router.message(Command("report_last"))
async def cmd_report_last(message: Message, db: AsyncDatabase, **kwargs):
    drop_id = message.from_user.id
    latest_session = await db.get_latest_drop_session(drop_id)
    if not latest_session: return await message.answer("❌ Не найдена последняя сессия.")
    
    report_text = format_drop_report(latest_session)
    await message.answer(report_text, parse_mode='Markdown')

@drop_router.message(Command("report"))
async def cmd_report(message: Message, db: AsyncDatabase, **kwargs):
    args = message.text.split()
    if len(args) < 2: return await message.answer("Формат: `/report <номер>`")
    phone = check_valid_phone(args[1])
    if not phone: return await message.answer("❌ Неверный формат номера.")
    
    cursor = await db.conn.execute("SELECT * FROM drop_sessions WHERE phone = ?", (phone,))
    row = await cursor.fetchone()
    
    if not row: return await message.answer(f"❌ Сессия по номеру **{phone}** не найдена.")
    
    report_text = format_drop_report(row)
    await message.answer(report_text, parse_mode='Markdown')
