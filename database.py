import sqlite3
import json
from datetime import datetime, timedelta
from config import DATABASE_PATH

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        """Создание всех необходимых таблиц"""
        
        # Настройки чатов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                welcome_message TEXT,
                rules TEXT,
                warn_limit INTEGER DEFAULT 3,
                antiflood_enabled BOOLEAN DEFAULT 1,
                antiflood_count INTEGER DEFAULT 5,
                antiflood_seconds INTEGER DEFAULT 10,
                bad_words TEXT DEFAULT '[]',
                banned_links TEXT DEFAULT '[]',
                slowmode_enabled BOOLEAN DEFAULT 0,
                slowmode_seconds INTEGER DEFAULT 5
            )
        ''')
        
        # Предупреждения пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                warned_by INTEGER,
                reason TEXT,
                created_at TIMESTAMP,
                UNIQUE(chat_id, user_id, created_at)
            )
        ''')
        
        # Заглушенные пользователи
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS muted_users (
                chat_id INTEGER,
                user_id INTEGER,
                mute_until TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        
        # Статистика пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                chat_id INTEGER,
                user_id INTEGER,
                messages_count INTEGER DEFAULT 0,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        
        self.conn.commit()
    
    # === НАСТРОЙКИ ЧАТА ===
    
    def get_chat_settings(self, chat_id):
        """Получить настройки чата"""
        self.cursor.execute(
            "SELECT * FROM chat_settings WHERE chat_id = ?",
            (chat_id,)
        )
        settings = self.cursor.fetchone()
        
        if not settings:
            # Создаем настройки по умолчанию
            self.cursor.execute('''
                INSERT INTO chat_settings (chat_id, welcome_message, rules)
                VALUES (?, ?, ?)
            ''', (chat_id, DEFAULT_WELCOME_MESSAGE, DEFAULT_RULES))
            self.conn.commit()
            
            self.cursor.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?",
                (chat_id,)
            )
            settings = self.cursor.fetchone()
        
        # Преобразуем в словарь
        columns = [description[0] for description in self.cursor.description]
        return dict(zip(columns, settings))
    
    def update_welcome(self, chat_id, message):
        """Обновить приветствие"""
        self.cursor.execute(
            "UPDATE chat_settings SET welcome_message = ? WHERE chat_id = ?",
            (message, chat_id)
        )
        self.conn.commit()
    
    def update_rules(self, chat_id, rules):
        """Обновить правила"""
        self.cursor.execute(
            "UPDATE chat_settings SET rules = ? WHERE chat_id = ?",
            (rules, chat_id)
        )
        self.conn.commit()
    
    def update_bad_words(self, chat_id, words_list):
        """Обновить список плохих слов"""
        self.cursor.execute(
            "UPDATE chat_settings SET bad_words = ? WHERE chat_id = ?",
            (json.dumps(words_list), chat_id)
        )
        self.conn.commit()
    
    def get_bad_words(self, chat_id):
        """Получить список плохих слов"""
        self.cursor.execute(
            "SELECT bad_words FROM chat_settings WHERE chat_id = ?",
            (chat_id,)
        )
        result = self.cursor.fetchone()
        if result and result[0]:
            return json.loads(result[0])
        return []
    
    # === ПРЕДУПРЕЖДЕНИЯ ===
    
    def add_warning(self, chat_id, user_id, warned_by, reason=None):
        """Добавить предупреждение пользователю"""
        self.cursor.execute('''
            INSERT INTO warnings (chat_id, user_id, warned_by, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, user_id, warned_by, reason, datetime.now()))
        self.conn.commit()
        
        # Получаем количество предупреждений
        return self.get_warnings_count(chat_id, user_id)
    
    def get_warnings_count(self, chat_id, user_id):
        """Получить количество предупреждений пользователя"""
        self.cursor.execute('''
            SELECT COUNT(*) FROM warnings
            WHERE chat_id = ? AND user_id = ?
        ''', (chat_id, user_id))
        return self.cursor.fetchone()[0]
    
    def remove_warning(self, chat_id, user_id):
        """Удалить последнее предупреждение"""
        self.cursor.execute('''
            DELETE FROM warnings
            WHERE id = (
                SELECT id FROM warnings
                WHERE chat_id = ? AND user_id = ?
                ORDER BY created_at DESC LIMIT 1
            )
        ''', (chat_id, user_id))
        self.conn.commit()
        
        return self.get_warnings_count(chat_id, user_id)
    
    def clear_warnings(self, chat_id, user_id):
        """Очистить все предупреждения пользователя"""
        self.cursor.execute(
            "DELETE FROM warnings WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        self.conn.commit()
    
    # === МУТЫ ===
    
    def add_mute(self, chat_id, user_id, duration_seconds):
        """Заглушить пользователя"""
        mute_until = datetime.now() + timedelta(seconds=duration_seconds)
        self.cursor.execute('''
            INSERT OR REPLACE INTO muted_users (chat_id, user_id, mute_until)
            VALUES (?, ?, ?)
        ''', (chat_id, user_id, mute_until))
        self.conn.commit()
        return mute_until
    
    def remove_mute(self, chat_id, user_id):
        """Снять заглушение"""
        self.cursor.execute(
            "DELETE FROM muted_users WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        self.conn.commit()
    
    def is_muted(self, chat_id, user_id):
        """Проверить, заглушен ли пользователь"""
        self.cursor.execute('''
            SELECT mute_until FROM muted_users
            WHERE chat_id = ? AND user_id = ?
        ''', (chat_id, user_id))
        
        result = self.cursor.fetchone()
        if not result:
            return False
        
        mute_until = datetime.fromisoformat(result[0])
        if mute_until > datetime.now():
            return True
        else:
            # Мут истек, удаляем
            self.remove_mute(chat_id, user_id)
            return False
    
    # === СТАТИСТИКА ===
    
    def update_user_stats(self, chat_id, user_id, username, first_name):
        """Обновить статистику пользователя"""
        now = datetime.now()
        
        self.cursor.execute('''
            INSERT OR REPLACE INTO user_stats 
            (chat_id, user_id, messages_count, first_seen, last_seen)
            VALUES (?, ?, 
                COALESCE(
                    (SELECT messages_count + 1 FROM user_stats 
                     WHERE chat_id = ? AND user_id = ?),
                    1
                ),
                COALESCE(
                    (SELECT first_seen FROM user_stats 
                     WHERE chat_id = ? AND user_id = ?),
                    ?
                ),
                ?)
        ''', (chat_id, user_id, chat_id, user_id, chat_id, user_id, now, now))
        self.conn.commit()
    
    def get_user_stats(self, chat_id, user_id):
        """Получить статистику пользователя"""
        self.cursor.execute('''
            SELECT * FROM user_stats
            WHERE chat_id = ? AND user_id = ?
        ''', (chat_id, user_id))
        
        result = self.cursor.fetchone()
        if result:
            columns = [description[0] for description in self.cursor.description]
            return dict(zip(columns, result))
        return None
