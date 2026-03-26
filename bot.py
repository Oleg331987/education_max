import asyncio
import logging
import os
import json
import base64
import uuid
import tempfile
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

from maxapi import Bot, Dispatcher
from maxapi.types import BotStarted, Command, MessageCreated

# Для извлечения текста из документов
from PyPDF2 import PdfReader
import docx

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Конфигурация ===
BOT_TOKEN = os.getenv('MAX_BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("MAX_BOT_TOKEN not set")
    exit(1)

# ID администратора (хардкод – для простоты)
ADMIN_CHAT_ID = 186120464   # замените на свой ID
MANAGER_CHAT_ID = int(os.getenv('MANAGER_CHAT_ID', 0))
if MANAGER_CHAT_ID == 0:
    logger.error("MANAGER_CHAT_ID must be set")
    exit(1)

# === Данные курса (скопированы из Telegram-бота) ===
MODULES = [
    {
        "id": 1,
        "day": 1,
        "title": "Основы мира тендеров",
        "emoji": "📚",
        "content": "<b>📚 День 1 | Модуль 1: Основы мира тендеров</b>\n\n✅ <b>Что такое тендер?</b>\n...",  # полный текст
        "task": "Найти и изучить 2 тендера в вашей сфере деятельности",
        "audio_file": "module1.mp3",
        "has_audio": True
    },
    # ... остальные модули (2-8) с аналогичной структурой
]

TEST_QUESTIONS = [
    {
        "id": 1,
        "question": "Какой федеральный закон регулирует закупки государственных бюджетных учреждений...",
        "options": {"а": "223-ФЗ", "б": "Гражданский кодекс РФ", "в": "44-ФЗ", "г": "94-ФЗ"},
        "correct": "в",
        "correct_text": "в) 44-ФЗ"
    },
    # ... остальные 7 вопросов
]

ADDITIONAL_MATERIALS = {
    "links": {
        "ЕИС": "https://zakupki.gov.ru",
        # ...
    },
    "contacts": {
        "email": "info@tritika.ru",
        "phone": "+7(4922)223-222",
        "mobile": "+7-904-653-69-87",
        "website": "https://tritika.ru",
        "telegram": "@tritikaru"
    }
}

# === Система доступа (адаптирована из Telegram-бота) ===
class AccessControl:
    def __init__(self):
        self.admins_file = "admins.json"
        self.paid_users_file = "paid_users.json"
        self.admins = set()
        self.paid_users = set()
        self.load_data()
        # Инициализация администраторов из .env (если нужно)
        initial_admins = os.getenv('INITIAL_ADMINS', '')
        if initial_admins:
            for admin_str in initial_admins.split(','):
                try:
                    self.admins.add(int(admin_str.strip()))
                except:
                    pass
            self.save_admins()

    def load_data(self):
        try:
            if os.path.exists(self.admins_file):
                with open(self.admins_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.admins = set(data.get("admins", []))
            if os.path.exists(self.paid_users_file):
                with open(self.paid_users_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.paid_users = set(data.get("paid_users", []))
        except Exception as e:
            logger.error(f"Ошибка загрузки данных доступа: {e}")

    def save_admins(self):
        try:
            with open(self.admins_file, 'w', encoding='utf-8') as f:
                json.dump({"admins": list(self.admins)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения администраторов: {e}")

    def save_paid_users(self):
        try:
            with open(self.paid_users_file, 'w', encoding='utf-8') as f:
                json.dump({"paid_users": list(self.paid_users)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения оплативших пользователей: {e}")

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

access_control = AccessControl()

# === Прогресс пользователей ===
USER_PROGRESS_FILE = "user_progress.json"

def load_user_progress():
    try:
        if os.path.exists(USER_PROGRESS_FILE):
            with open(USER_PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        return {}
    except Exception as e:
        logger.error(f"Ошибка загрузки прогресса: {e}")
        return {}

def save_user_progress():
    try:
        with open(USER_PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_progress, f, ensure_ascii=False, indent=2)
        logger.info(f"Прогресс сохранён для {len(user_progress)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка сохранения прогресса: {e}")

user_progress = load_user_progress()

# === Вспомогательные функции ===
def get_main_menu_text(user_id: int) -> str:
    is_paid = access_control.is_paid_user(user_id)
    is_admin = access_control.is_admin(user_id)

    if is_paid:
        text = "📚 Меню курса\n🎧 Аудио уроки\n📊 Мой прогресс\n📞 Контакты\n🔗 Полезные ссылки\n🆘 Помощь\n📝 Пройти тест\n🏆 Результаты теста\n✅ Отметить все модули\n📥 Скачать чек-лист"
        if is_admin:
            text += "\n👥 Управление доступом"
        return text
    else:
        return "🔓 Получить доступ\n📞 Контакты\n🆘 Помощь\nℹ️ О курсе"

def get_lessons_list_text():
    text = "Список уроков:\n"
    for i, m in enumerate(MODULES, 1):
        completed = False
        # здесь можно показать статус, но для простоты – просто список
        text += f"{m['emoji']} День {m['day']}: {m['title']}\n"
    return text

def get_lesson_nav_text(current_index: int):
    return (f"⬅️ Предыдущий урок\nСледующий урок ➡️\n🎧 Прослушать аудио\n"
            f"✅ Отметить текущий модуль\n📚 Меню курса\n📊 Мой прогресс\n🔙 Главное меню")

async def send_audio_module(chat_id: int, module_index: int):
    module = MODULES[module_index]
    audio_path = os.path.join("audio", module["audio_file"])
    if not os.path.exists(audio_path):
        await bot.send_message(chat_id, "❌ Аудиофайл временно недоступен.")
        return
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    caption = f"🎧 {module['emoji']} Аудио к уроку {module_index+1}: {module['title']}\n\n"
    caption += "После прослушивания введите команду: ✅ Отметить текущий модуль"
    await bot.send_document(
        chat_id=chat_id,
        document=audio_bytes,
        filename=module["audio_file"],
        caption=caption
    )

async def show_module(chat_id: int, module_index: int):
    module = MODULES[module_index]
    # Сохраняем состояние
    user_states[chat_id] = {"mode": "viewing_module", "current_module": module_index}
    # Текст модуля (с очисткой HTML)
    text = module["content"].replace("<b>", "**").replace("</b>", "**") + "\n\n"
    text += f"**Практическое задание:** {module['task']}"
    await bot.send_message(chat_id, text)

    # Отправляем аудио
    await send_audio_module(chat_id, module_index)

    # Отправляем навигацию
    await bot.send_message(chat_id, get_lesson_nav_text(module_index))

async def start_test(chat_id: int):
    # Сохраняем состояние теста
    user_states[chat_id] = {
        "mode": "taking_test",
        "current_question": 0,
        "answers": {},
        "skipped": []
    }
    await send_test_question(chat_id, 0)

async def send_test_question(chat_id: int, q_index: int):
    state = user_states.get(chat_id)
    if not state or state.get("mode") != "taking_test":
        return
    if q_index >= len(TEST_QUESTIONS):
        await finish_test(chat_id)
        return

    q = TEST_QUESTIONS[q_index]
    text = f"**Вопрос {q_index+1} из {len(TEST_QUESTIONS)}**\n\n{q['question']}\n\n"
    for key, val in q["options"].items():
        text += f"{key}) {val}\n"
    text += "\nВведите букву ответа (а, б, в, г) или '⏭ Пропустить'."
    await bot.send_message(chat_id, text)
    state["current_question"] = q_index

async def process_test_answer(chat_id: int, answer: str):
    state = user_states.get(chat_id)
    if not state or state.get("mode") != "taking_test":
        return
    q_index = state["current_question"]
    if q_index >= len(TEST_QUESTIONS):
        return
    q = TEST_QUESTIONS[q_index]
    state["answers"][q["id"]] = answer
    next_q = q_index + 1
    if next_q < len(TEST_QUESTIONS):
        await send_test_question(chat_id, next_q)
    else:
        await finish_test(chat_id)

async def finish_test(chat_id: int):
    state = user_states.get(chat_id)
    if not state:
        return
    answers = state.get("answers", {})
    correct = 0
    results = []
    for q in TEST_QUESTIONS:
        user_ans = answers.get(q["id"])
        is_correct = (user_ans == q["correct"])
        if is_correct:
            correct += 1
        results.append({
            "question_id": q["id"],
            "question": q["question"][:50] + "...",
            "user_answer": user_ans,
            "correct_text": q["correct_text"],
            "is_correct": is_correct
        })
    total = len(TEST_QUESTIONS)
    percent = (correct / total) * 100
    result_text = f"**Результаты теста**\n\n✅ Правильных: {correct} из {total} ({percent:.1f}%)\n\n"
    for r in results:
        result_text += f"{'✅' if r['is_correct'] else '❌'} {r['question']}\n   Ваш ответ: {r['user_answer']}, Правильный: {r['correct_text']}\n"
    # Сохраняем результат
    if chat_id not in user_progress:
        user_progress[chat_id] = {"completed_modules": [], "test_results": []}
    user_progress[chat_id]["test_results"].append({
        "date": datetime.now().isoformat(),
        "correct_answers": correct,
        "total_questions": total,
        "percentage": percent,
        "results": results
    })
    save_user_progress()
    await bot.send_message(chat_id, result_text)
    # Возвращаем в главное меню
    user_states[chat_id] = {"mode": "main"}
    await show_main_menu(chat_id)

async def show_main_menu(chat_id: int):
    user_states[chat_id] = {"mode": "main"}
    text = "Главное меню:\n" + get_main_menu_text(chat_id)
    await bot.send_message(chat_id, text)

# === Обработчики MAX ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_states = {}   # {chat_id: {"mode": "...", ...}}

@dp.bot_started()
async def on_start(event: BotStarted):
    await show_main_menu(event.chat_id)

@dp.message_created(Command('start'))
async def cmd_start(event: MessageCreated):
    await show_main_menu(event.chat_id)

@dp.message_created()
async def handle_message(event: MessageCreated):
    chat_id = event.chat.chat_id
    text = event.message.body.text or ""
    attachments = event.message.body.attachments

    # Обработка текстовых команд
    # Сначала проверяем команды, не зависящие от состояния

    if text == "🔓 Получить доступ":
        # Отправляем реквизиты и уведомляем администратора
        await bot.send_message(chat_id,
            "Стоимость доступа: 3 999 руб.\nРеквизиты: ...\nПосле оплаты напишите менеджеру.")
        await bot.send_message(MANAGER_CHAT_ID,
            f"Запрос доступа от {chat_id} (username: {event.message.sender.username})")
        return

    if text == "ℹ️ О курсе":
        await bot.send_message(chat_id, "Краткое описание курса: ...")
        return

    if text == "📞 Контакты":
        contacts = ADDITIONAL_MATERIALS["contacts"]
        await bot.send_message(chat_id,
            f"📞 {contacts['phone']}\n📧 {contacts['email']}\n🌐 {contacts['website']}\n📱 {contacts['telegram']}")
        return

    if text == "🆘 Помощь":
        await bot.send_message(chat_id,
            "Помощь: используйте команды из главного меню. При технических проблемах пишите менеджеру.")
        return

    # Проверяем, есть ли у пользователя доступ
    is_paid = access_control.is_paid_user(chat_id)
    if not is_paid and text not in ["🔓 Получить доступ", "📞 Контакты", "🆘 Помощь", "ℹ️ О курсе", "◀️ Назад в меню"]:
        await bot.send_message(chat_id, "У вас нет доступа. Нажмите '🔓 Получить доступ'.")
        return

    # Теперь обработка команд, требующих доступа
    state = user_states.get(chat_id, {"mode": "main"})
    mode = state.get("mode")

    if mode == "main":
        if text == "📚 Меню курса":
            await bot.send_message(chat_id, get_lessons_list_text())
            user_states[chat_id] = {"mode": "selecting_lesson"}
        elif text == "🎧 Аудио уроки":
            await bot.send_message(chat_id, "Список аудиоуроков:\n" + "\n".join([f"{i+1}. {m['title']}" for i,m in enumerate(MODULES) if m.get('has_audio')]))
        elif text == "📊 Мой прогресс":
            # Показать прогресс
            prog = user_progress.get(chat_id, {"completed_modules": []})
            completed = len(prog["completed_modules"])
            total = len(MODULES)
            await bot.send_message(chat_id, f"Прогресс: {completed} из {total} модулей пройдено.")
        elif text == "🔗 Полезные ссылки":
            links = ADDITIONAL_MATERIALS["links"]
            text_links = "\n".join([f"{name}: {url}" for name, url in links.items()])
            await bot.send_message(chat_id, text_links)
        elif text == "📝 Пройти тест":
            await start_test(chat_id)
        elif text == "🏆 Результаты теста":
            prog = user_progress.get(chat_id, {})
            tests = prog.get("test_results", [])
            if not tests:
                await bot.send_message(chat_id, "Вы ещё не проходили тест.")
            else:
                last = tests[-1]
                await bot.send_message(chat_id, f"Последний результат: {last['correct_answers']}/{last['total_questions']} ({last['percentage']:.1f}%)")
        elif text == "✅ Отметить все модули":
            if chat_id not in user_progress:
                user_progress[chat_id] = {"completed_modules": []}
            user_progress[chat_id]["completed_modules"] = list(range(1, len(MODULES)+1))
            save_user_progress()
            await bot.send_message(chat_id, f"Все {len(MODULES)} модулей отмечены как пройденные.")
        elif text == "📥 Скачать чек-лист":
            checklist_path = "Чек-лист -Первые 10 шагов в тендерах-.docx"
            if os.path.exists(checklist_path):
                with open(checklist_path, "rb") as f:
                    await bot.send_document(chat_id, f.read(), filename=os.path.basename(checklist_path), caption="Чек-лист первых шагов")
            else:
                await bot.send_message(chat_id, "Файл чек-листа временно недоступен.")
        elif text == "👥 Управление доступом" and access_control.is_admin(chat_id):
            await bot.send_message(chat_id, "Управление доступом:\n➕ Добавить пользователя\n➖ Удалить пользователя\n📋 Список пользователей\n👑 Управление админами")
            user_states[chat_id] = {"mode": "admin_panel"}
        elif text == "◀️ Назад в меню":
            await show_main_menu(chat_id)
        else:
            await bot.send_message(chat_id, "Неизвестная команда. Используйте кнопки главного меню.")

    elif mode == "selecting_lesson":
        # Пользователь выбрал урок
        for i, m in enumerate(MODULES):
            if text.startswith(m['emoji']) or m['title'] in text:
                await show_module(chat_id, i)
                return
        # Если не найдено, возвращаем в меню курса
        await bot.send_message(chat_id, "Урок не найден. Выберите из списка.")
        user_states[chat_id] = {"mode": "main"}

    elif mode == "viewing_module":
        current = state.get("current_module", 0)
        if text == "⬅️ Предыдущий урок":
            if current > 0:
                await show_module(chat_id, current - 1)
            else:
                await bot.send_message(chat_id, "Это первый урок.")
        elif text == "Следующий урок ➡️":
            if current < len(MODULES) - 1:
                await show_module(chat_id, current + 1)
            else:
                await bot.send_message(chat_id, "Это последний урок.")
        elif text == "🎧 Прослушать аудио":
            await send_audio_module(chat_id, current)
        elif text == "✅ Отметить текущий модуль":
            # Отметить модуль как пройденный
            module_num = current + 1
            if chat_id not in user_progress:
                user_progress[chat_id] = {"completed_modules": []}
            if module_num not in user_progress[chat_id]["completed_modules"]:
                user_progress[chat_id]["completed_modules"].append(module_num)
                save_user_progress()
                await bot.send_message(chat_id, f"Модуль {module_num} отмечен как пройденный.")
            else:
                await bot.send_message(chat_id, "Этот модуль уже отмечен.")
        elif text == "📚 Меню курса":
            await bot.send_message(chat_id, get_lessons_list_text())
            user_states[chat_id] = {"mode": "selecting_lesson"}
        elif text == "📊 Мой прогресс":
            prog = user_progress.get(chat_id, {"completed_modules": []})
            completed = len(prog["completed_modules"])
            total = len(MODULES)
            await bot.send_message(chat_id, f"Прогресс: {completed} из {total} модулей пройдено.")
        elif text == "🔙 Главное меню":
            await show_main_menu(chat_id)
        else:
            await bot.send_message(chat_id, "Используйте кнопки навигации.")

    elif mode == "taking_test":
        if text == "⏭ Пропустить":
            state["current_question"] += 1
            await send_test_question(chat_id, state["current_question"])
        elif text in ["а", "б", "в", "г"]:
            await process_test_answer(chat_id, text)
        else:
            await bot.send_message(chat_id, "Введите букву ответа (а, б, в, г) или '⏭ Пропустить'.")

    elif mode == "admin_panel":
        if access_control.is_admin(chat_id):
            if text == "➕ Добавить пользователя":
                await bot.send_message(chat_id, "Введите ID пользователя (число):")
                user_states[chat_id] = {"mode": "admin_add_user"}
            elif text == "➖ Удалить пользователя":
                await bot.send_message(chat_id, "Введите ID пользователя для удаления:")
                user_states[chat_id] = {"mode": "admin_remove_user"}
            elif text == "📋 Список пользователей":
                paid = access_control.get_all_paid_users()
                await bot.send_message(chat_id, f"Пользователи с доступом: {', '.join(map(str, paid))}")
            elif text == "👑 Управление админами":
                await bot.send_message(chat_id, "Добавить админа: /add_admin ID\nУдалить админа: /remove_admin ID")
                # простая реализация – можно расширить
            elif text == "◀️ Назад в меню":
                await show_main_menu(chat_id)
            else:
                await bot.send_message(chat_id, "Неизвестная команда в админ-панели.")
        else:
            await show_main_menu(chat_id)

    elif mode == "admin_add_user":
        try:
            user_id = int(text.strip())
            if access_control.add_paid_user(user_id):
                await bot.send_message(chat_id, f"Пользователь {user_id} добавлен.")
            else:
                await bot.send_message(chat_id, f"Пользователь {user_id} уже имеет доступ.")
        except:
            await bot.send_message(chat_id, "Ошибка: введите числовой ID.")
        await show_main_menu(chat_id)

    elif mode == "admin_remove_user":
        try:
            user_id = int(text.strip())
            if access_control.remove_paid_user(user_id):
                await bot.send_message(chat_id, f"Доступ пользователя {user_id} отозван.")
            else:
                await bot.send_message(chat_id, f"Пользователь {user_id} не найден.")
        except:
            await bot.send_message(chat_id, "Ошибка: введите числовой ID.")
        await show_main_menu(chat_id)

    else:
        # По умолчанию показываем главное меню
        await show_main_menu(chat_id)

# === Запуск ===
async def main():
    try:
        await bot.delete_webhook()
        logger.info("Webhook deleted")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")
    logger.info("Starting polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
