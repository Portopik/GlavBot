import re
from datetime import datetime, timedelta
from telegram import ChatPermissions

def parse_time(time_str):
    """
    Парсит время из строки (5m, 1h, 2d)
    Возвращает количество секунд
    """
    if not time_str:
        return None
    
    units = {
        's': 1,
        'm': 60,
        'h': 3600,
        'd': 86400
    }
    
    match = re.match(r'(\d+)([smhd])', time_str.lower())
    if match:
        value, unit = match.groups()
        return int(value) * units.get(unit, 60)
    
    return None

def format_time(seconds):
    """Форматирует секунды в человекочитаемый вид"""
    if seconds < 60:
        return f"{seconds} сек"
    elif seconds < 3600:
        return f"{seconds // 60} мин"
    elif seconds < 86400:
        return f"{seconds // 3600} ч"
    else:
        return f"{seconds // 86400} дн"

def create_mute_permissions():
    """Создает права для заглушенного пользователя"""
    return ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False
    )

def create_default_permissions():
    """Создает обычные права пользователя"""
    return ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False
    )

async def is_admin(update, context, user_id=None):
    """Проверяет, является ли пользователь администратором"""
    if user_id is None:
        user_id = update.effective_user.id
    
    chat = update.effective_chat
    
    try:
        member = await chat.get_member(user_id)
        return member.status in ['administrator', 'creator']
    except:
        return False
