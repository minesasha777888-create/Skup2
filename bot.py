# bot.py
import logging
import aiosqlite
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
import os

load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("Установите BOT_TOKEN в переменных окружения или в файле .env")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "submissions.db"

# В памяти: когда админ нажал "Ответить" — ждём от него текста оценки
awaiting_reply = {}  # {admin_user_id: submission_id}

class Form(StatesGroup):
    name = State()
    quantity = State()
    url = State()
    unpacked = State()
    city = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            '''CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                user_name TEXT,
                name TEXT,
                quantity TEXT,
                url TEXT,
                unpacked TEXT,
                city TEXT,
                status TEXT DEFAULT 'new',
                admin_id INTEGER,
                admin_comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        await db.execute(
            '''CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )'''
        )
        await db.commit()

# --- Keyboards ---
def main_menu_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Оставить заявку")],
            [KeyboardButton(text="Поддержка"), KeyboardButton(text="Отзывы")]
        ],
        resize_keyboard=True
    )
    return kb

def make_submission_keyboard(submission_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ответить клиенту", callback_data=f"reply_{submission_id}")]
    ])
    return kb

# --- Helpers for config ---
async def set_config(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO config(key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def get_config(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

# --- Handlers ---
@dp.message(Command(commands=["start"]))
async def cmd_start(message: types.Message):
    text = (
        "Добро пожаловать в бота SkupFast!\n"
        "Если ты хочешь быстро продать свой товар — ты попал по адресу 👇\n\n"
        "Нажми «Оставить заявку», чтобы начать."
    )
    await message.answer(text, reply_markup=main_menu_keyboard())

@dp.message(lambda m: m.text == "Оставить заявку")
async def start_form(message: types.Message, state: FSMContext):
    await message.answer("Введите название товара:")
    await state.set_state(Form.name)

@dp.message(Form.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Количество товара (число или описание):")
    await state.set_state(Form.quantity)

@dp.message(Form.quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    await state.update_data(quantity=message.text)
    await message.answer("Ссылка на товар (если есть), либо напишите '-' :")
    await state.set_state(Form.url)

@dp.message(Form.url)
async def process_url(message: types.Message, state: FSMContext):
    await state.update_data(url=message.text)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton("Да"), KeyboardButton("Нет")]], resize_keyboard=True)
    await message.answer("Распакован ли товар? (Да/Нет)", reply_markup=kb)
    await state.set_state(Form.unpacked)

@dp.message(Form.unpacked)
async def process_unpacked(message: types.Message, state: FSMContext):
    await state.update_data(unpacked=message.text)
    await message.answer("Укажите город, где находится товар:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Form.city)

@dp.message(Form.city)
async def process_city(message: types.Message, state: FSMContext):
    data = await state.get_data()
    data['city'] = message.text

    # Save submission to DB
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO submissions (user_id, user_name, name, quantity, url, unpacked, city) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message.from_user.id, message.from_user.full_name, data['name'], data['quantity'], data['url'], data['unpacked'], data['city'])
        )
        await db.commit()
        submission_id = cur.lastrowid

    # Send to manager chat
    manager_chat = await get_config("manager_chat_id")
    submission_text = (
        f"📥 <b>Новая заявка #{submission_id}</b>\n\n"
        f"<b>Пользователь:</b> {message.from_user.full_name} (id: <code>{message.from_user.id}</code>)\n"
        f"<b>Название:</b> {data['name']}\n"
        f"<b>Количество:</b> {data['quantity']}\n"
        f"<b>Ссылка:</b> {data['url']}\n"
        f"<b>Распакован:</b> {data['unpacked']}\n"
        f"<b>Город:</b> {data['city']}\n"
    )
    if manager_chat:
        await bot.send_message(chat_id=int(manager_chat), text=submission_text, reply_markup=make_submission_keyboard(submission_id))
    else:
        await message.answer("Заявка получена, но менеджерский чат не настроен. Пожалуйста, уведомите администратора.")
        owner_id = await get_config("owner_id")
        if owner_id:
            await bot.send_message(chat_id=int(owner_id), text=f"Новая заявка #{submission_id} (менеджерский чат не настроен):\n{submission_text}")

    await message.answer("Благодарим вас за анкету!\nМенеджер проверит и даст оценку вашего товара в течение 15 минут.")
    await state.clear()

@dp.message(lambda m: m.text == "Поддержка")
async def support_button(message: types.Message):
    support_username = await get_config("support_username")
    if support_username:
        await message.answer(f"Поддержка: @{support_username}")
    else:
        await message.answer("Поддержка: @skupfast")

@dp.message(lambda m: m.text == "Отзывы")
async def reviews_button(message: types.Message):
    reviews = await get_config("reviews_link")
    if reviews:
        await message.answer(f"Отзывы: {reviews}")
    else:
        await message.answer("Отзывы ещё не настроены.")

# --- Admin & setup commands ---
@dp.message(Command(commands=["register_admin"]))
async def cmd_register_admin(message: types.Message):
    # Регистрируем пользователя, который выполнил команду, как владельца/админа
    await set_config("owner_id", str(message.from_user.id))
    await set_config("support_username", "skupfast")  # можно изменить позже
    await message.answer("Вы зарегистрированы как администратор (владелец). Теперь выполните /set_manager_chat в чате менеджеров.")

@dp.message(Command(commands=["set_manager_chat"]))
async def cmd_set_manager_chat(message: types.Message):
    # Эту команду нужно выполнить в том чате, куда будут приходить заявки (группа/канал)
    chat_id = message.chat.id
    await set_config("manager_chat_id", str(chat_id))
    await message.answer(f"Менеджерский чат сохранён: {chat_id}")

@dp.message(Command(commands=["set_reviews"]))
async def cmd_set_reviews(message: types.Message):
    # usage: /set_reviews https://t.me/your_reviews
    args = message.get_args()
    if not args:
        await message.answer("Использование: /set_reviews <ссылка>")
        return
    await set_config("reviews_link", args)
    await message.answer("Ссылка на отзывы сохранена.")

# --- Callback когда менеджер нажимает "Ответить клиенту" ---
@dp.callback_query(lambda c: c.data and c.data.startswith("reply_"))
async def handle_reply_callback(callback: types.CallbackQuery):
    submission_id = int(callback.data.split("_", 1)[1])
    admin_id = callback.from_user.id

    # проверяем — команда доступна только в менеджерском чате
    manager_chat = await get_config("manager_chat_id")
    if not manager_chat or str(callback.message.chat.id) != str(manager_chat):
        await callback.answer("Эта кнопка доступна только в менеджерском чате.", show_alert=True)
        return

    # ставим admin в ожидание ввода оценки
    awaiting_reply[admin_id] = submission_id
    await callback.message.answer(f"Вы отвечаете на заявку #{submission_id}. Введите текст оценки для клиента (например: 1200₽).")
    await callback.answer()

@dp.message()
async def handle_admin_reply(message: types.Message):
    admin_id = message.from_user.id
    if admin_id in awaiting_reply:
        submission_id = awaiting_reply.pop(admin_id)
        evaluation_text = message.text

        # Получим запись заявки
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT user_id, name FROM submissions WHERE id = ?", (submission_id,))
            row = await cur.fetchone()
            if not row:
                await message.answer("Заявка не найдена в базе.")
                return
            user_id, product_name = row

            # Обновим запись
            await db.execute("UPDATE submissions SET status = ?, admin_id = ?, admin_comment = ? WHERE id = ?",
                             ("answered", admin_id, evaluation_text, submission_id))
            await db.commit()

        # Отправим пользователю
        user_msg = f"Оценка товара: {evaluation_text}\n\nНазвание: {product_name}\nЕсли согласны — напишите менеджеру."
        try:
            await bot.send_message(chat_id=int(user_id), text=user_msg)
            await message.answer(f"Оценка отправлена пользователю (id: {user_id}).")
        except Exception as e:
            await message.answer(f"Не удалось отправить сообщение пользователю: {e}")

# --- Запуск ---
async def main():
    await init_db()
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
