import asyncio
import logging
import os
import json
from datetime import datetime
from dotenv import load_dotenv

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, BotStarted
from maxapi import F

from access_control import AccessControl
from modules_data import MODULES, TEST_QUESTIONS, ADDITIONAL_MATERIALS

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === Конфигурация ===
BOT_TOKEN = os.getenv('MAX_BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("MAX_BOT_TOKEN не задан в .env")
    exit(1)

MANAGER_CHAT_ID = int(os.getenv('MANAGER_CHAT_ID', 0))
if not MANAGER_CHAT_ID:
    logger.warning("MANAGER_CHAT_ID не задан, уведомления о платежах не будут отправляться")

# === Инициализация ===
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

# ========== КЛАВИАТУРЫ ==========
from maxapi.types import ReplyKeyboardMarkup, KeyboardButton

def get_main_keyboard(user_id: int):
    is_paid = access_control.is_paid_user(user_id)
    is_admin = access_control.is_admin(user_id)
    buttons = []
    if is_paid:
        buttons.append([KeyboardButton(text="📚 Меню курса"), KeyboardButton(text="🎧 Аудио уроки")])
        buttons.append([KeyboardButton(text="📊 Мой прогресс"), KeyboardButton(text="📞 Контакты")])
        buttons.append([KeyboardButton(text="🔗 Полезные ссылки"), KeyboardButton(text="🆘 Помощь")])
        buttons.append([KeyboardButton(text="📝 Пройти тест"), KeyboardButton(text="🏆 Результаты теста")])
        buttons.append([KeyboardButton(text="✅ Отметить все модули"), KeyboardButton(text="📥 Скачать чек-лист")])
        if is_admin:
            buttons.append([KeyboardButton(text="👥 Управление доступом")])
    else:
        buttons.append([KeyboardButton(text="🔓 Получить доступ"), KeyboardButton(text="📞 Контакты")])
        buttons.append([KeyboardButton(text="🆘 Помощь"), KeyboardButton(text="ℹ️ О курсе")])
    buttons.append([KeyboardButton(text="◀️ Назад в меню")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_lessons_list_keyboard():
    buttons = []
    for m in MODULES:
        buttons.append([KeyboardButton(text=f"{m['emoji']} День {m['day']}: {m['title'][:20]}")])
    buttons.append([KeyboardButton(text="📊 Мой прогресс"), KeyboardButton(text="◀️ Назад в меню")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_lesson_navigation_keyboard(current_index: int, total: int):
    buttons = [
        [KeyboardButton(text="⬅️ Предыдущий урок"), KeyboardButton(text=f"📖 {current_index+1}/{total}"), KeyboardButton(text="Следующий урок ➡️")],
        [KeyboardButton(text="🎧 Прослушать аудио"), KeyboardButton(text="✅ Отметить текущий модуль")],
        [KeyboardButton(text="📚 Меню курса"), KeyboardButton(text="📊 Мой прогресс"), KeyboardButton(text="◀️ Назад в меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_test_keyboard(current_num: int, total: int):
    buttons = [
        [KeyboardButton(text="а"), KeyboardButton(text="б")],
        [KeyboardButton(text="в"), KeyboardButton(text="г")],
        [KeyboardButton(text="⏭ Пропустить"), KeyboardButton(text="🏁 Завершить тест"), KeyboardButton(text=f"❓ {current_num}/{total}")],
        [KeyboardButton(text="◀️ Назад в меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_keyboard():
    buttons = [
        [KeyboardButton(text="➕ Добавить пользователя"), KeyboardButton(text="➖ Удалить пользователя")],
        [KeyboardButton(text="📋 Список пользователей"), KeyboardButton(text="👑 Управление админами")],
        [KeyboardButton(text="◀️ Назад в меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def send_audio_module(bot: Bot, chat_id: int, module_index: int):
    module = MODULES[module_index]
    audio_path = os.path.join("audio", module["audio_file"])
    if not os.path.exists(audio_path):
        await bot.send_message(chat_id, "❌ Аудиофайл временно недоступен.")
        return
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    caption = f"🎧 {module['emoji']} Аудио к уроку {module_index+1}: {module['title']}\nПосле прослушивания нажмите «✅ Отметить текущий модуль»."
    await bot.send_document(chat_id, audio_bytes, filename=module["audio_file"], caption=caption)

async def show_module(bot: Bot, chat_id: int, module_index: int):
    module = MODULES[module_index]
    user_states[chat_id] = {"mode": "viewing_module", "current_module": module_index}
    text = module["content"].replace("<b>", "*").replace("</b>", "*") + "\n\n"
    text += f"*Практическое задание:* {module['task']}"
    await bot.send_message(chat_id, text)
    if module.get("has_audio"):
        await send_audio_module(bot, chat_id, module_index)
    await bot.send_message(chat_id, "Навигация:", reply_markup=get_lesson_navigation_keyboard(module_index, len(MODULES)))

async def send_test_question(bot: Bot, chat_id: int, q_index: int):
    state = user_states.get(chat_id)
    if not state or state.get("mode") != "taking_test":
        return
    if q_index >= len(TEST_QUESTIONS):
        await finish_test(bot, chat_id)
        return
    q = TEST_QUESTIONS[q_index]
    text = f"*Вопрос {q_index+1} из {len(TEST_QUESTIONS)}*\n\n{q['question']}\n\n"
    for key, val in q["options"].items():
        text += f"{key}) {val}\n"
    await bot.send_message(chat_id, text, reply_markup=get_test_keyboard(q_index+1, len(TEST_QUESTIONS)))
    state["current_question"] = q_index

async def finish_test(bot: Bot, chat_id: int):
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
    await bot.send_message(chat_id, result_text)
    user_states[chat_id] = {"mode": "main"}
    await show_main_menu(bot, chat_id)

async def show_main_menu(bot: Bot, chat_id: int):
    user_states[chat_id] = {"mode": "main"}
    await bot.send_message(chat_id, "Главное меню:", reply_markup=get_main_keyboard(chat_id))

# ========== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ==========
def register_handlers(dp: Dispatcher, bot: Bot):
    @dp.bot_started()
    async def on_start(event: BotStarted):
        await show_main_menu(bot, event.chat_id)

    @dp.message_created(F.text == "◀️ Назад в меню")
    async def back_to_menu(event: MessageCreated):
        await show_main_menu(bot, event.chat.chat_id)

    @dp.message_created(F.text == "🔓 Получить доступ")
    async def handle_payment(event: MessageCreated):
        chat_id = event.chat.chat_id
        await bot.send_message(chat_id, "💳 Стоимость доступа: 3 999 руб.\nОплата по QR-коду:\n(отправьте фото QR или реквизиты)")
        if os.path.exists("qr_code.png"):
            with open("qr_code.png", "rb") as f:
                await bot.send_photo(chat_id, f.read(), caption="QR-код для оплаты")
        if MANAGER_CHAT_ID:
            await bot.send_message(MANAGER_CHAT_ID, f"🔔 Запрос доступа от {chat_id}")

    @dp.message_created(F.text == "ℹ️ О курсе")
    async def about_course(event: MessageCreated):
        await bot.send_message(event.chat.chat_id, "Курс «Тендеры с нуля»: 8 модулей, аудио, тест, чек-лист. Для получения доступа нажмите 🔓 Получить доступ.")

    @dp.message_created(F.text == "📞 Контакты")
    async def contacts(event: MessageCreated):
        c = ADDITIONAL_MATERIALS["contacts"]
        await bot.send_message(event.chat.chat_id, f"📞 {c['phone']}\n📧 {c['email']}\n🌐 {c['website']}\n📱 {c['telegram']}")

    @dp.message_created(F.text == "🆘 Помощь")
    async def help_command(event: MessageCreated):
        await bot.send_message(event.chat.chat_id, "Используйте кнопки меню. Если бот не отвечает, напишите /start или перезапустите.")

    @dp.message_created(F.text == "📚 Меню курса")
    async def course_menu(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            await bot.send_message(chat_id, "У вас нет доступа. Нажмите 🔓 Получить доступ.")
            return
        await bot.send_message(chat_id, "Выберите урок:", reply_markup=get_lessons_list_keyboard())
        user_states[chat_id] = {"mode": "selecting_lesson"}

    @dp.message_created(F.text == "🎧 Аудио уроки")
    async def audio_list(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        text = "🎧 Аудиоуроки:\n" + "\n".join([f"{i+1}. {m['title']}" for i,m in enumerate(MODULES) if m.get('has_audio')])
        await bot.send_message(chat_id, text)

    @dp.message_created(F.text == "📊 Мой прогресс")
    async def my_progress(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        prog = user_progress.get(chat_id, {"completed_modules": []})
        completed = len(prog["completed_modules"])
        await bot.send_message(chat_id, f"📊 Прогресс: {completed} из {len(MODULES)} модулей.")

    @dp.message_created(F.text == "🔗 Полезные ссылки")
    async def useful_links(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        text = "\n".join([f"{name}: {url}" for name, url in ADDITIONAL_MATERIALS["links"].items()])
        await bot.send_message(chat_id, text)

    @dp.message_created(F.text == "📝 Пройти тест")
    async def start_test_command(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        user_states[chat_id] = {
            "mode": "taking_test",
            "current_question": 0,
            "answers": {},
            "skipped": []
        }
        await send_test_question(bot, chat_id, 0)

    @dp.message_created(F.text == "🏆 Результаты теста")
    async def test_results(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        tests = user_progress.get(chat_id, {}).get("test_results", [])
        if not tests:
            await bot.send_message(chat_id, "Вы ещё не проходили тест.")
        else:
            last = tests[-1]
            await bot.send_message(chat_id, f"🏆 Последний результат: {last['correct_answers']}/{last['total_questions']} ({last['percentage']:.1f}%)")

    @dp.message_created(F.text == "✅ Отметить все модули")
    async def mark_all_modules(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        if chat_id not in user_progress:
            user_progress[chat_id] = {"completed_modules": []}
        user_progress[chat_id]["completed_modules"] = list(range(1, len(MODULES)+1))
        save_user_progress(user_progress)
        await bot.send_message(chat_id, f"✅ Все {len(MODULES)} модулей отмечены как пройденные.")

    @dp.message_created(F.text == "📥 Скачать чек-лист")
    async def download_checklist(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_paid_user(chat_id):
            return
        checklist_path = "Чек-лист -Первые 10 шагов в тендерах-.docx"
        if os.path.exists(checklist_path):
            with open(checklist_path, "rb") as f:
                await bot.send_document(chat_id, f.read(), filename="checklist.docx", caption="📥 Чек-лист первых 10 шагов")
        else:
            await bot.send_message(chat_id, "Файл чек-листа временно недоступен.")

    @dp.message_created()
    async def select_lesson(event: MessageCreated):
        chat_id = event.chat.chat_id
        text = event.message.body.text
        state = user_states.get(chat_id, {})
        if state.get("mode") == "selecting_lesson":
            for i, m in enumerate(MODULES):
                if text.startswith(m['emoji']) or m['title'] in text:
                    await show_module(bot, chat_id, i)
                    return

    @dp.message_created(F.text == "⬅️ Предыдущий урок")
    async def prev_lesson(event: MessageCreated):
        chat_id = event.chat.chat_id
        state = user_states.get(chat_id, {})
        if state.get("mode") == "viewing_module":
            cur = state.get("current_module", 0)
            if cur > 0:
                await show_module(bot, chat_id, cur-1)
            else:
                await bot.send_message(chat_id, "Это первый урок.")

    @dp.message_created(F.text == "Следующий урок ➡️")
    async def next_lesson(event: MessageCreated):
        chat_id = event.chat.chat_id
        state = user_states.get(chat_id, {})
        if state.get("mode") == "viewing_module":
            cur = state.get("current_module", 0)
            if cur < len(MODULES)-1:
                await show_module(bot, chat_id, cur+1)
            else:
                await bot.send_message(chat_id, "🎉 Вы завершили все уроки! Теперь можете пройти тест.")

    @dp.message_created(F.text == "🎧 Прослушать аудио")
    async def replay_audio(event: MessageCreated):
        chat_id = event.chat.chat_id
        state = user_states.get(chat_id, {})
        if state.get("mode") == "viewing_module":
            cur = state.get("current_module", 0)
            await send_audio_module(bot, chat_id, cur)

    @dp.message_created(F.text == "✅ Отметить текущий модуль")
    async def mark_current_lesson(event: MessageCreated):
        chat_id = event.chat.chat_id
        state = user_states.get(chat_id, {})
        if state.get("mode") == "viewing_module":
            cur = state.get("current_module", 0)
            module_num = cur + 1
            if chat_id not in user_progress:
                user_progress[chat_id] = {"completed_modules": []}
            if module_num not in user_progress[chat_id]["completed_modules"]:
                user_progress[chat_id]["completed_modules"].append(module_num)
                save_user_progress(user_progress)
                await bot.send_message(chat_id, f"✅ Модуль {module_num} отмечен как пройденный.")
            else:
                await bot.send_message(chat_id, "Этот модуль уже отмечен.")

    @dp.message_created(F.text.in_({"а", "б", "в", "г"}))
    async def test_answer(event: MessageCreated):
        chat_id = event.chat.chat_id
        answer = event.message.body.text
        state = user_states.get(chat_id, {})
        if state.get("mode") != "taking_test":
            return
        q_index = state.get("current_question", 0)
        if q_index >= len(TEST_QUESTIONS):
            return
        state["answers"][TEST_QUESTIONS[q_index]["id"]] = answer
        next_q = q_index + 1
        if next_q < len(TEST_QUESTIONS):
            await send_test_question(bot, chat_id, next_q)
        else:
            await finish_test(bot, chat_id)

    @dp.message_created(F.text == "⏭ Пропустить")
    async def skip_question(event: MessageCreated):
        chat_id = event.chat.chat_id
        state = user_states.get(chat_id, {})
        if state.get("mode") == "taking_test":
            next_q = state.get("current_question", 0) + 1
            if next_q < len(TEST_QUESTIONS):
                await send_test_question(bot, chat_id, next_q)
            else:
                await finish_test(bot, chat_id)

    @dp.message_created(F.text == "🏁 Завершить тест")
    async def force_finish_test(event: MessageCreated):
        chat_id = event.chat.chat_id
        state = user_states.get(chat_id, {})
        if state.get("mode") == "taking_test":
            await finish_test(bot, chat_id)

    @dp.message_created(F.text == "👥 Управление доступом")
    async def admin_panel(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_admin(chat_id):
            return
        await bot.send_message(chat_id, "👑 Управление доступом:", reply_markup=get_admin_keyboard())
        user_states[chat_id] = {"mode": "admin_panel"}

    @dp.message_created(F.text == "➕ Добавить пользователя")
    async def admin_add_user_start(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_admin(chat_id):
            return
        user_states[chat_id] = {"mode": "admin_add_user"}
        await bot.send_message(chat_id, "Введите ID пользователя (число):")

    @dp.message_created(F.text == "➖ Удалить пользователя")
    async def admin_remove_user_start(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_admin(chat_id):
            return
        user_states[chat_id] = {"mode": "admin_remove_user"}
        await bot.send_message(chat_id, "Введите ID пользователя для удаления:")

    @dp.message_created(F.text == "📋 Список пользователей")
    async def admin_list_users(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_admin(chat_id):
            return
        users = access_control.get_all_paid_users()
        await bot.send_message(chat_id, f"📋 Пользователи с доступом:\n{', '.join(map(str, users))}")

    @dp.message_created(F.text == "👑 Управление админами")
    async def admin_manage_admins(event: MessageCreated):
        chat_id = event.chat.chat_id
        if not access_control.is_admin(chat_id):
            return
        await bot.send_message(chat_id, "Используйте команды:\n/add_admin ID\n/remove_admin ID")

    @dp.message_created()
    async def handle_admin_input(event: MessageCreated):
        chat_id = event.chat.chat_id
        text = event.message.body.text.strip()
        state = user_states.get(chat_id, {})
        mode = state.get("mode")
        if mode == "admin_add_user":
            if text.isdigit():
                uid = int(text)
                if access_control.add_paid_user(uid):
                    await bot.send_message(chat_id, f"✅ Пользователь {uid} добавлен.")
                else:
                    await bot.send_message(chat_id, f"⚠️ Пользователь {uid} уже имеет доступ.")
            else:
                await bot.send_message(chat_id, "❌ Ошибка: введите числовой ID.")
            user_states[chat_id] = {"mode": "main"}
            await show_main_menu(bot, chat_id)
        elif mode == "admin_remove_user":
            if text.isdigit():
                uid = int(text)
                if access_control.remove_paid_user(uid):
                    await bot.send_message(chat_id, f"✅ Доступ пользователя {uid} отозван.")
                else:
                    await bot.send_message(chat_id, f"⚠️ Пользователь {uid} не найден.")
            else:
                await bot.send_message(chat_id, "❌ Ошибка: введите числовой ID.")
            user_states[chat_id] = {"mode": "main"}
            await show_main_menu(bot, chat_id)

# ========== ЗАПУСК ==========
async def main():
    # Создаём бота и диспетчер
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    register_handlers(dp, bot)
    
    try:
        await bot.delete_webhook()
        logger.info("Webhook удалён")
    except Exception as e:
        logger.warning(f"Ошибка удаления вебхука: {e}")
    
    logger.info("Запуск polling...")
    # Бесконечный цикл с перезапуском при ошибках
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as e:
            logger.error(f"Polling упал: {e}")
            logger.info("Перезапуск через 5 секунд...")
            await asyncio.sleep(5)
            # Пересоздаём бота, чтобы обновить сессию
            bot = Bot(token=BOT_TOKEN)
            dp = Dispatcher()
            register_handlers(dp, bot)
            continue
        break

if __name__ == "__main__":
    asyncio.run(main())
