import asyncio
import os
import random
import sqlite3
import time
from html import escape
from datetime import date

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "pixel_pets.sqlite3")


# Редкость и виды питомцев
RARITIES = [
    ("Обычный", "⚪", 50, ["Цыплёнок", "Котёнок", "Щенок"]),
    ("Необычный", "🟢", 25, ["Лягушонок", "Лисёнок", "Пингвинёнок"]),
    ("Редкий", "🔵", 15, ["Дракончик", "Единорожек", "Аксолотль"]),
    ("Эпический", "🟣", 8, ["Феникс", "Лунный кот", "Кристальный волк"]),
    ("Легендарный", "🟡", 2, ["Звёздный дракон", "Космический кит"]),
]

RARITY_COLORS = {r[0]: r[1] for r in RARITIES}

MAX_STAT = 100
HUNGER_PER_HOUR = 4
MOOD_PER_HOUR = 2
ENERGY_PER_HOUR = 1.5
SLEEP_SECONDS = 3 * 60 * 60


# Рацион каждого питомца
PET_DIETS = {
    "Цыплёнок": ["Зерно", "Насекомые"],
    "Котёнок": ["Корм для кошек", "Мясо", "Рыба"],
    "Щенок": ["Корм для собак", "Мясо"],

    "Лягушонок": ["Насекомые", "Черви"],
    "Лисёнок": ["Мясо", "Ягоды", "Насекомые"],
    "Пингвинёнок": ["Рыба", "Криль"],

    "Дракончик": ["Мясо"],
    "Единорожек": ["Яблоко", "Морковка", "Сено"],
    "Аксолотль": ["Черви", "Мелкая рыба", "Рачки"],

    "Феникс": ["Мясо", "Ягоды"],
    "Лунный кот": ["Корм для кошек", "Мясо", "Рыба"],
    "Кристальный волк": ["Мясо"],

    "Звёздный дракон": ["Мясо", "Звёздный нектар"],
    "Космический кит": ["Планктон", "Криль"],
}


# Предметы магазина и их стоимость
SHOP_ITEMS = {
    "Зерно": 10,
    "Насекомые": 12,
    "Корм для кошек": 18,
    "Корм для собак": 18,
    "Мясо": 20,
    "Рыба": 18,
    "Ягоды": 12,
    "Криль": 16,
    "Черви": 12,
    "Мелкая рыба": 15,
    "Рачки": 14,
    "Яблоко": 15,
    "Морковка": 20,
    "Сено": 10,
    "Звёздный нектар": 30,
    "Планктон": 18,
    "Аптечка": 35,
}

ITEM_EMOJIS = {
    "Зерно": "🌾",
    "Насекомые": "🪲",
    "Корм для кошек": "🐱",
    "Корм для собак": "🐶",
    "Мясо": "🥩",
    "Рыба": "🐟",
    "Ягоды": "🫐",
    "Криль": "🦐",
    "Черви": "🪱",
    "Мелкая рыба": "🐠",
    "Рачки": "🦐",
    "Яблоко": "🍎",
    "Морковка": "🥕",
    "Сено": "🌿",
    "Звёздный нектар": "✨",
    "Планктон": "🫧",
    "Аптечка": "🧪",
}


# -------------------------
# БАЗА ДАННЫХ
# -------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                coins INTEGER NOT NULL DEFAULT 100,
                streak INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                created_at INTEGER NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS pets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                species TEXT NOT NULL,
                rarity TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'Детёныш',
                hunger REAL NOT NULL DEFAULT 85,
                mood REAL NOT NULL DEFAULT 85,
                energy REAL NOT NULL DEFAULT 85,
                health REAL NOT NULL DEFAULT 100,
                last_updated INTEGER NOT NULL,
                sleep_until INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS inventory(
                user_id INTEGER NOT NULL,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, item)
            )
        """)


def ensure_user(user_id):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(user_id, created_at) VALUES(?, ?)",
            (user_id, int(time.time()))
        )


def clamp(value):
    return max(0, min(MAX_STAT, value))


# -------------------------
# ПИТОМЦЫ И ХАРАКТЕРИСТИКИ
# -------------------------

def get_active_pet(user_id):
    with db() as c:
        row = c.execute(
            """
            SELECT * FROM pets
            WHERE user_id=? AND is_active=1
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,)
        ).fetchone()

    if not row:
        return None

    pet = dict(row)
    now = int(time.time())

    elapsed = max(0, now - pet["last_updated"])
    hours = elapsed / 3600

    sleeping = pet["sleep_until"] > now

    if sleeping:
        pet["energy"] = clamp(pet["energy"] + hours * 18)
        pet["hunger"] = clamp(pet["hunger"] - hours * 1.5)
        pet["mood"] = clamp(pet["mood"] - hours * 0.5)
    else:
        pet["hunger"] = clamp(
            pet["hunger"] - hours * HUNGER_PER_HOUR
        )
        pet["mood"] = clamp(
            pet["mood"] - hours * MOOD_PER_HOUR
        )
        pet["energy"] = clamp(
            pet["energy"] - hours * ENERGY_PER_HOUR
        )

    if pet["hunger"] < 15 or pet["mood"] < 10:
        pet["health"] = clamp(pet["health"] - hours * 2)
    elif pet["hunger"] > 40 and pet["mood"] > 35:
        pet["health"] = clamp(pet["health"] + hours * 0.5)

    pet["last_updated"] = now

    with db() as c:
        c.execute(
            """
            UPDATE pets
            SET hunger=?, mood=?, energy=?, health=?, last_updated=?
            WHERE id=?
            """,
            (
                pet["hunger"],
                pet["mood"],
                pet["energy"],
                pet["health"],
                now,
                pet["id"]
            )
        )

    return pet


def stat_bar(value):
    filled = round(value / 10)
    return "🟩" * filled + "⬛" * (10 - filled)


def pet_text(p):
    asleep = p["sleep_until"] > int(time.time())

    if asleep:
        status = "💤 Спит"
    elif p["health"] < 35:
        status = "🤒 Приболел"
    else:
        status = "✨ Бодрствует"

    rarity = f'{RARITY_COLORS.get(p["rarity"], "⚪")} {p["rarity"]}'

    return (
        f"🎮 <b>PIXEL PETS</b>\n\n"
        f"🐾 <b>{escape(p['name'])}</b> — {escape(p['species'])}\n"
        f"{rarity} · {escape(p['stage'])}\n"
        f"{status}\n\n"
        f"🍖 Голод: {round(p['hunger'])}/100\n"
        f"{stat_bar(p['hunger'])}\n"
        f"😊 Настроение: {round(p['mood'])}/100\n"
        f"{stat_bar(p['mood'])}\n"
        f"⚡ Энергия: {round(p['energy'])}/100\n"
        f"{stat_bar(p['energy'])}\n"
        f"❤️ Здоровье: {round(p['health'])}/100\n"
        f"{stat_bar(p['health'])}"
    )


# -------------------------
# КЛАВИАТУРЫ
# -------------------------

def main_keyboard():
    b = InlineKeyboardBuilder()

    buttons = [
        ("🍎 Покормить", "feed"),
        ("🎾 Играть", "play"),
        ("🌙 Спать", "sleep"),
        ("💊 Лечить", "heal"),
        ("🛍 Магазин", "shop"),
        ("🎁 Ежедневный бонус", "daily"),
        ("🥚 Новое яйцо", "egg"),
        ("📚 Коллекция", "collection"),
        ("🐾 Выбрать питомца", "choose_pet"),
        ("✏️ Переименовать", "rename"),
        ("❓ Помощь", "help"),
    ]

    for label, data in buttons:
        b.button(text=label, callback_data=data)

    b.adjust(2, 2, 2, 2, 2, 1)
    return b.as_markup()


def shop_keyboard():
    b = InlineKeyboardBuilder()

    for item, price in SHOP_ITEMS.items():
        emoji = ITEM_EMOJIS.get(item, "📦")
        b.button(
            text=f"{emoji} {item} — {price} 🪙",
            callback_data=f"buy:{item}:{price}"
        )

    b.button(text="↩️ Назад", callback_data="home")
    b.adjust(2)
    return b.as_markup()


def feed_keyboard(user_id):
    b = InlineKeyboardBuilder()

    with db() as c:
        rows = c.execute(
            """
            SELECT item, quantity FROM inventory
            WHERE user_id=? AND quantity > 0
            """,
            (user_id,)
        ).fetchall()

    for row in rows:
        item = row["item"]
        quantity = row["quantity"]
        emoji = ITEM_EMOJIS.get(item, "🍖")

        b.button(
            text=f"{emoji} {item} × {quantity}",
            callback_data=f"eat:{item}"
        )

    if not rows:
        b.button(text="🛍 Перейти в магазин", callback_data="shop")

    b.button(text="↩️ Назад", callback_data="home")
    b.adjust(2)
    return b.as_markup()


def choose_pet_keyboard(user_id):
    b = InlineKeyboardBuilder()

    with db() as c:
        pets = c.execute(
            """
            SELECT id, name, species, rarity, is_active
            FROM pets
            WHERE user_id=?
            ORDER BY id DESC
            """,
            (user_id,)
        ).fetchall()

    for pet in pets:
        active_mark = "⭐ " if pet["is_active"] else ""
        rarity_emoji = RARITY_COLORS.get(pet["rarity"], "⚪")

        b.button(
            text=(
                f"{active_mark}{rarity_emoji} "
                f"{pet['name']} — {pet['species']}"
            ),
            callback_data=f"selectpet:{pet['id']}"
        )

    b.button(text="↩️ Назад", callback_data="home")
    b.adjust(1)
    return b.as_markup()


# -------------------------
# ГЛАВНОЕ МЕНЮ
# -------------------------

async def show_home(target, user_id):
    p = get_active_pet(user_id)

    if not p:
        text = (
            "🥚 <b>Добро пожаловать в PIXEL PETS!</b>\n\n"
            "Твоё приключение начинается с яйца. "
            "Нажми «Вылупить яйцо», чтобы получить первого питомца."
        )

        b = InlineKeyboardBuilder()
        b.button(text="🥚 Вылупить яйцо", callback_data="egg")
        markup = b.as_markup()

    else:
        text = pet_text(p)
        markup = main_keyboard()

    if isinstance(target, Message):
        await target.answer(
            text,
            reply_markup=markup,
            parse_mode="HTML"
        )
    else:
        await target.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="HTML"
        )


# -------------------------
# ВЫЛУПЛЕНИЕ ЯЙЦА
# -------------------------

async def hatch(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(user_id)

    with db() as c:
        count = c.execute(
            "SELECT COUNT(*) FROM pets WHERE user_id=?",
            (user_id,)
        ).fetchone()[0]

        coins = c.execute(
            "SELECT coins FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()["coins"]

    if count and coins < 50:
        await callback.answer(
            "Новое яйцо стоит 50 монет. "
            "Заработай монеты ежедневным бонусом!",
            show_alert=True
        )
        return

    if count:
        with db() as c:
            c.execute(
                "UPDATE users SET coins=coins-50 WHERE user_id=?",
                (user_id,)
            )

    roll = random.randint(1, 100)
    cumulative = 0
    selected = RARITIES[0]

    for rarity in RARITIES:
        cumulative += rarity[2]
        if roll <= cumulative:
            selected = rarity
            break

    rarity_name, emoji, _, species_list = selected
    species = random.choice(species_list)

    with db() as c:
        c.execute(
            "UPDATE pets SET is_active=0 WHERE user_id=?",
            (user_id,)
        )

        c.execute(
            """
            INSERT INTO pets(user_id, name, species, rarity, last_updated)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                user_id,
                species,
                species,
                rarity_name,
                int(time.time())
            )
        )

    await callback.message.edit_text(
        f"✨ <b>Яйцо вылупилось!</b>\n\n"
        f"{emoji} Тебе достался питомец: <b>{species}</b>\n"
        f"Редкость: <b>{rarity_name}</b>\n\n"
        f"Можешь переименовать его командой /rename.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )

    await callback.answer("Новый питомец добавлен в коллекцию!")


# -------------------------
# ЗАПУСК БОТА
# -------------------------

async def main():
    if not TOKEN:
        raise RuntimeError(
            "Не найден BOT_TOKEN. Проверь файл .env."
        )

    init_db()

    bot = Bot(TOKEN)
    dp = Dispatcher()

    # /start
    @dp.message(CommandStart())
    async def start(message: Message):
        ensure_user(message.from_user.id)
        await show_home(message, message.from_user.id)

    # /help
    @dp.message(Command("help"))
    async def help_cmd(message: Message):
        await message.answer(
            "❓ <b>PIXEL PETS — помощь</b>\n\n"
            "/start — открыть игру\n"
            "/rename Имя — переименовать питомца\n"
            "/help — помощь",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

    # Переименование питомца
    @dp.message(Command("rename"))
    async def rename(message: Message):
        name = message.text.partition(" ")[2].strip()[:24]

        if not name:
            await message.answer(
                "Напиши имя после команды:\n"
                "<code>/rename Пиксель</code>",
                parse_mode="HTML"
            )
            return

        p = get_active_pet(message.from_user.id)

        if not p:
            await message.answer("Сначала вылупи питомца: /start")
            return

        with db() as c:
            c.execute(
                "UPDATE pets SET name=? WHERE id=? AND user_id=?",
                (name, p["id"], message.from_user.id)
            )

        await message.answer(
            f"🐾 Теперь твоего питомца зовут "
            f"<b>{escape(name)}</b>!",
            parse_mode="HTML"
        )

        await show_home(message, message.from_user.id)

    # Кнопка переименования
    @dp.callback_query(F.data == "rename")
    async def rename_button(callback: CallbackQuery):
        await callback.message.answer(
            "✏️ Чтобы переименовать питомца, отправь сообщение "
            "в формате:\n\n"
            "<code>/rename Новое имя</code>",
            parse_mode="HTML"
        )
        await callback.answer()

    # Помощь
    @dp.callback_query(F.data == "help")
    async def help_button(callback: CallbackQuery):
        await callback.message.answer(
            "❓ <b>PIXEL PETS — помощь</b>\n\n"
            "🍎 Покормить — выбрать еду из инвентаря.\n"
            "🎾 Играть — повысить настроение и получить 5 монет.\n"
            "🌙 Спать — восстанавливать энергию в течение 3 часов.\n"
            "💊 Лечить — использовать аптечку.\n"
            "🛍 Магазин — покупать еду и предметы.\n"
            "🎁 Ежедневный бонус — получать монеты раз в день.\n"
            "🥚 Новое яйцо — получить нового питомца.\n"
            "📚 Коллекция — посмотреть всех питомцев.\n"
            "🐾 Выбрать питомца — переключиться на другого.\n"
            "✏️ Переименовать — изменить имя питомца.",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()

    # Возврат в главное меню
    @dp.callback_query(F.data == "home")
    async def home_cb(callback: CallbackQuery):
        await show_home(callback, callback.from_user.id)
        await callback.answer()

    # Новое яйцо
    @dp.callback_query(F.data == "egg")
    async def egg_cb(callback: CallbackQuery):
        await hatch(callback)

    # -------------------------
    # ВЫБОР ПИТОМЦА
    # -------------------------

    @dp.callback_query(F.data == "choose_pet")
    async def choose_pet(callback: CallbackQuery):
        uid = callback.from_user.id

        with db() as c:
            pets = c.execute(
                "SELECT id FROM pets WHERE user_id=?",
                (uid,)
            ).fetchall()

        if not pets:
            await callback.answer(
                "У тебя пока нет питомцев!",
                show_alert=True
            )
            return

        await callback.message.edit_text(
            "🐾 <b>Выбери питомца</b>\n\n"
            "Нажми на питомца, которым хочешь сейчас управлять:",
            reply_markup=choose_pet_keyboard(uid),
            parse_mode="HTML"
        )

        await callback.answer()

    @dp.callback_query(F.data.startswith("selectpet:"))
    async def select_pet(callback: CallbackQuery):
        uid = callback.from_user.id
        pet_id = int(callback.data.split(":")[1])

        with db() as c:
            pet = c.execute(
                "SELECT * FROM pets WHERE id=? AND user_id=?",
                (pet_id, uid)
            ).fetchone()

            if not pet:
                await callback.answer(
                    "Этот питомец не найден!",
                    show_alert=True
                )
                return

            if pet["is_active"]:
                await callback.answer(
                    "Этот питомец уже выбран!",
                    show_alert=True
                )
                return

            c.execute(
                "UPDATE pets SET is_active=0 WHERE user_id=?",
                (uid,)
            )

            c.execute(
                "UPDATE pets SET is_active=1 WHERE id=? AND user_id=?",
                (pet_id, uid)
            )

        await callback.answer(
            f"Теперь активен питомец {pet['name']}!"
        )

        await show_home(callback, uid)

    # -------------------------
    # КОРМЛЕНИЕ
    # -------------------------

    @dp.callback_query(F.data == "feed")
    async def feed(callback: CallbackQuery):
        uid = callback.from_user.id
        p = get_active_pet(uid)

        if not p:
            await callback.answer(
                "Сначала вылупи питомца!",
                show_alert=True
            )
            return

        with db() as c:
            foods = c.execute(
                """
                SELECT item, quantity FROM inventory
                WHERE user_id=? AND quantity > 0
                """,
                (uid,)
            ).fetchall()

        if not foods:
            await callback.message.edit_text(
                "🍖 <b>У тебя пока нет еды!</b>\n\n"
                "Загляни в магазин и купи что-нибудь для питомца.",
                reply_markup=feed_keyboard(uid),
                parse_mode="HTML"
            )
            await callback.answer()
            return

        await callback.message.edit_text(
            f"🍖 <b>Чем покормить {escape(p['name'])}?</b>\n\n"
            "Выбери продукт из своего инвентаря. "
            "Помни, что разные питомцы едят разную еду!",
            reply_markup=feed_keyboard(uid),
            parse_mode="HTML"
        )

        await callback.answer()

    # Использование выбранной еды
    @dp.callback_query(F.data.startswith("eat:"))
    async def eat(callback: CallbackQuery):
        uid = callback.from_user.id
        item = callback.data.split(":", 1)[1]

        p = get_active_pet(uid)

        if not p:
            await callback.answer(
                "Сначала вылупи питомца!",
                show_alert=True
            )
            return

        allowed_foods = PET_DIETS.get(p["species"], [])

        # Проверяем, подходит ли еда питомцу
        if item not in allowed_foods:
            await callback.answer(
                f"{p['species']} такое не ест! "
                "Еда осталась в инвентаре.",
                show_alert=True
            )
            return

        with db() as c:
            row = c.execute(
                """
                SELECT quantity FROM inventory
                WHERE user_id=? AND item=?
                """,
                (uid, item)
            ).fetchone()

            if not row or row["quantity"] <= 0:
                await callback.answer(
                    "Эта еда закончилась!",
                    show_alert=True
                )
                return

            c.execute(
                """
                UPDATE inventory
                SET quantity=quantity-1
                WHERE user_id=? AND item=?
                """,
                (uid, item)
            )

            c.execute(
                """
                UPDATE pets
                SET hunger=MIN(100, hunger+30),
                    mood=MIN(100, mood+5)
                WHERE id=? AND user_id=?
                """,
                (p["id"], uid)
            )

        emoji = ITEM_EMOJIS.get(item, "🍖")

        await callback.answer(
            f"{p['name']} с удовольствием съел(а) {item}! {emoji}"
        )

        await show_home(callback, uid)

    # -------------------------
    # ИГРА
    # -------------------------

    @dp.callback_query(F.data == "play")
    async def play(callback: CallbackQuery):
        p = get_active_pet(callback.from_user.id)

        if not p:
            await callback.answer(
                "Сначала вылупи питомца!",
                show_alert=True
            )
            return

        if p["energy"] < 15:
            await callback.answer(
                "Питомец слишком устал. Пусть поспит!",
                show_alert=True
            )
            return

        with db() as c:
            c.execute(
                """
                UPDATE pets
                SET mood=MIN(100, mood+25),
                    energy=MAX(0, energy-15)
                WHERE id=?
                """,
                (p["id"],)
            )

            c.execute(
                "UPDATE users SET coins=coins+5 WHERE user_id=?",
                (callback.from_user.id,)
            )

        await callback.answer(
            "Вы отлично поиграли! +5 монет 🪙"
        )

        await show_home(callback, callback.from_user.id)

    # -------------------------
    # СОН
    # -------------------------

    @dp.callback_query(F.data == "sleep")
    async def sleep(callback: CallbackQuery):
        p = get_active_pet(callback.from_user.id)

        if not p:
            await callback.answer(
                "Сначала вылупи питомца!",
                show_alert=True
            )
            return

        until = int(time.time()) + SLEEP_SECONDS

        with db() as c:
            c.execute(
                """
                UPDATE pets
                SET sleep_until=?, last_updated=?
                WHERE id=?
                """,
                (until, int(time.time()), p["id"])
            )

        await callback.answer(
            "Сладких снов! Энергия будет восстанавливаться 3 часа."
        )

        await show_home(callback, callback.from_user.id)

    # -------------------------
    # ЛЕЧЕНИЕ
    # -------------------------

    @dp.callback_query(F.data == "heal")
    async def heal(callback: CallbackQuery):
        uid = callback.from_user.id
        p = get_active_pet(uid)

        if not p:
            await callback.answer(
                "Сначала вылупи питомца!",
                show_alert=True
            )
            return

        with db() as c:
            row = c.execute(
                """
                SELECT quantity FROM inventory
                WHERE user_id=? AND item='Аптечка'
                """,
                (uid,)
            ).fetchone()

            if not row or row["quantity"] <= 0:
                await callback.answer(
                    "Нет аптечки. Купи её в магазине!",
                    show_alert=True
                )
                return

            c.execute(
                """
                UPDATE inventory
                SET quantity=quantity-1
                WHERE user_id=? AND item='Аптечка'
                """,
                (uid,)
            )

            c.execute(
                """
                UPDATE pets
                SET health=MIN(100, health+45)
                WHERE id=?
                """,
                (p["id"],)
            )

        await callback.answer("Питомцу стало лучше! ❤️")
        await show_home(callback, uid)

    # -------------------------
    # МАГАЗИН
    # -------------------------

    @dp.callback_query(F.data == "shop")
    async def shop(callback: CallbackQuery):
        await callback.message.edit_text(
            "🛍 <b>Магазин PIXEL PETS</b>\n\n"
            "Выбирай еду и предметы для своих питомцев:",
            reply_markup=shop_keyboard(),
            parse_mode="HTML"
        )

        await callback.answer()

    # Покупка предмета
    @dp.callback_query(F.data.startswith("buy:"))
    async def buy(callback: CallbackQuery):
        _, item, price_s = callback.data.split(":")
        price = int(price_s)
        uid = callback.from_user.id

        if item not in SHOP_ITEMS or SHOP_ITEMS[item] != price:
            await callback.answer(
                "Не удалось выполнить покупку.",
                show_alert=True
            )
            return

        with db() as c:
            user = c.execute(
                "SELECT coins FROM users WHERE user_id=?",
                (uid,)
            ).fetchone()

            if not user or user["coins"] < price:
                await callback.answer(
                    "Не хватает монет!",
                    show_alert=True
                )
                return

            c.execute(
                "UPDATE users SET coins=coins-? WHERE user_id=?",
                (price, uid)
            )

            c.execute(
                """
                INSERT INTO inventory(user_id, item, quantity)
                VALUES(?, ?, 1)
                ON CONFLICT(user_id, item)
                DO UPDATE SET quantity=quantity+1
                """,
                (uid, item)
            )

        emoji = ITEM_EMOJIS.get(item, "📦")

        await callback.answer(f"Куплено: {item}! {emoji}")

        await callback.message.edit_text(
            f"✅ <b>Покупка добавлена в инвентарь!</b>\n\n"
            f"{emoji} {item}\n"
            f"Стоимость: {price} 🪙",
            reply_markup=shop_keyboard(),
            parse_mode="HTML"
        )

    # -------------------------
    # ЕЖЕДНЕВНЫЙ БОНУС
    # -------------------------

    @dp.callback_query(F.data == "daily")
    async def daily(callback: CallbackQuery):
        uid = callback.from_user.id
        ensure_user(uid)

        today = date.today().isoformat()
        yesterday = date.fromordinal(
            date.today().toordinal() - 1
        ).isoformat()

        with db() as c:
            u = c.execute(
                "SELECT * FROM users WHERE user_id=?",
                (uid,)
            ).fetchone()

            if u["last_daily"] == today:
                await callback.answer(
                    "Сегодня бонус уже получен. Загляни завтра!",
                    show_alert=True
                )
                return

            streak = (
                u["streak"] + 1
                if u["last_daily"] == yesterday
                else 1
            )

            reward = 30 + min(streak, 7) * 5

            c.execute(
                """
                UPDATE users
                SET coins=coins+?, streak=?, last_daily=?
                WHERE user_id=?
                """,
                (reward, streak, today, uid)
            )

        await callback.answer(
            f"Получено {reward} монет! Серия: {streak} дн. 🎁",
            show_alert=True
        )

        await show_home(callback, uid)

    # -------------------------
    # КОЛЛЕКЦИЯ
    # -------------------------

    @dp.callback_query(F.data == "collection")
    async def collection(callback: CallbackQuery):
        uid = callback.from_user.id

        with db() as c:
            pets = c.execute(
                """
                SELECT id, name, species, rarity, stage, is_active
                FROM pets
                WHERE user_id=?
                ORDER BY id DESC
                """,
                (uid,)
            ).fetchall()

            u = c.execute(
                "SELECT coins, streak FROM users WHERE user_id=?",
                (uid,)
            ).fetchone()

        if not pets:
            text = "📚 Коллекция пока пуста. Вылупи первое яйцо!"
        else:
            lines = []

            for p in pets:
                mark = "⭐" if p["is_active"] else "▫️"
                rarity_emoji = RARITY_COLORS.get(
                    p["rarity"], "⚪"
                )

                lines.append(
                    f"{mark} {rarity_emoji} "
                    f"<b>{escape(p['name'])}</b> — "
                    f"{escape(p['species'])} "
                    f"({escape(p['rarity'])})"
                )

            text = (
                "📚 <b>Твоя коллекция</b>\n\n"
                + "\n".join(lines)
            )

        text += (
            f"\n\n🪙 Монеты: {u['coins']}"
            f" · 🔥 Стрик: {u['streak']}"
        )

        b = InlineKeyboardBuilder()
        b.button(
            text="🐾 Выбрать питомца",
            callback_data="choose_pet"
        )
        b.button(text="↩️ Назад", callback_data="home")
        b.adjust(1)

        await callback.message.edit_text(
            text,
            reply_markup=b.as_markup(),
            parse_mode="HTML"
        )

        await callback.answer()

    # Запуск
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())