import asyncio
import threading
import time
import os
import subprocess
import random
import json
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from cryptography.fernet import Fernet

# Попытка импортировать аппаратное обеспечение
try:
    from hardware import Keypad, LCD, MotionSensor, Relay
    HARDWARE_AVAILABLE = True
except ImportError:
    print("⚠️ Оборудование недоступно. Запуск в режиме отладки.")
    HARDWARE_AVAILABLE = False

#НАСТРОЙКИ
BOT_TOKEN = "7605084706:AAE041dILkRYMPO2ik0js59zcOilwYukytw"
ALLOWED_USER_ID = 1593693874 #1593693874 1052464218
CODES_FILE = "codes.json.enc"
KEY_FILE = "secret.key"

#ОБЪЕКТЫ IRL
lcd = LCD() if HARDWARE_AVAILABLE else None
keypad = Keypad() if HARDWARE_AVAILABLE else None
relay = Relay() if HARDWARE_AVAILABLE else None
motion_sensor = MotionSensor() if HARDWARE_AVAILABLE else None

#СОСТОЯНИЯ
access_codes = {
    "master": {},
    "temp_codes": {},
    "one_time_codes": {}
}

#ГЛОБАЛЬНЫЕ ДЛЯ ОТПРАВКИ
lock_status = False
bot_app = None
bot_loop = None
chat_id_for_motion = None

#ЗАЩИТА JSON
def generate_key():
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)

def load_key():
    if not os.path.exists(KEY_FILE):
        generate_key()
    return open(KEY_FILE, "rb").read()

fernet = Fernet(load_key())

def load_codes():
    global access_codes
    try:
        with open(CODES_FILE, "rb") as f:
            encrypted = f.read()
            decrypted = fernet.decrypt(encrypted)
            access_codes = json.loads(decrypted)
    except FileNotFoundError:
        print("⚠️ Защищённый файл не найден. Используется словарь по умолчанию.")
    except Exception as e:
        print(f"❌ Ошибка загрузки codes.json.enc: {e}")


def save_codes():
    try:
        data = json.dumps(access_codes).encode()
        encrypted = fernet.encrypt(data)
        with open(CODES_FILE, "wb") as f:
            f.write(encrypted)
    except Exception as e:
        print(f"❌ Ошибка сохранения codes.json.enc: {e}")

####################################################ЛОГИКА 
# Защита от перебора PIN-кодов
wrong_attempts = 0
MAX_ATTEMPTS = 5
LOCK_DURATION = 60  #секунд
lock_until = 0

def update_lcd():
    if not lcd:
        return
    if lock_status:
        lcd.display("Door", "OPEN", 1)
    else:
        lcd.display("Door", "CLOSED", 1)

def is_authorized(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

def is_code_valid(code: str) -> bool:
    global wrong_attempts, lock_until

    current_time = time.time()
    if current_time < lock_until:
        if lcd:
            lcd.display("LOCKED", f"{int(lock_until - current_time)}s", delay=2)
        return False

    if code in access_codes["master"]:
        wrong_attempts = 0
        return True

    if code in access_codes["temp_codes"]:
        if access_codes["temp_codes"][code] >= current_time:
            wrong_attempts = 0
            return True
        else:
            del access_codes["temp_codes"][code]
            save_codes()

    if code in access_codes["one_time_codes"]:
        if access_codes["one_time_codes"][code] > 0:
            access_codes["one_time_codes"][code] -= 1
            if access_codes["one_time_codes"][code] == 0:
                del access_codes["one_time_codes"][code]
            save_codes()
            if bot_app and chat_id_for_motion:
                asyncio.run_coroutine_threadsafe(
                    bot_app.bot.send_message(chat_id=chat_id_for_motion,
                                             text=f"🔐 Одноразовый код {code} был использован."),
                    bot_loop
                )
            wrong_attempts = 0
            return True

    # Если дошли до сюда — код неверный
    wrong_attempts += 1
    if wrong_attempts >= MAX_ATTEMPTS:
        lock_until = current_time + LOCK_DURATION
        if lcd:
            lcd.display("LOCKED", f"{LOCK_DURATION}s", delay=2)
        if bot_app and chat_id_for_motion:
            asyncio.run_coroutine_threadsafe(
                bot_app.bot.send_message(chat_id=chat_id_for_motion,
                                         text=f"🚫 5 неудачных попыток. Блокировка на {LOCK_DURATION} секунд."),
                bot_loop
            )
    return False

async def add_master_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        code = context.args[0]
        if isinstance(access_codes["master"], str):
            access_codes["master"] = [access_codes["master"]]
        if code not in access_codes["master"]:
            access_codes["master"].append(code)
            save_codes()
            await update.message.reply_text(f"✅ Добавлен мастер-код: {code}")
        else:
            await update.message.reply_text("⚠️ Такой код уже существует.")
    except:
        await update.message.reply_text("❗ Используй: /addmaster <код>")

async def list_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    now = time.time()

    # Список мастер-кодов
    master = access_codes["master"]
    master_list = [f"{code}" for code in master] if isinstance(master, list) else [master]

    # Список временных кодов
    temp = access_codes["temp_codes"]
    temp_list = [
        f"{code} — истекает через {int(expire - now)} сек"
        for code, expire in temp.items() if expire > now
    ]

    # Список одноразовых кодов
    one_time = access_codes["one_time_codes"]
    one_time_list = [
        f"{code} — осталось {count} использований"
        for code, count in one_time.items() if count > 0
    ]

    result = "\n📋 Активные коды:\n"
    if master_list:
        result += "\n🛡️ Мастер-коды:\n" + "\n".join(master_list)
    if temp_list:
        result += "\n⏱️ Временные коды:\n" + "\n".join(temp_list)
    if one_time_list:
        result += "\n🎯 Одноразовые коды:\n" + "\n".join(one_time_list)
    if not (master_list or temp_list or one_time_list):
        result += "\n❌ Нет активных кодов."

    result += "\n\n✏️ Добавить мастер-код: /addmaster <код>"
    result += "\n🗑️ Удалить код: /delcode <код>"
    await update.message.reply_text(result)

async def delete_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        code = context.args[0]
        removed = False
        if code in access_codes["temp_codes"]:
            del access_codes["temp_codes"][code]
            removed = True
        if code in access_codes["one_time_codes"]:
            del access_codes["one_time_codes"][code]
            removed = True
        if code in access_codes["master"]:
            access_codes["master"].remove(code)
            removed = True
        if removed:
            save_codes()
            await update.message.reply_text(f"🗑️ Код {code} удалён")
        else:
            await update.message.reply_text("❌ Код не найден")
    except:
        await update.message.reply_text("❗ Используй: /delcode <код>")


def unlock_lock():
    global lock_status
    lock_status = True
    if relay:
        relay.off()
    update_lcd()

    if bot_app and chat_id_for_motion and bot_loop:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(
                chat_id=chat_id_for_motion,
                text="🔓 Дверь была открыта."
            ),
            bot_loop
        )

def lock_lock():
    global lock_status
    lock_status = False
    if relay:
        relay.on()
    update_lcd()

    if bot_app and chat_id_for_motion and bot_loop:
        asyncio.run_coroutine_threadsafe(
            bot_app.bot.send_message(
                chat_id=chat_id_for_motion,
                text="🔒 Дверь была закрыта."
            ),
            bot_loop
        )

#ОТПРАВКА ФОТО ПРИ ДВИЖЕНИИ
async def send_motion_photo():
    try:
        image_path = "motion.jpg"
        command = [
            "ffmpeg", "-f", "v4l2", "-input_format", "nv12", "-video_size", "640x480",
            "-i", "/dev/video0", "-frames:v", "1", image_path, "-y"
        ]
        subprocess.run(command, check=True)
        with open(image_path, "rb") as f:
            await bot_app.bot.send_photo(chat_id=chat_id_for_motion, photo=f, caption="📸 Движение обнаружено!")
        os.remove(image_path)
    except Exception as e:
        print(f"❌ Ошибка отправки фото: {e}")

#CALLBACK ДВИЖЕНИЯ
def on_motion_detected():
    print("⚡ Обнаружено движение")
    if lcd:
        lcd.display("Motion", "Detected!", delay=2)
        lcd.display("Enter PIN")

    if HARDWARE_AVAILABLE and bot_app and chat_id_for_motion and bot_loop:
        asyncio.run_coroutine_threadsafe(send_motion_photo(), bot_loop)

if motion_sensor:
    motion_sensor.on_motion(on_motion_detected)

#ВВОД С КЛАВИАТУРы
def keypad_loop():
    if not keypad:
        print("⌨️ Клавиатура отключена")
        return

    lcd.display("Enter PIN") if lcd else None
    code = ""
    while True:
        key = keypad.get_key()
        if key:
            if key == "#":
                if code == "0000":
                    lock_lock()
                    lcd.display("Access", "LOCKED", delay=2) if lcd else print("🔒 Locking")
                elif is_code_valid(code):
                    unlock_lock()
                    lcd.display("Access", "OPENED", delay=2) if lcd else print("✅ Access granted")
                else:
                    lcd.display("Access", "DENIED", delay=2) if lcd else print("❌ Access denied")
                    # Уведомление о неправильном коде
                    if bot_app and chat_id_for_motion and bot_loop:
                        asyncio.run_coroutine_threadsafe(
                            bot_app.bot.send_message(
                                chat_id=chat_id_for_motion,
                                text="❌ Введён неправильный код с клавиатуры."
                            ),
                            bot_loop
                        )
                code = ""
                lcd.display("Enter PIN") if lcd else None

            elif key == "*":
                code = ""  # Стирание текущего ввода
                lcd.display("Enter PIN") if lcd else None

            elif key == "A":
                unlock_lock()
                lcd.display("Manual", "OPEN", delay=2)
                lcd.display("Enter PIN")
                if bot_app and chat_id_for_motion and bot_loop:
                    asyncio.run_coroutine_threadsafe(
                        bot_app.bot.send_message(
                            chat_id=chat_id_for_motion,
                            text="🔓 Замок открыт вручную."
                        ),
                        bot_loop
                    )

            elif key == "B":
                lock_lock()
                lcd.display("Manual", "CLOSE", delay=2)
                lcd.display("Enter PIN")
                if bot_app and chat_id_for_motion and bot_loop:
                    asyncio.run_coroutine_threadsafe(
                        bot_app.bot.send_message(
                            chat_id=chat_id_for_motion,
                            text="🔒 Замок закрыт вручную."
                        ),
                        bot_loop
                    )

            else:
                code += key
                lcd.write_line("*" * len(code), line=1) if lcd else print("*" * len(code))
        time.sleep(0.1)



#ТЕЛЕГРАМ-БОТ

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_id_for_motion
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа.")
        return
    chat_id_for_motion = update.effective_chat.id
    keyboard = [
        [KeyboardButton("🔒 Закрыть замок"), KeyboardButton("🔓 Открыть замок")],
        [KeyboardButton("📷 Фото"), KeyboardButton("🔐 Статус замка")],
        [KeyboardButton("🔑 Временный код"), KeyboardButton("🎲 Одноразовый код")],
        [KeyboardButton("📋 Коды")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("📡 Бот активен. Справка по командам: /help", reply_markup=markup)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update.effective_user.id):
        await update.message.reply_text("🔓 Статус: Открыт." if lock_status else "🔒 Статус: Закрыт.")

async def lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update.effective_user.id):
        lock_lock()
        lcd.display("Enter PIN")
        await update.message.reply_text("🔒 Замок закрыт.")

async def unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authorized(update.effective_user.id):
        unlock_lock()
        lcd.display("Enter PIN")
        await update.message.reply_text("🔓 Замок открыт.")

async def setcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("❌ Нет доступа")
        return
    try:
        code = context.args[0]
        duration = int(context.args[1])
        expire = time.time() + duration * 60
        access_codes["temp_codes"][code] = expire
        save_codes()
        await update.message.reply_text(f"✅ Код {code} активен {duration} минут")
    except:
        await update.message.reply_text("❗ Используй: /setcode <код> <минуты>")

async def generate_one_time_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    code = str(random.randint(100000, 999999))
    access_codes["one_time_codes"][code] = 1
    save_codes()
    await update.message.reply_text(f"🎫 Одноразовый код: {code}\nДействителен 1 раз")

async def photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not HARDWARE_AVAILABLE:
        await update.message.reply_text("📷 Камера недоступна")
        return
    image_path = "photo.jpg"
    try:
        subprocess.run([
            "ffmpeg", "-f", "v4l2", "-input_format", "nv12", "-video_size", "640x480",
            "-i", "/dev/video0", "-frames:v", "1", image_path, "-y"
        ], check=True)
        with open(image_path, "rb") as f:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f)
        os.remove(image_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка камеры: {e}")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    match update.message.text:
        case "🔐 Статус замка": await status(update, context)
        case "🔓 Открыть замок": await unlock(update, context)
        case "🔒 Закрыть замок": await lock(update, context)
        case "📷 Фото": await photo(update, context)
        case "🔑 Временный код": await update.message.reply_text("/setcode <код> <минуты>")
        case "🎲 Одноразовый код": await generate_one_time_code(update, context)
        case "📋 Коды": await list_codes(update, context)
        case _: await update.message.reply_text("❓ Неизвестная команда")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "📖 Справка по командам:\n"
        "\n/start — запуск бота и меню"
        "\n/status — статус замка"
        "\n/lock — закрыть замок"
        "\n/unlock — открыть замок"
        "\n/photo — сделать фото"
        "\n/setcode <код> <минуты> — установить временный код"
        "\n/addmaster <код> — добавить мастер-код"
        "\n/delcode <код> — удалить любой код"
        "\n/codes — список всех активных кодов"
    )


def start_bot():
    global bot_app, bot_loop
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot_app = app
    bot_loop = asyncio.get_event_loop()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("lock", lock))
    app.add_handler(CommandHandler("unlock", unlock))
    app.add_handler(CommandHandler("setcode", setcode))
    app.add_handler(CommandHandler("photo", photo))
    app.add_handler(CommandHandler("codes", list_codes))
    app.add_handler(CommandHandler("addmaster", add_master_code))
    app.add_handler(CommandHandler("delcode", delete_code))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT, text_handler))
    app.run_polling()

#ЗАПУСК
if __name__ == "__main__":
    load_codes()
    if HARDWARE_AVAILABLE:
        threading.Thread(target=keypad_loop, daemon=True).start()
    start_bot()
