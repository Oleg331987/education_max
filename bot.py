import asyncio
import logging
import os
import json
import sys
from datetime import datetime
from dotenv import load_dotenv

from maxapi import Bot, Dispatcher, F
from maxapi.types import BotStarted, Command, MessageCreated

from access_control import AccessControl
from modules_data import MODULES, TEST_QUESTIONS, ADDITIONAL_MATERIALS

from flask import Flask
import threading

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('MAX_BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("MAX_BOT_TOKEN не задан")
    sys.exit(1)

MANAGER_CHAT_ID = int(os.getenv('MANAGER_CHAT_ID', 0))
if not MANAGER_CHAT_ID:
    logger.warning("MANAGER_CHAT_ID не задан")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
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

async def send_audio_module(chat_id: int, module_index: int):
    module = MODULES[module_index]
    audio_path = os.path.join("audio", module["audio_file"])
    if not os.path.exists(audio_path):
        await bot.send_message(chat_id=chat_id, text="❌ Аудиофайл временно недоступен.")
        return
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    caption = f"🎧 {module['emoji']} Аудио к уроку {module_index+1}: {module['title']}"
    from maxapi import InputFile
    await bot.send_document(chat_id=chat_id, document=InputFile(audio_bytes, filename=module["audio_file"]), caption=caption)

async def show_module(chat_id: int, module_index: int):
    module = MODULES[module_index]
    user_states[chat_id] = "viewing_module"
    if chat_id not in user_temp_data:
        user_temp_data[chat_id] = {}
    user_temp_data[chat_id]["current_module"] = module_index
    text = module["content"].replace("<b>", "*").replace("</b>", "*") + "\n\n"
    text += f"*Практическое задание:* {module['task']}\n\n"
    text += "📌 Для навигации используйте команды:\n"
    text += "    /prev   - предыдущий урок\n"
    text += "    /next   - следующий урок\n"
    text += "    /audio  - прослушать аудио ещё раз\n"
    text += "    /done   - отметить текущий модуль пройденным\n"
    text += "    /menu   - вернуться в главное меню\n"
    text += "    /progress - посмотреть прогресс"
    await bot.send_message(chat_id=chat_id, text=text)
    if module.get("has_audio"):
        await send_audio_module(chat_id, module_index)

async def send_test_question(chat_id: int, q_index: int):
    if q_index >= len(TEST_QUESTIONS):
        await finish_test(chat_id)
        return
    q = TEST_QUESTIONS[q_index]
    text = f"*Вопрос {q_index+1} из {len(TEST_QUESTIONS)}*\n\n{q['question']}\n\n"
    for key, val in q["options"].items():
        text += f"{key}) {val}\n"
    text += "\nВведите букву ответа (а, б, в, г) или /skip чтобы пропустить, /finish для досрочного завершения."
    await bot.send_message(chat_id=chat_id, text=text)
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
    is_paid = access_control.is_paid_user(chat_id)
    is_admin = access_control.is_admin(chat_id)
    text = "👋 *Главное меню*\n\n"
    if is_paid:
        text += "📚 *Доступные команды:*\n"
        text += "/menu_course - список уроков\n"
        text += "/audio_list  - список аудиоуроков\n"
        text += "/progress    - мой прогресс\n"
        text += "/contacts    - контакты\n"
        text += "/links       - полезные ссылки\n"
        text += "/help        - помощь\n"
        text += "/test        - пройти тест\n"
        text += "/test_results - результаты теста\n"
        text += "/mark_all    - отметить все модули пройденными\n"
        text += "/checklist   - скачать чек-лист\n"
        if is_admin:
            text += "\n👑 *Админ-команды:*\n"
            text += "/admin       - управление доступом\n"
    else:
        text += "🔓 *Для получения доступа отправьте:* /get_access\n"
        text += "/contacts    - контакты\n"
        text += "/help        - помощь\n"
        text += "/about       - о курсе\n"
    await bot.send_message(chat_id=chat_id, text=text)

# ========== ОБРАБОТЧИКИ (используем event.message.sender.id) ==========
@dp.bot_started()
async def on_bot_started(event: BotStarted):
    await show_main_menu(event.chat_id)  # у BotStarted есть chat_id

@dp.message_created(Command('start'))
async def cmd_start(event: MessageCreated):
    user_id = event.message.sender.id
    if user_id not in user_progress:
        user_progress[user_id] = {
            'start_date': datetime.now().isoformat(),
            'completed_modules': [],
            'last_module': 0,
            'name': event.message.sender.name or "Пользователь",
            'audio_listened': [],
            'test_results': []
        }
        save_user_progress(user_progress)
    await show_main_menu(user_id)

@dp.message_created(Command('menu_course'))
async def cmd_menu_course(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        await bot.send_message(chat_id=user_id, text="Нет доступа. /get_access")
        return
    text = "📚 *Список уроков:*\n\n"
    for i, m in enumerate(MODULES, 1):
        status = "✅" if i in user_progress.get(user_id, {}).get("completed_modules", []) else "⏳"
        text += f"{status} {m['emoji']} *{i}. {m['title']}*\n"
    text += "\nЧтобы открыть урок, отправьте:\n `/lesson N` (где N – номер дня)"
    await bot.send_message(chat_id=user_id, text=text)

@dp.message_created(Command('lesson'))
async def cmd_lesson(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        await bot.send_message(chat_id=user_id, text="Нет доступа")
        return
    args = event.message.body.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.send_message(chat_id=user_id, text="Используйте: /lesson N (1-8)")
        return
    n = int(args[1])
    if n < 1 or n > len(MODULES):
        await bot.send_message(chat_id=user_id, text=f"Номер от 1 до {len(MODULES)}")
        return
    await show_module(user_id, n-1)

@dp.message_created(Command('prev'))
async def cmd_prev(event: MessageCreated):
    user_id = event.message.sender.id
    if user_states.get(user_id) != "viewing_module":
        await bot.send_message(chat_id=user_id, text="Вы не просматриваете урок")
        return
    cur = user_temp_data.get(user_id, {}).get("current_module", 0)
    if cur > 0:
        await show_module(user_id, cur-1)
    else:
        await bot.send_message(chat_id=user_id, text="Это первый урок.")

@dp.message_created(Command('next'))
async def cmd_next(event: MessageCreated):
    user_id = event.message.sender.id
    if user_states.get(user_id) != "viewing_module":
        await bot.send_message(chat_id=user_id, text="Вы не просматриваете урок")
        return
    cur = user_temp_data.get(user_id, {}).get("current_module", 0)
    if cur < len(MODULES)-1:
        await show_module(user_id, cur+1)
    else:
        await bot.send_message(chat_id=user_id, text="🎉 Вы завершили последний урок!")

@dp.message_created(Command('audio'))
async def cmd_audio(event: MessageCreated):
    user_id = event.message.sender.id
    if user_states.get(user_id) != "viewing_module":
        await bot.send_message(chat_id=user_id, text="Вы не просматриваете урок")
        return
    cur = user_temp_data.get(user_id, {}).get("current_module", 0)
    if MODULES[cur].get("has_audio"):
        await send_audio_module(user_id, cur)
    else:
        await bot.send_message(chat_id=user_id, text="У этого урока нет аудио.")

@dp.message_created(Command('done'))
async def cmd_done(event: MessageCreated):
    user_id = event.message.sender.id
    if user_states.get(user_id) != "viewing_module":
        await bot.send_message(chat_id=user_id, text="Вы не просматриваете урок")
        return
    cur = user_temp_data.get(user_id, {}).get("current_module", 0)
    module_num = cur + 1
    if user_id not in user_progress:
        user_progress[user_id] = {"completed_modules": []}
    if module_num not in user_progress[user_id]["completed_modules"]:
        user_progress[user_id]["completed_modules"].append(module_num)
        save_user_progress(user_progress)
        await bot.send_message(chat_id=user_id, text=f"✅ Модуль {module_num} отмечен.")
    else:
        await bot.send_message(chat_id=user_id, text="Уже отмечен.")

@dp.message_created(Command('audio_list'))
async def cmd_audio_list(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        return
    text = "🎧 *Аудиоуроки:*\n"
    for i, m in enumerate(MODULES, 1):
        if m.get('has_audio'):
            text += f"{i}. {m['title']}\n"
    await bot.send_message(chat_id=user_id, text=text)

@dp.message_created(Command('progress'))
async def cmd_progress(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        await bot.send_message(chat_id=user_id, text="Нет доступа")
        return
    prog = user_progress.get(user_id, {"completed_modules": []})
    completed = len(prog["completed_modules"])
    await bot.send_message(chat_id=user_id, text=f"📊 Прогресс: {completed} из {len(MODULES)}")

@dp.message_created(Command('contacts'))
async def cmd_contacts(event: MessageCreated):
    c = ADDITIONAL_MATERIALS["contacts"]
    await bot.send_message(chat_id=event.message.sender.id, text=f"📞 {c['phone']}\n📧 {c['email']}\n🌐 {c['website']}\n📱 {c['telegram']}")

@dp.message_created(Command('links'))
async def cmd_links(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        return
    text = "\n".join([f"{name}: {url}" for name, url in ADDITIONAL_MATERIALS["links"].items()])
    await bot.send_message(chat_id=user_id, text=text)

@dp.message_created(Command('help'))
async def cmd_help(event: MessageCreated):
    await show_main_menu(event.message.sender.id)

@dp.message_created(Command('test'))
async def cmd_test(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        await bot.send_message(chat_id=user_id, text="Нет доступа")
        return
    user_states[user_id] = "taking_test"
    if user_id not in user_temp_data:
        user_temp_data[user_id] = {}
    user_temp_data[user_id]["answers"] = {}
    await send_test_question(user_id, 0)

@dp.message_created(Command('test_results'))
async def cmd_test_results(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        return
    tests = user_progress.get(user_id, {}).get("test_results", [])
    if not tests:
        await bot.send_message(chat_id=user_id, text="Вы ещё не проходили тест.")
    else:
        last = tests[-1]
        await bot.send_message(chat_id=user_id, text=f"🏆 Последний результат: {last['correct_answers']}/{last['total_questions']} ({last['percentage']:.1f}%)")

@dp.message_created(Command('mark_all'))
async def cmd_mark_all(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        return
    if user_id not in user_progress:
        user_progress[user_id] = {"completed_modules": []}
    user_progress[user_id]["completed_modules"] = list(range(1, len(MODULES)+1))
    save_user_progress(user_progress)
    await bot.send_message(chat_id=user_id, text=f"✅ Все {len(MODULES)} модулей отмечены.")

@dp.message_created(Command('checklist'))
async def cmd_checklist(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_paid_user(user_id):
        return
    checklist_path = "Чек-лист -Первые 10 шагов в тендерах-.docx"
    if os.path.exists(checklist_path):
        with open(checklist_path, "rb") as f:
            from maxapi import InputFile
            await bot.send_document(chat_id=user_id, document=InputFile(f.read(), filename="checklist.docx"), caption="📥 Чек-лист")
    else:
        await bot.send_message(chat_id=user_id, text="Файл чек-листа недоступен.")

@dp.message_created(Command('get_access'))
async def cmd_get_access(event: MessageCreated):
    user_id = event.message.sender.id
    await bot.send_message(chat_id=user_id, text="💳 Стоимость доступа: 3 999 руб.\nОплата по QR-коду:")
    if os.path.exists("qr_code.png"):
        with open("qr_code.png", "rb") as f:
            from maxapi import InputFile
            await bot.send_photo(chat_id=user_id, photo=InputFile(f.read(), filename="qr_code.png"), caption="QR-код")
    if MANAGER_CHAT_ID:
        await bot.send_message(chat_id=MANAGER_CHAT_ID, text=f"🔔 Запрос доступа от {user_id}")

@dp.message_created(Command('about'))
async def cmd_about(event: MessageCreated):
    await bot.send_message(chat_id=event.message.sender.id, text="Курс «Тендеры с нуля»: 8 модулей, аудио, тест, чек-лист. /get_access")

@dp.message_created(Command('admin'))
async def cmd_admin(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_admin(user_id):
        await bot.send_message(chat_id=user_id, text="Нет прав")
        return
    text = "👑 *Админ-панель*\n\n"
    text += "/add_user <ID>\n/remove_user <ID>\n/list_users\n/add_admin <ID>\n/remove_admin <ID>"
    await bot.send_message(chat_id=user_id, text=text)

@dp.message_created(Command('add_user'))
async def cmd_add_user(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_admin(user_id):
        return
    args = event.message.body.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.send_message(chat_id=user_id, text="Используйте: /add_user ID")
        return
    uid = int(args[1])
    if access_control.add_paid_user(uid):
        await bot.send_message(chat_id=user_id, text=f"✅ Пользователь {uid} добавлен.")
    else:
        await bot.send_message(chat_id=user_id, text=f"⚠️ Уже имеет доступ.")

@dp.message_created(Command('remove_user'))
async def cmd_remove_user(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_admin(user_id):
        return
    args = event.message.body.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.send_message(chat_id=user_id, text="Используйте: /remove_user ID")
        return
    uid = int(args[1])
    if access_control.remove_paid_user(uid):
        await bot.send_message(chat_id=user_id, text=f"✅ Доступ отозван.")
    else:
        await bot.send_message(chat_id=user_id, text=f"⚠️ Пользователь не найден.")

@dp.message_created(Command('list_users'))
async def cmd_list_users(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_admin(user_id):
        return
    users = access_control.get_all_paid_users()
    if users:
        await bot.send_message(chat_id=user_id, text=f"📋 Пользователи:\n{', '.join(map(str, users))}")
    else:
        await bot.send_message(chat_id=user_id, text="📋 Нет пользователей.")

@dp.message_created(Command('add_admin'))
async def cmd_add_admin(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_admin(user_id):
        return
    args = event.message.body.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.send_message(chat_id=user_id, text="Используйте: /add_admin ID")
        return
    uid = int(args[1])
    if access_control.add_admin(uid):
        await bot.send_message(chat_id=user_id, text=f"✅ Администратор добавлен.")
    else:
        await bot.send_message(chat_id=user_id, text=f"⚠️ Уже администратор.")

@dp.message_created(Command('remove_admin'))
async def cmd_remove_admin(event: MessageCreated):
    user_id = event.message.sender.id
    if not access_control.is_admin(user_id):
        return
    args = event.message.body.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await bot.send_message(chat_id=user_id, text="Используйте: /remove_admin ID")
        return
    uid = int(args[1])
    if access_control.remove_admin(uid):
        await bot.send_message(chat_id=user_id, text=f"✅ Администратор удалён.")
    else:
        await bot.send_message(chat_id=user_id, text=f"⚠️ Не был администратором.")

@dp.message_created()
async def handle_text_in_test(event: MessageCreated):
    user_id = event.message.sender.id
    state = user_states.get(user_id)
    if state != "taking_test":
        return
    text = event.message.body.text.strip().lower()
    if text == "/skip":
        current_q = user_temp_data.get(user_id, {}).get("current_question", 0)
        next_q = current_q + 1
        if next_q < len(TEST_QUESTIONS):
            await send_test_question(user_id, next_q)
        else:
            await finish_test(user_id)
        return
    if text == "/finish":
        await finish_test(user_id)
        return
    if text in ("а", "б", "в", "г"):
        current_q = user_temp_data.get(user_id, {}).get("current_question", 0)
        if current_q >= len(TEST_QUESTIONS):
            return
        if "answers" not in user_temp_data[user_id]:
            user_temp_data[user_id]["answers"] = {}
        user_temp_data[user_id]["answers"][TEST_QUESTIONS[current_q]["id"]] = text
        next_q = current_q + 1
        if next_q < len(TEST_QUESTIONS):
            await send_test_question(user_id, next_q)
        else:
            await finish_test(user_id)
        return
    else:
        await bot.send_message(chat_id=user_id, text="Некорректный ввод. Отправьте букву а, б, в, г или /skip, /finish")

# ========== HEALTH CHECK ==========
app_flask = Flask(__name__)

@app_flask.route('/')
def health_check():
    return "Bot is running", 200

def run_flask():
    app_flask.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False, use_reloader=False)

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()
logger.info(f"Health check started on port {os.environ.get('PORT', 8080)}")

async def main():
    try:
        await bot.delete_webhook()
        logger.info("Webhook удалён")
    except Exception as e:
        logger.warning(f"Ошибка удаления вебхука: {e}")
    logger.info("Запуск polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
