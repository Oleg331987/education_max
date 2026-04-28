import asyncio
import logging
import os
import json
import sys
from datetime import datetime
from dotenv import load_dotenv

# Импорты из umaxbot (исправлено: umaxbot вместо maxbot)
from umaxbot.bot import Bot
from umaxbot.dispatcher import Dispatcher
from umaxbot.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from umaxbot.filters import F
from umaxbot.fsm import State, StatesGroup
from umaxbot.callback_query import CallbackQuery

from access_control import AccessControl
from modules_data import MODULES, TEST_QUESTIONS, ADDITIONAL_MATERIALS

# Flask для health check
from flask import Flask
import threading

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Конфигурация ===
BOT_TOKEN = os.getenv('MAX_BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("MAX_BOT_TOKEN не задан в .env")
    sys.exit(1)

MANAGER_CHAT_ID = int(os.getenv('MANAGER_CHAT_ID', 0))
if not MANAGER_CHAT_ID:
    logger.warning("MANAGER_CHAT_ID не задан, уведомления о платежах не будут отправляться")

# === Инициализация ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
access_control = AccessControl()
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

def save_user_progress(progress):
    try:
        with open(USER_PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        logger.info("Прогресс сохранён")
    except Exception as e:
        logger.error(f"Ошибка сохранения прогресса: {e}")

user_progress = load_user_progress()
user_states = {}
user_temp_data = {}

# Определение состояний для FSM
class UserState(StatesGroup):
    selecting_lesson = State()
    viewing_module = State()
    taking_test = State()
    admin_add_user = State()
    admin_remove_user = State()

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard(user_id: int):
    is_paid = access_control.is_paid_user(user_id)
    is_admin = access_control.is_admin(user_id)
    buttons = []
    if is_paid:
        buttons.append([
            InlineKeyboardButton(text="📚 Меню курса", callback_data="menu_course"),
            InlineKeyboardButton(text="🎧 Аудио уроки", callback_data="menu_audio")
        ])
        buttons.append([
            InlineKeyboardButton(text="📊 Мой прогресс", callback_data="menu_progress"),
            InlineKeyboardButton(text="📞 Контакты", callback_data="menu_contacts")
        ])
        buttons.append([
            InlineKeyboardButton(text="🔗 Полезные ссылки", callback_data="menu_links"),
            InlineKeyboardButton(text="🆘 Помощь", callback_data="menu_help")
        ])
        buttons.append([
            InlineKeyboardButton(text="📝 Пройти тест", callback_data="menu_test"),
            InlineKeyboardButton(text="🏆 Результаты теста", callback_data="menu_test_results")
        ])
        buttons.append([
            InlineKeyboardButton(text="✅ Отметить все модули", callback_data="menu_mark_all"),
            InlineKeyboardButton(text="📥 Скачать чек-лист", callback_data="menu_checklist")
        ])
        if is_admin:
            buttons.append([InlineKeyboardButton(text="👥 Управление доступом", callback_data="admin_panel")])
    else:
        buttons.append([InlineKeyboardButton(text="🔓 Получить доступ", callback_data="get_access")])
        buttons.append([InlineKeyboardButton(text="📞 Контакты", callback_data="menu_contacts")])
        buttons.append([InlineKeyboardButton(text="🆘 Помощь", callback_data="menu_help")])
        buttons.append([InlineKeyboardButton(text="ℹ️ О курсе", callback_data="about_course")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_lessons_list_keyboard():
    buttons = []
    for m in MODULES:
        buttons.append([InlineKeyboardButton(
            text=f"{m['emoji']} День {m['day']}: {m['title'][:20]}",
            callback_data=f"lesson_{m['id']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_lesson_navigation_keyboard(current_index: int, total: int):
    buttons = [
        [
            InlineKeyboardButton(text="⬅️ Предыдущий урок", callback_data="prev_lesson"),
            InlineKeyboardButton(text=f"📖 {current_index+1}/{total}", callback_data="noop"),
            InlineKeyboardButton(text="Следующий урок ➡️", callback_data="next_lesson")
        ],
        [
            InlineKeyboardButton(text="🎧 Прослушать аудио", callback_data="play_audio"),
            InlineKeyboardButton(text="✅ Отметить текущий модуль", callback_data="mark_current")
        ],
        [
            InlineKeyboardButton(text="📚 Меню курса", callback_data="menu_course"),
            InlineKeyboardButton(text="📊 Мой прогресс", callback_data="menu_progress"),
            InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_test_keyboard(current_num: int, total: int):
    buttons = [
        [
            InlineKeyboardButton(text="а", callback_data="test_ans_а"),
            InlineKeyboardButton(text="б", callback_data="test_ans_б")
        ],
        [
            InlineKeyboardButton(text="в", callback_data="test_ans_в"),
            InlineKeyboardButton(text="г", callback_data="test_ans_г")
        ],
        [
            InlineKeyboardButton(text="⏭ Пропустить", callback_data="test_skip"),
            InlineKeyboardButton(text="🏁 Завершить тест", callback_data="test_finish"),
            InlineKeyboardButton(text=f"❓ {current_num}/{total}", callback_data="noop")
        ],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    buttons = [
        [
            InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin_add_user"),
            InlineKeyboardButton(text="➖ Удалить пользователя", callback_data="admin_remove_user")
        ],
        [
            InlineKeyboardButton(text="📋 Список пользователей", callback_data="admin_list_users"),
            InlineKeyboardButton(text="👑 Управление админами", callback_data="admin_manage_admins")
        ],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def send_audio_module(chat_id: int, module_index: int):
    module = MODULES[module_index]
    audio_path = os.path.join("audio", module["audio_file"])
    if not os.path.exists(audio_path):
        await bot.send_message(chat_id=chat_id, text="❌ Аудиофайл временно недоступен.")
        return
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    caption = f"🎧 {module['emoji']} Аудио к уроку {module_index+1}: {module['title']}"
    await bot.send_file(chat_id=chat_id, file=audio_bytes, filename=module["audio_file"], caption=caption)

async def show_module(chat_id: int, module_index: int):
    module = MODULES[module_index]
    user_states[chat_id] = "viewing_module"
    if chat_id not in user_temp_data:
        user_temp_data[chat_id] = {}
    user_temp_data[chat_id]["current_module"] = module_index
    text = module["content"].replace("<b>", "*").replace("</b>", "*") + "\n\n"
    text += f"*Практическое задание:* {module['task']}"
    await bot.send_message(chat_id=chat_id, text=text)
    if module.get("has_audio"):
        await send_audio_module(chat_id, module_index)
    await bot.send_message(chat_id=chat_id, text="Навигация:", reply_markup=get_lesson_navigation_keyboard(module_index, len(MODULES)))

async def send_test_question(chat_id: int, q_index: int):
    if q_index >= len(TEST_QUESTIONS):
        await finish_test(chat_id)
        return
    q = TEST_QUESTIONS[q_index]
    text = f"*Вопрос {q_index+1} из {len(TEST_QUESTIONS)}*\n\n{q['question']}\n\n"
    for key, val in q["options"].items():
        text += f"{key}) {val}\n"
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=get_test_keyboard(q_index+1, len(TEST_QUESTIONS)))
    if chat_id not in user_temp_data:
        user_temp_data[chat_id] = {}
    user_temp_data[chat_id]["current_question"] = q_index

async def finish_test(chat_id: int):
    answers = user_temp_data.get(chat_id, {}).get("answers", {})
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
    result_text = f"*Результаты теста*\n✅ Правильных: {correct} из {total} ({percent:.1f}%)\n\n"
    for r in results:
        result_text += f"{'✅' if r['is_correct'] else '❌'} {r['question']}\n   Ваш ответ: {r['user_answer']}, Правильный: {r['correct_text']}\n"
    if chat_id not in user_progress:
        user_progress[chat_id] = {"completed_modules": [], "test_results": []}
    user_progress[chat_id]["test_results"].append({
        "date": datetime.now().isoformat(),
        "correct_answers": correct,
        "total_questions": total,
        "percentage": percent,
        "results": results
    })
    save_user_progress(user_progress)
    if chat_id in user_temp_data:
        user_temp_data[chat_id].pop('answers', None)
        user_temp_data[chat_id].pop('current_question', None)
    if chat_id in user_states:
        user_states.pop(chat_id, None)
    await bot.send_message(chat_id=chat_id, text=result_text)
    await show_main_menu(chat_id)

async def show_main_menu(chat_id: int):
    user_states[chat_id] = "main"
    await bot.send_message(chat_id=chat_id, text="Главное меню:", reply_markup=get_main_keyboard(chat_id))

# ========== ОБРАБОТЧИКИ ==========
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    user_id = message.sender.id
    if user_id not in user_progress:
        user_progress[user_id] = {
            'start_date': datetime.now().isoformat(),
            'completed_modules': [],
            'last_module': 0,
            'name': message.sender.name or "Пользователь",
            'audio_listened': [],
            'test_results': []
        }
        save_user_progress(user_progress)
    await message.answer("Добро пожаловать! Используйте кнопки ниже.")
    await show_main_menu(user_id)

@dp.message()
async def handle_text_messages(message: Message):
    user_id = message.sender.id
    text = message.text
    state = user_states.get(user_id)
    if state == "admin_add_user":
        if text.isdigit():
            uid = int(text)
            if access_control.add_paid_user(uid):
                await bot.send_message(chat_id=user_id, text=f"✅ Пользователь {uid} добавлен.")
            else:
                await bot.send_message(chat_id=user_id, text=f"⚠️ Пользователь {uid} уже имеет доступ.")
        else:
            await bot.send_message(chat_id=user_id, text="❌ Ошибка: введите числовой ID.")
        user_states.pop(user_id, None)
        await show_main_menu(user_id)
    elif state == "admin_remove_user":
        if text.isdigit():
            uid = int(text)
            if access_control.remove_paid_user(uid):
                await bot.send_message(chat_id=user_id, text=f"✅ Доступ пользователя {uid} отозван.")
            else:
                await bot.send_message(chat_id=user_id, text=f"⚠️ Пользователь {uid} не найден.")
        else:
            await bot.send_message(chat_id=user_id, text="❌ Ошибка: введите числовой ID.")
        user_states.pop(user_id, None)
        await show_main_menu(user_id)
    else:
        await show_main_menu(user_id)

@dp.callback()
async def handle_callback(cb: CallbackQuery):
    user_id = cb.user.id
    data = cb.data
    state = user_states.get(user_id)

    if data == "back_menu":
        await show_main_menu(user_id)
        await cb.answer()
        return

    # --- Главное меню ---
    if data == "menu_course":
        if not access_control.is_paid_user(user_id):
            await bot.send_message(chat_id=user_id, text="У вас нет доступа. Нажмите 🔓 Получить доступ.")
            await cb.answer()
            return
        await bot.send_message(chat_id=user_id, text="Выберите урок:", reply_markup=get_lessons_list_keyboard())
        user_states[user_id] = "selecting_lesson"
        await cb.answer()
        return

    if data == "menu_audio":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        text = "🎧 Аудиоуроки:\n" + "\n".join([f"{i+1}. {m['title']}" for i,m in enumerate(MODULES) if m.get('has_audio')])
        await bot.send_message(chat_id=user_id, text=text)
        await cb.answer()
        return

    if data == "menu_progress":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        prog = user_progress.get(user_id, {"completed_modules": []})
        completed = len(prog["completed_modules"])
        await bot.send_message(chat_id=user_id, text=f"📊 Прогресс: {completed} из {len(MODULES)} модулей.")
        await cb.answer()
        return

    if data == "menu_contacts":
        c = ADDITIONAL_MATERIALS["contacts"]
        await bot.send_message(chat_id=user_id, text=f"📞 {c['phone']}\n📧 {c['email']}\n🌐 {c['website']}\n📱 {c['telegram']}")
        await cb.answer()
        return

    if data == "menu_links":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        text = "\n".join([f"{name}: {url}" for name, url in ADDITIONAL_MATERIALS["links"].items()])
        await bot.send_message(chat_id=user_id, text=text)
        await cb.answer()
        return

    if data == "menu_help":
        await bot.send_message(chat_id=user_id, text="Используйте кнопки меню. Если бот не отвечает, напишите /start или перезапустите.")
        await cb.answer()
        return

    if data == "menu_test":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        if user_id not in user_temp_data:
            user_temp_data[user_id] = {}
        user_temp_data[user_id]["answers"] = {}
        user_states[user_id] = "taking_test"
        await send_test_question(user_id, 0)
        await cb.answer()
        return

    if data == "menu_test_results":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        tests = user_progress.get(user_id, {}).get("test_results", [])
        if not tests:
            await bot.send_message(chat_id=user_id, text="Вы ещё не проходили тест.")
        else:
            last = tests[-1]
            await bot.send_message(chat_id=user_id, text=f"🏆 Последний результат: {last['correct_answers']}/{last['total_questions']} ({last['percentage']:.1f}%)")
        await cb.answer()
        return

    if data == "menu_mark_all":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        if user_id not in user_progress:
            user_progress[user_id] = {"completed_modules": []}
        user_progress[user_id]["completed_modules"] = list(range(1, len(MODULES)+1))
        save_user_progress(user_progress)
        await bot.send_message(chat_id=user_id, text=f"✅ Все {len(MODULES)} модулей отмечены как пройденные.")
        await cb.answer()
        return

    if data == "menu_checklist":
        if not access_control.is_paid_user(user_id):
            await cb.answer()
            return
        checklist_path = "Чек-лист -Первые 10 шагов в тендерах-.docx"
        if os.path.exists(checklist_path):
            with open(checklist_path, "rb") as f:
                await bot.send_file(chat_id=user_id, file=f.read(), filename="checklist.docx", caption="📥 Чек-лист первых 10 шагов")
        else:
            await bot.send_message(chat_id=user_id, text="Файл чек-листа временно недоступен.")
        await cb.answer()
        return

    if data == "get_access":
        await bot.send_message(chat_id=user_id, text="💳 Стоимость доступа: 3 999 руб.\nОплата по QR-коду:\n(отправьте фото QR или реквизиты)")
        if os.path.exists("qr_code.png"):
            with open("qr_code.png", "rb") as f:
                await bot.send_file(chat_id=user_id, file=f.read(), filename="qr_code.png", caption="QR-код для оплаты")
        if MANAGER_CHAT_ID:
            await bot.send_message(chat_id=MANAGER_CHAT_ID, text=f"🔔 Запрос доступа от {user_id}")
        await cb.answer()
        return

    if data == "about_course":
        await bot.send_message(chat_id=user_id, text="Курс «Тендеры с нуля»: 8 модулей, аудио, тест, чек-лист. Для получения доступа нажмите 🔓 Получить доступ.")
        await cb.answer()
        return

    # --- Выбор урока ---
    if data.startswith("lesson_"):
        lesson_id = int(data.split("_")[1])
        module_index = lesson_id - 1
        if 0 <= module_index < len(MODULES):
            await show_module(user_id, module_index)
        else:
            await bot.send_message(chat_id=user_id, text="Урок не найден.")
        await cb.answer()
        return

    # --- Навигация внутри урока ---
    if state == "viewing_module":
        cur = user_temp_data.get(user_id, {}).get("current_module", 0)
        if data == "prev_lesson":
            if cur > 0:
                await show_module(user_id, cur-1)
            else:
                await bot.send_message(chat_id=user_id, text="Это первый урок.")
        elif data == "next_lesson":
            if cur < len(MODULES)-1:
                await show_module(user_id, cur+1)
            else:
                await bot.send_message(chat_id=user_id, text="🎉 Вы завершили все уроки! Теперь можете пройти тест.")
        elif data == "play_audio":
            if MODULES[cur].get("has_audio"):
                await send_audio_module(user_id, cur)
        elif data == "mark_current":
            module_num = cur + 1
            if user_id not in user_progress:
                user_progress[user_id] = {"completed_modules": []}
            if module_num not in user_progress[user_id]["completed_modules"]:
                user_progress[user_id]["completed_modules"].append(module_num)
                save_user_progress(user_progress)
                await bot.send_message(chat_id=user_id, text=f"✅ Модуль {module_num} отмечен как пройденный.")
            else:
                await bot.send_message(chat_id=user_id, text="Этот модуль уже отмечен.")
        await cb.answer()
        return

    # --- Тест ---
    if state == "taking_test":
        current_q = user_temp_data.get(user_id, {}).get("current_question", 0)
        if data == "test_skip":
            next_q = current_q + 1
            if next_q < len(TEST_QUESTIONS):
                await send_test_question(user_id, next_q)
            else:
                await finish_test(user_id)
        elif data == "test_finish":
            await finish_test(user_id)
        elif data.startswith("test_ans_"):
            answer = data.split("_")[-1]
            if 'answers' not in user_temp_data[user_id]:
                user_temp_data[user_id]['answers'] = {}
            user_temp_data[user_id]['answers'][TEST_QUESTIONS[current_q]["id"]] = answer
            next_q = current_q + 1
            if next_q < len(TEST_QUESTIONS):
                await send_test_question(user_id, next_q)
            else:
                await finish_test(user_id)
        await cb.answer()
        return

    # --- Админ-панель ---
    if data == "admin_panel":
        if not access_control.is_admin(user_id):
            await cb.answer()
            return
        await bot.send_message(chat_id=user_id, text="👑 Управление доступом:", reply_markup=get_admin_keyboard())
        await cb.answer()
        return

    if data == "admin_add_user":
        if not access_control.is_admin(user_id):
            await cb.answer()
            return
        user_states[user_id] = "admin_add_user"
        await bot.send_message(chat_id=user_id, text="Введите ID пользователя (число):")
        await cb.answer()
        return

    if data == "admin_remove_user":
        if not access_control.is_admin(user_id):
            await cb.answer()
            return
        user_states[user_id] = "admin_remove_user"
        await bot.send_message(chat_id=user_id, text="Введите ID пользователя для удаления:")
        await cb.answer()
        return

    if data == "admin_list_users":
        if not access_control.is_admin(user_id):
            await cb.answer()
            return
        users = access_control.get_all_paid_users()
        if users:
            await bot.send_message(chat_id=user_id, text=f"📋 Пользователи с доступом:\n{', '.join(map(str, users))}")
        else:
            await bot.send_message(chat_id=user_id, text="📋 Нет пользователей с доступом.")
        await cb.answer()
        return

    if data == "admin_manage_admins":
        if not access_control.is_admin(user_id):
            await cb.answer()
            return
        await bot.send_message(chat_id=user_id, text="👑 Управление администраторами:\n➕ /add_admin ID\n➖ /remove_admin ID")
        await cb.answer()
        return

    await cb.answer()

# ========== ЗАПУСК FLASK ДЛЯ RENDER ==========
app_flask = Flask(__name__)

@app_flask.route('/')
def health_check():
    return "Bot is running", 200

def run_flask():
    app_flask.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False, use_reloader=False)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
logger.info(f"Health check server started on port {os.environ.get('PORT', 8080)}")

# ========== ЗАПУСК БОТА ==========
async def main():
    try:
        await bot.delete_webhook()
        logger.info("Webhook удалён")
    except Exception as e:
        logger.warning(f"Ошибка удаления вебхука: {e}")
    logger.info("Запуск polling...")
    await dp.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
