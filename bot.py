#!/usr/bin/env python3
import logging
import asyncio
from datetime import datetime, timedelta
from cachetools import TTLCache

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

from config import BOT_TOKEN, DEFAULT_WARN_LIMIT
from database import Database
from utils import parse_time, format_time, create_mute_permissions, is_admin

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация БД
db = Database()

# Кэш для антифлуда (хранит время сообщений пользователей)
# {chat_id_user_id: [timestamps]}
flood_cache = TTLCache(maxsize=10000, ttl=60)

# Кэш для проверки админов (чтобы не дудосить Telegram API)
admin_cache = TTLCache(maxsize=1000, ttl=300)  # 5 минут

# === КОМАНДЫ МОДЕРАЦИИ ===

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бан пользователя (/ban)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    # Проверяем, есть ли ответ на сообщение
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение пользователя, которого хотите забанить!")
        return
    
    user_to_ban = message.reply_to_message.from_user
    
    try:
        await chat.ban_member(user_to_ban.id)
        await message.reply_text(f"✅ Пользователь {user_to_ban.full_name} забанен.")
        
        # Логируем действие
        logger.info(f"User {user_to_ban.id} banned by {update.effective_user.id} in chat {chat.id}")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разбан пользователя (/unban)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    # Проверяем аргументы (username или ID)
    if not context.args:
        await message.reply_text("❌ Укажите username или ID пользователя!\nПример: /unban @username")
        return
    
    target = context.args[0]
    
    try:
        if target.startswith('@'):
            # Поиск по username
            # В Telegram API нет прямого метода, используем chat.get_member с username
            await message.reply_text("❌ Разбан по username временно недоступен. Используйте ID.")
            return
        else:
            # По ID
            user_id = int(target)
        
        await chat.unban_member(user_id)
        await message.reply_text(f"✅ Пользователь {user_id} разбанен.")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заглушить пользователя (/mute [время])"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение пользователя, которого хотите заглушить!")
        return
    
    user_to_mute = message.reply_to_message.from_user
    
    # Парсим время
    duration = None
    if context.args:
        duration = parse_time(context.args[0])
    
    if not duration:
        duration = 3600  # 1 час по умолчанию
    
    mute_until = datetime.now() + timedelta(seconds=duration)
    
    try:
        # Ограничиваем права
        await chat.restrict_member(
            user_to_mute.id,
            permissions=create_mute_permissions(),
            until_date=mute_until
        )
        
        # Сохраняем в БД
        db.add_mute(chat.id, user_to_mute.id, duration)
        
        await message.reply_text(
            f"🔇 Пользователь {user_to_mute.full_name} заглушен на {format_time(duration)}."
        )
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Снять заглушение (/unmute)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение пользователя!")
        return
    
    user_to_unmute = message.reply_to_message.from_user
    
    try:
        # Возвращаем обычные права
        await chat.restrict_member(
            user_to_unmute.id,
            permissions=ChatPermissions(can_send_messages=True)
        )
        
        # Удаляем из БД
        db.remove_mute(chat.id, user_to_unmute.id)
        
        await message.reply_text(f"🔊 Пользователь {user_to_unmute.full_name} разблокирован.")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдать предупреждение (/warn [причина])"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение пользователя!")
        return
    
    user_to_warn = message.reply_to_message.from_user
    reason = ' '.join(context.args) if context.args else "Без причины"
    
    # Добавляем предупреждение
    warn_count = db.add_warning(chat.id, user_to_warn.id, update.effective_user.id, reason)
    
    # Получаем лимит из настроек чата
    settings = db.get_chat_settings(chat.id)
    warn_limit = settings.get('warn_limit', DEFAULT_WARN_LIMIT)
    
    if warn_count >= warn_limit:
        # Превышен лимит - баним
        try:
            await chat.ban_member(user_to_warn.id)
            db.clear_warnings(chat.id, user_to_warn.id)
            await message.reply_text(
                f"🚫 {user_to_warn.full_name} получил {warn_count}/{warn_limit} предупреждений и был забанен.\n"
                f"Причина последнего: {reason}"
            )
        except Exception as e:
            await message.reply_text(f"❌ Ошибка при бане: {str(e)}")
    else:
        await message.reply_text(
            f"⚠️ {user_to_warn.full_name} получил предупреждение ({warn_count}/{warn_limit})\n"
            f"Причина: {reason}"
        )

async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Снять последнее предупреждение (/unwarn)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение пользователя!")
        return
    
    user_to_unwarn = message.reply_to_message.from_user
    
    # Удаляем предупреждение
    warn_count = db.remove_warning(chat.id, user_to_unwarn.id)
    
    await message.reply_text(
        f"✅ С пользователя {user_to_unwarn.full_name} снято предупреждение.\n"
        f"Текущее количество: {warn_count}"
    )

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистить сообщения (/clear [количество])"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    # Определяем количество сообщений для удаления
    count = 10  # по умолчанию
    if context.args:
        try:
            count = int(context.args[0])
            if count > 100:
                count = 100
        except ValueError:
            await message.reply_text("❌ Укажите число!")
            return
    
    # Удаляем сообщения
    deleted = 0
    try:
        if message.reply_to_message:
            # Удаляем начиная с ответного сообщения
            message_id = message.reply_to_message.message_id
            for i in range(count):
                try:
                    await chat.delete_message(message_id + i)
                    deleted += 1
                    await asyncio.sleep(0.5)  # Защита от лимитов
                except:
                    pass
        else:
            # Удаляем последние N сообщений (не работает в группах без супергруппы)
            await message.reply_text("❌ Для очистки без ответа нужна супергруппа. Ответьте на сообщение!")
            return
        
        result_msg = await message.reply_text(f"✅ Удалено {deleted} сообщений.")
        
        # Удаляем команду и результат через 3 секунды
        await asyncio.sleep(3)
        await message.delete()
        await result_msg.delete()
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Закрепить сообщение (/pin)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    message = update.message
    
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение для закрепления!")
        return
    
    try:
        await message.reply_to_message.pin(disable_notification=True)
        await message.reply_text("📌 Сообщение закреплено.")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def slowmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Включить медленный режим (/slowmode [секунды])"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Эта команда только для администраторов!")
        return
    
    chat = update.effective_chat
    message = update.message
    
    seconds = 5
    if context.args:
        try:
            seconds = int(context.args[0])
            if seconds < 0:
                seconds = 0
            if seconds > 300:  # Максимум 5 минут
                seconds = 300
        except ValueError:
            await message.reply_text("❌ Укажите число секунд!")
            return
    
    try:
        # Устанавливаем медленный режим
        await chat.set_slow_mode_delay(seconds)
        
        if seconds > 0:
            await message.reply_text(f"🐢 Медленный режим включен: {seconds} сек между сообщениями.")
        else:
            await message.reply_text("🐢 Медленный режим отключен.")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

# === ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ===

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пожаловаться на сообщение (/report)"""
    message = update.message
    
    if not message.reply_to_message:
        await message.reply_text("❌ Ответьте на сообщение, на которое хотите пожаловаться!")
        return
    
    reported_msg = message.reply_to_message
    reporter = update.effective_user
    reported_user = reported_msg.from_user
    
    # Ищем администраторов в чате
    chat = update.effective_chat
    admins = await chat.get_administrators()
    
    # Отправляем жалобу в личные сообщения админам
    report_text = (
        f"🚨 ЖАЛОБА в чате {chat.title}\n\n"
        f"От: {reporter.full_name} (@{reporter.username})\n"
        f"На: {reported_user.full_name} (@{reported_user.username})\n"
        f"Сообщение: {reported_msg.text or reported_msg.caption or '[медиа]'}\n"
        f"[Перейти к сообщению]({reported_msg.link})"
    )
    
    sent_count = 0
    for admin in admins:
        if not admin.user.is_bot:
            try:
                await context.bot.send_message(
                    admin.user.id,
                    report_text,
                    parse_mode=ParseMode.MARKDOWN
                )
                sent_count += 1
            except:
                pass
    
    await message.reply_text(f"✅ Жалоба отправлена {sent_count} администраторам.")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о пользователе (/info [ответ/username])"""
    chat = update.effective_chat
    message = update.message
    
    # Определяем, о ком информация
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif context.args and context.args[0].startswith('@'):
        # Поиск по username (упрощенно)
        username = context.args[0][1:]
        await message.reply_text("❌ Поиск по username временно недоступен.")
        return
    else:
        target_user = update.effective_user
    
    # Получаем статистику
    stats = db.get_user_stats(chat.id, target_user.id)
    warns = db.get_warnings_count(chat.id, target_user.id)
    is_muted_user = db.is_muted(chat.id, target_user.id)
    
    # Получаем информацию о пользователе из чата
    try:
        chat_member = await chat.get_member(target_user.id)
        status_map = {
            'creator': 'Создатель',
            'administrator': 'Админ',
            'member': 'Участник',
            'restricted': 'Ограничен',
            'left': 'Покинул',
            'kicked': 'Забанен'
        }
        status = status_map.get(chat_member.status, chat_member.status)
    except:
        status = "Неизвестно"
    
    # Формируем ответ
    info_text = (
        f"👤 **Информация о пользователе**\n\n"
        f"**Имя:** {target_user.full_name}\n"
        f"**Username:** @{target_user.username if target_user.username else 'нет'}\n"
        f"**ID:** `{target_user.id}`\n"
        f"**Статус:** {status}\n"
        f"**Предупреждения:** {warns}\n"
        f"**Статус мута:** {'🔇 Да' if is_muted_user else '🔊 Нет'}\n"
    )
    
    if stats:
        info_text += (
            f"\n**Статистика:**\n"
            f"**Сообщений:** {stats['messages_count']}\n"
            f"**Впервые:** {stats['first_seen']}\n"
            f"**Последний раз:** {stats['last_seen']}\n"
        )
    
    await message.reply_text(info_text, parse_mode=ParseMode.MARKDOWN)

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать правила (/rules)"""
    chat_id = update.effective_chat.id
    settings = db.get_chat_settings(chat_id)
    
    keyboard = [[InlineKeyboardButton("✅ Принимаю правила", callback_data="accept_rules")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        settings.get('rules', "Правила не установлены."),
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать помощь (/help)"""
    help_text = """
🤖 **Доступные команды:**

**👑 Для админов:**
• /ban - забанить пользователя
• /unban - разбанить
• /mute [время] - заглушить (1h, 1d, 30m)
• /unmute - снять заглушение
• /warn [причина] - выдать предупреждение
• /unwarn - снять предупреждение
• /clear [N] - удалить N сообщений
• /pin - закрепить сообщение
• /slowmode [сек] - медленный режим

**👥 Для всех:**
• /report - пожаловаться на сообщение
• /info - информация о пользователе
• /rules - правила чата
• /help - это сообщение

**💡 Советы:**
• Для большинства команд ответьте на сообщение пользователя
• Время указывается цифрой и буквой: 5m, 2h, 1d
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню с кнопками"""
    keyboard = [
        [InlineKeyboardButton("📜 Правила", callback_data="menu_rules")],
        [InlineKeyboardButton("ℹ️ Моя информация", callback_data="menu_info")],
        [InlineKeyboardButton("🆘 Помощь", callback_data="menu_help")],
        [InlineKeyboardButton("🚨 Пожаловаться", callback_data="menu_report")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📋 **Главное меню**\nВыберите действие:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# === ОБРАБОТЧИКИ СОБЫТИЙ ===

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие новых участников"""
    chat = update.effective_chat
    settings = db.get_chat_settings(chat.id)
    
    for new_member in update.message.new_chat_members:
        # Пропускаем ботов
        if new_member.is_bot:
            continue
        
        # Проверка на подозрительность (аккаунт младше 7 дней)
        account_age = (datetime.now() - new_member.created_at).days if new_member.created_at else 999
        is_suspicious = account_age < 7
        
        welcome_text = settings.get('welcome_message', DEFAULT_WELCOME_MESSAGE)
        welcome_text = welcome_text.format(name=new_member.full_name)
        
        if is_suspicious:
            welcome_text += "\n\n⚠️ **Внимание:** Ваш аккаунт слишком новый. Пройдите проверку:"
            
            # Создаем капчу
            import random
            num1 = random.randint(1, 10)
            num2 = random.randint(1, 10)
            captcha_answer = num1 + num2
            
            # Сохраняем ответ в контексте
            context.user_data['captcha'] = {
                'chat_id': chat.id,
                'user_id': new_member.id,
                'answer': captcha_answer
            }
            
            keyboard = [[InlineKeyboardButton("Решить капчу", callback_data="solve_captcha")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"{welcome_text}\n\n{num1} + {num2} = ?",
                reply_markup=reply_markup
            )
            
            # Ограничиваем права нового пользователя
            await chat.restrict_member(
                new_member.id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        else:
            await update.message.reply_text(welcome_text)

async def handle_left_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выхода участника"""
    left_member = update.message.left_chat_member
    if left_member and not left_member.is_bot:
        logger.info(f"User {left_member.id} left chat {update.effective_chat.id}")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений (антифлуд, антимат, проверка мута)"""
    if not update.message or not update.message.text:
        return
    
    chat = update.effective_chat
    user = update.effective_user
    message = update.message
    
    # Проверяем, не заглушен ли пользователь
    if db.is_muted(chat.id, user.id):
        try:
            await message.delete()
        except:
            pass
        return
    
    # Обновляем статистику
    db.update_user_stats(chat.id, user.id, user.username, user.first_name)
    
    # Получаем настройки чата
    settings = db.get_chat_settings(chat.id)
    
    # АНТИФЛУД
    if settings.get('antiflood_enabled', True):
        cache_key = f"{chat.id}_{user.id}"
        
        if cache_key not in flood_cache:
            flood_cache[cache_key] = []
        
        # Добавляем текущее время
        current_time = datetime.now().timestamp()
        flood_cache[cache_key].append(current_time)
        
        # Удаляем старые записи
        flood_cache[cache_key] = [
            t for t in flood_cache[cache_key] 
            if current_time - t <= settings.get('antiflood_seconds', 10)
        ]
        
        # Проверяем количество сообщений за период
        if len(flood_cache[cache_key]) > settings.get('antiflood_count', 5):
            # Флуд! Заглушаем на 5 минут
            try:
                await message.delete()
                
                mute_until = datetime.now() + timedelta(minutes=5)
                await chat.restrict_member(
                    user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=mute_until
                )
                
                db.add_mute(chat.id, user.id, 300)  # 5 минут
                
                await context.bot.send_message(
                    chat.id,
                    f"🚫 {user.full_name} заглушен на 5 минут за флуд."
                )
            except:
                pass
            return
    
    # АНТИ-МАТ
    bad_words = db.get_bad_words(chat.id)
    if bad_words:
        text_lower = message.text.lower()
        for word in bad_words:
            if word.lower() in text_lower:
                try:
                    await message.delete()
                    
                    # Выдаем предупреждение
                    warn_count = db.add_warning(chat.id, user.id, context.bot.id, f"Мат: {word}")
                    
                    await context.bot.send_message(
                        chat.id,
                        f"⚠️ {user.full_name}, использование запрещенных слов запрещено!\n"
                        f"Предупреждение {warn_count}/{settings.get('warn_limit', 3)}"
                    )
                    
                    # Проверяем лимит предупреждений
                    if warn_count >= settings.get('warn_limit', 3):
                        await chat.ban_member(user.id)
                        await context.bot.send_message(
                            chat.id,
                            f"🚫 {user.full_name} забанен за превышение лимита предупреждений."
                        )
                except:
                    pass
                return

# === ОБРАБОТЧИКИ КНОПОК ===

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    chat = query.message.chat
    
    if data == "accept_rules":
        await query.edit_message_text("✅ Спасибо! Правила приняты.")
        
        # Если пользователь был ограничен, снимаем ограничения
        if db.is_muted(chat.id, user.id):
            db.remove_mute(chat.id, user.id)
            await chat.restrict_member(
                user.id,
                permissions=ChatPermissions(can_send_messages=True)
            )
    
    elif data == "solve_captcha":
        if 'captcha' in context.user_data:
            captcha = context.user_data['captcha']
            if captcha['user_id'] == user.id and captcha['chat_id'] == chat.id:
                await query.edit_message_text(
                    "✅ Капча решена! Напишите ответ в чат."
                )
            else:
                await query.edit_message_text("❌ Это не ваша капча!")
        else:
            await query.edit_message_text("❌ Капча не найдена!")
    
    elif data == "menu_rules":
        settings = db.get_chat_settings(chat.id)
        keyboard = [[InlineKeyboardButton("✅ Принять", callback_data="accept_rules")]]
        await query.edit_message_text(
            settings.get('rules', "Правила не установлены."),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "menu_info":
        stats = db.get_user_stats(chat.id, user.id)
        warns = db.get_warnings_count(chat.id, user.id)
        
        text = f"**Ваша информация:**\n\nID: `{user.id}`\n"
        text += f"Предупреждений: {warns}\n"
        
        if stats:
            text += f"Сообщений: {stats['messages_count']}\n"
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    
    elif data == "menu_help":
        await query.edit_message_text(
            "Используйте /help для списка команд.\n"
            "Или просто пишите в чат!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "menu_report":
        await query.edit_message_text(
            "Чтобы пожаловаться, ответьте /report на сообщение."
        )

# === НАСТРОЙКИ (АДМИН-ПАНЕЛЬ) ===

async def set_welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить приветствие (/set_welcome Текст)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Только для админов!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Укажите текст приветствия!")
        return
    
    welcome_text = ' '.join(context.args)
    chat_id = update.effective_chat.id
    
    db.update_welcome(chat_id, welcome_text)
    await update.message.reply_text("✅ Приветствие обновлено!")

async def set_rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить правила (/set_rules Текст)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Только для админов!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Укажите текст правил!")
        return
    
    rules_text = ' '.join(context.args)
    chat_id = update.effective_chat.id
    
    db.update_rules(chat_id, rules_text)
    await update.message.reply_text("✅ Правила обновлены!")

async def add_badword_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить плохое слово (/add_badword слово)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Только для админов!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Укажите слово!")
        return
    
    word = context.args[0].lower()
    chat_id = update.effective_chat.id
    
    bad_words = db.get_bad_words(chat_id)
    if word not in bad_words:
        bad_words.append(word)
        db.update_bad_words(chat_id, bad_words)
        await update.message.reply_text(f"✅ Слово '{word}' добавлено в черный список!")
    else:
        await update.message.reply_text(f"⚠️ Слово '{word}' уже в списке!")

async def remove_badword_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить плохое слово (/remove_badword слово)"""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ Только для админов!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Укажите слово!")
        return
    
    word = context.args[0].lower()
    chat_id = update.effective_chat.id
    
    bad_words = db.get_bad_words(chat_id)
    if word in bad_words:
        bad_words.remove(word)
        db.update_bad_words(chat_id, bad_words)
        await update.message.reply_text(f"✅ Слово '{word}' удалено из черного списка!")
    else:
        await update.message.reply_text(f"⚠️ Слово '{word}' не найдено в списке!")

# === ОСНОВНАЯ ФУНКЦИЯ ===

def main():
    """Запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды модерации
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("unmute", unmute_command))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("unwarn", unwarn_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("pin", pin_command))
    application.add_handler(CommandHandler("slowmode", slowmode_command))
    
    # Пользовательские команды
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("start", menu_command))
    
    # Команды настройки
    application.add_handler(CommandHandler("set_welcome", set_welcome_command))
    application.add_handler(CommandHandler("set_rules", set_rules_command))
    application.add_handler(CommandHandler("add_badword", add_badword_command))
    application.add_handler(CommandHandler("remove_badword", remove_badword_command))
    
    # Обработчики событий
    application.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, 
        handle_new_members
    ))
    application.add_handler(MessageHandler(
        filters.StatusUpdate.LEFT_CHAT_MEMBER,
        handle_left_member
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_messages
    ))
    
    # Обработчик кнопок
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Запускаем бота
    print("🤖 Бот запущен! Нажмите Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
