import os
import json
import logging

logger = logging.getLogger(__name__)

class AccessControl:
    def __init__(self):
        self.admins_file = "admins.json"
        self.paid_users_file = "paid_users.json"
        self.admins = set()
        self.paid_users = set()
        self.load_data()
        self.init_admins_from_env()

    def load_data(self):
        try:
            if os.path.exists(self.admins_file):
                with open(self.admins_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.admins = set(data.get("admins", []))
                    logger.info(f"Загружено администраторов: {len(self.admins)}")
            if os.path.exists(self.paid_users_file):
                with open(self.paid_users_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.paid_users = set(data.get("paid_users", []))
                    logger.info(f"Загружено платных пользователей: {len(self.paid_users)}")
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")

    def save_admins(self):
        try:
            with open(self.admins_file, 'w', encoding='utf-8') as f:
                json.dump({"admins": list(self.admins)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения админов: {e}")

    def save_paid_users(self):
        try:
            with open(self.paid_users_file, 'w', encoding='utf-8') as f:
                json.dump({"paid_users": list(self.paid_users)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения платных пользователей: {e}")

    def init_admins_from_env(self):
        import os
        initial_admins = os.getenv('INITIAL_ADMINS', '')
        for admin_str in initial_admins.split(','):
            admin_str = admin_str.strip()
            if admin_str and admin_str.isdigit():
                self.admins.add(int(admin_str))
        if initial_admins:
            self.save_admins()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admins

    def is_paid_user(self, user_id: int) -> bool:
        return user_id in self.paid_users or user_id in self.admins

    def add_admin(self, user_id: int) -> bool:
        if user_id not in self.admins:
            self.admins.add(user_id)
            self.save_admins()
            return True
        return False

    def remove_admin(self, user_id: int) -> bool:
        if user_id in self.admins:
            self.admins.remove(user_id)
            self.save_admins()
            return True
        return False

    def add_paid_user(self, user_id: int) -> bool:
        if user_id not in self.paid_users:
            self.paid_users.add(user_id)
            self.save_paid_users()
            return True
        return False

    def remove_paid_user(self, user_id: int) -> bool:
        if user_id in self.paid_users:
            self.paid_users.remove(user_id)
            self.save_paid_users()
            return True
        return False

    def get_all_admins(self):
        return list(self.admins)

    def get_all_paid_users(self):
        return list(self.paid_users)
