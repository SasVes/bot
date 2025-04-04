import logging
import asyncio
import datetime
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram_calendar import SimpleCalendar, SimpleCalendarCallback
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import os

# Загружаем переменные из .env
load_dotenv()

# Получаем токен
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    print("Токен не найден в .env!")
else:
    print("Токен загружен:", TOKEN)
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ID чата для уведомлений (замените на ваш)
NOTIFICATION_CHAT_ID = "-1002534379051"

# Подключение к базе данных
conn = sqlite3.connect("bookings.db", check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицу для бронирований
cursor.execute('''CREATE TABLE IF NOT EXISTS bookings (
                    user_id INTEGER,
                    username TEXT,
                    date TEXT,
                    equipment TEXT,
                    quantity INTEGER,
                    price INTEGER)''')

# Создаем таблицу для архива
cursor.execute('''CREATE TABLE IF NOT EXISTS archive_bookings (
                    user_id INTEGER,
                    username TEXT,
                    date TEXT,
                    equipment TEXT,
                    quantity INTEGER,
                    price INTEGER)''')
conn.commit()

# Словарь с оборудованием (обновленный)
EQUIPMENT = {
    "Приборы": {
        "1200x": [1, 6000], "600c": [1, 4000], "600x": [2, 3000], "300x": [2, 2100], "60x": [3, 900], "F22c": [3, 2100],
         "INFINIBAR 12": [3, 1050], "INFINIBAR 6": [1, 810],
         "MC Pro": [2, 600], "INFINIMAT 4": [1, 3000], "Dedolight": [1, 300], "600 х/с за 2100": [3, 2100]
    },
    "Софтбоксы, насадки": {
        "соты INFINIBAR 12": [2, 390], "софтбокс INFINIBAR 12": [2, 600], "Lightdome 150": [1, 750], "Lightdome 90": [1, 600], "Lantern 90": [1, 600], "Lantern F22": [1, 390], "Рефлекторы 1200": [1,810], "Softbox 60x": [2, 300]
    },
    "Плоскость": {
        "Фрост рама": [4, 200], "Отражатель": [1, 200]
    },
    "Генератор, коммутация и тд.": {
        "Генератор 8кв": [1, 7500], "Генератор 2кв": [1, 3000], "Кабло 10м по 5шт": [2, 750], "Кабло 10м по 1шт": [5, 150], "Страховка 50см": [10, 30], "V-mount": [2, 360], "Дым машина": [1, 1000], "Фал 30м": [2, 200], "Фал 20м": [2, 150], "Бабкина сумка": [1, 1]
    },
    "Связь": {
        "Интеркомы 6шт": [1, 5000], "Интеркомы 4шт": [1, 3300], "Интеркомы 2шт": [1, 1650], "Рации": [2, 100]
    }
}

# Состояния для FSM
class BookingState(StatesGroup):
    choosing_date = State()
    choosing_category = State()
    choosing_items = State()
    confirmation = State()
    removing_items = State()

class DeletingBookingState(StatesGroup):
    choosing_booking_to_delete = State()

class EditingBookingState(StatesGroup):
    choosing_booking_to_edit = State()
    choosing_edit_action = State()
    editing_date = State()
    editing_equipment = State()

# Обновляем главное меню
main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Забронировать оборудование")],
        [KeyboardButton(text="Занятые даты")],
        [KeyboardButton(text="Мои бронирования")],
        [KeyboardButton(text="Все бронирования")],
        [KeyboardButton(text="Удалить бронь")],
        [KeyboardButton(text="Редактировать бронь")],
        [KeyboardButton(text="Архив бронирований")]
    ],
    resize_keyboard=True
)

# Функция для отправки уведомлений в чат
async def send_notification_to_chat(message: str):
    try:
        await bot.send_message(chat_id=NOTIFICATION_CHAT_ID, text=message, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление в чат: {e}")

# Функция для переноса прошедших бронирований в архив
async def move_past_bookings_to_archive():
    current_date = datetime.date.today().strftime("%Y-%m-%d")
    
    cursor.execute("SELECT * FROM bookings WHERE date < ?", (current_date,))
    past_bookings = cursor.fetchall()
    
    if past_bookings:
        cursor.executemany("INSERT INTO archive_bookings VALUES (?, ?, ?, ?, ?, ?)", past_bookings)
        conn.commit()
        
        cursor.execute("DELETE FROM bookings WHERE date < ?", (current_date,))
        conn.commit()

# Функция проверки доступности оборудования
async def is_equipment_available(equipment_str: str, date: str, exclude_booking_id: int = None) -> bool:
    equipment_lines = equipment_str.split("\n")
    requested_items = {}
    
    for line in equipment_lines:
        if " x" in line:
            item, quantity = line.split(" x")
            requested_items[item.strip()] = int(quantity)
    
    if exclude_booking_id:
        cursor.execute("""
            SELECT equipment FROM bookings 
            WHERE date = ? AND rowid != ?
        """, (date, exclude_booking_id))
    else:
        cursor.execute("SELECT equipment FROM bookings WHERE date = ?", (date,))
    
    booked_equipment = cursor.fetchall()
    
    for item, needed_quantity in requested_items.items():
        total_available = 0
        for category in EQUIPMENT.values():
            if item in category:
                total_available = category[item][0]
                break
        
        if total_available == 0:
            return False
        
        booked_count = 0
        for booking in booked_equipment:
            for line in booking[0].split("\n"):
                if line.startswith(item + " x"):
                    booked_count += int(line.split("x")[1])
        
        if booked_count + needed_quantity > total_available:
            return False
    
    return True

# Команда /start
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await message.answer("Привет! Я бот для бронирования оборудования. Используйте кнопки ниже:", reply_markup=main_menu_keyboard)

# Обработка нажатия на кнопку "Забронировать оборудование"
@dp.message(lambda message: message.text == "Забронировать оборудование")
async def start_booking(message: Message, state: FSMContext):
    await state.set_state(BookingState.choosing_date)
    await message.answer("Выберите дату бронирования:", reply_markup=await SimpleCalendar().start_calendar())

# Обработка выбора даты из календаря
@dp.callback_query(SimpleCalendarCallback.filter())
async def process_simple_calendar(callback_query: CallbackQuery, callback_data: dict, state: FSMContext):
    selected, date = await SimpleCalendar().process_selection(callback_query, callback_data)
    if selected:
        selected_date = date.date()
        if selected_date < datetime.date.today():
            await callback_query.message.answer("Ошибка! Нельзя выбрать прошедшую дату.")
            return
        await state.update_data(date=selected_date.strftime("%Y-%m-%d"))
        await callback_query.message.answer(f"Вы выбрали дату: {selected_date.strftime('%Y-%m-%d')}")
        
        await state.set_state(BookingState.choosing_category)
        
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=cat)] for cat in EQUIPMENT.keys()] +
                     [[KeyboardButton(text="Изменить дату"), KeyboardButton(text="Отмена"), KeyboardButton(text="Готово")]],
            resize_keyboard=True
        )
        await callback_query.message.answer("Выберите категорию оборудования:", reply_markup=keyboard)

# Обработка выбора категории
@dp.message(BookingState.choosing_category)
async def choose_category(message: Message, state: FSMContext):
    if message.text in EQUIPMENT:
        await state.update_data(category=message.text)
        
        data = await state.get_data()
        date = data.get("date")
        
        cursor.execute("SELECT equipment FROM bookings WHERE date = ?", (date,))
        booked_equipment = cursor.fetchall()
        booked_items = {}
        for booking in booked_equipment:
            for item_line in booking[0].split("\n"):
                if " x" in item_line:
                    name, quantity = item_line.split(" x")
                    booked_items[name] = booked_items.get(name, 0) + int(quantity)
        
        keyboard_buttons = []
        for item, details in EQUIPMENT[message.text].items():
            total_available = details[0]
            booked = booked_items.get(item, 0)
            available = total_available - booked
            keyboard_buttons.append([KeyboardButton(text=f"{item} ({available} шт.)")])
        
        keyboard_buttons.append([KeyboardButton(text="Назад"), KeyboardButton(text="Готово")])
        keyboard_buttons.append([KeyboardButton(text="Изменить дату")])
        
        keyboard = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
        await message.answer("Выберите оборудование:", reply_markup=keyboard)
        await state.set_state(BookingState.choosing_items)
    elif message.text == "Изменить дату":
        await state.set_state(BookingState.choosing_date)
        await message.answer("Выберите дату бронирования:", reply_markup=await SimpleCalendar().start_calendar())
    elif message.text == "Отмена":
        await state.clear()
        await message.answer("Бронирование отменено.", reply_markup=main_menu_keyboard)
    elif message.text == "Готово":
        await show_confirmation(message, state)
    else:
        await message.answer("Выберите категорию из списка.")

# Функция для показа подтверждения бронирования
async def show_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()
    items = data.get("items", {})
    
    total_price = 0
    user_friendly_details = []
    for item, quantity in items.items():
        for category, equipment in EQUIPMENT.items():
            if item in equipment:
                price_per_unit = equipment[item][1]
                total_item_price = price_per_unit * quantity
                total_price += total_item_price
                user_friendly_details.append(f"{item} x{quantity} ({total_item_price} руб.)")
                break
    
    selected_items = "\n".join(user_friendly_details)
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подтвердить бронь")],
            [KeyboardButton(text="Добавить еще оборудование")],
            [KeyboardButton(text="Удалить оборудование")],
            [KeyboardButton(text="Отменить смету")]
        ],
        resize_keyboard=True
    )
    
    if items:
        await message.answer(
            f"Текущий заказ:\n{selected_items}\n\n*Итого: {total_price} руб.*\n\nВыберите действие:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        await message.answer("Вы не выбрали ни одного оборудования.", reply_markup=keyboard)
    
    await state.set_state(BookingState.confirmation)

# Обработка выбора оборудования
@dp.message(BookingState.choosing_items)
async def choose_items(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data["category"]
    items = data.get("items", {})
    
    if message.text.split(" (")[0] in EQUIPMENT[category]:
        item_name = message.text.split(" (")[0]
        date = data["date"]
        
        cursor.execute("SELECT equipment FROM bookings WHERE date = ?", (date,))
        booked_equipment = cursor.fetchall()
        booked_items = {}
        for booking in booked_equipment:
            for item_line in booking[0].split("\n"):
                if " x" in item_line:
                    name, quantity = item_line.split(" x")
                    booked_items[name] = booked_items.get(name, 0) + int(quantity)
        
        total_available = EQUIPMENT[category][item_name][0]
        booked = booked_items.get(item_name, 0)
        available = total_available - booked
        
        if available > 0:
            already_added = items.get(item_name, 0)
            if already_added < available:
                items[item_name] = already_added + 1
                await state.update_data(items=items)
                await message.answer(f"Добавлено: {item_name} ({items[item_name]} шт.)\nОсталось: {available - items[item_name]} шт.")
            else:
                await message.answer(f"Невозможно добавить больше {item_name}. Доступно только {available} шт.")
        else:
            await message.answer("Это оборудование уже занято на выбранную дату.")
        
        keyboard_buttons = []
        for item, details in EQUIPMENT[category].items():
            total_available = details[0]
            booked = booked_items.get(item, 0)
            available = total_available - booked - items.get(item, 0)
            keyboard_buttons.append([KeyboardButton(text=f"{item} ({available} шт.)")])
        
        keyboard_buttons.append([KeyboardButton(text="Назад"), KeyboardButton(text="Готово")])
        keyboard = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
        await message.answer("Выберите оборудование:", reply_markup=keyboard)
    elif message.text == "Готово":
        if not items:
            await message.answer("Вы не выбрали ни одного оборудования.")
        else:
            await show_confirmation(message, state)
    elif message.text == "Назад":
        await state.set_state(BookingState.choosing_category)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=cat)] for cat in EQUIPMENT.keys()] +
                     [[KeyboardButton(text="Изменить дату"), KeyboardButton(text="Отмена"), KeyboardButton(text="Готово")]],
            resize_keyboard=True
        )
        await message.answer("Выберите категорию оборудования:", reply_markup=keyboard)
    else:
        await message.answer("Выберите оборудование из списка или нажмите 'Готово'.")

# Обработка подтверждения бронирования
@dp.message(BookingState.confirmation)
async def handle_confirmation(message: Message, state: FSMContext):
    if message.text == "Подтвердить бронь":
        await confirm_booking(message, state)
    elif message.text == "Добавить еще оборудование":
        await state.set_state(BookingState.choosing_category)
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=cat)] for cat in EQUIPMENT.keys()] +
                     [[KeyboardButton(text="Изменить дату"), KeyboardButton(text="Отмена"), KeyboardButton(text="Готово")]],
            resize_keyboard=True
        )
        await message.answer("Выберите категорию оборудования:", reply_markup=keyboard)
    elif message.text == "Удалить оборудование":
        data = await state.get_data()
        items = data.get("items", {})
        if not items:
            await message.answer("Нет оборудования для удаления.")
        else:
            keyboard_buttons = []
            for item, quantity in items.items():
                keyboard_buttons.append([KeyboardButton(text=f"{item} ({quantity} шт.)")])
            
            keyboard_buttons.append([KeyboardButton(text="Назад")])
            keyboard = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
            await message.answer("Выберите оборудование для удаления:", reply_markup=keyboard)
            await state.set_state(BookingState.removing_items)
    elif message.text == "Отменить смету":
        await state.clear()
        await message.answer("Смета отменена. Вы вернулись в главное меню.", reply_markup=main_menu_keyboard)
    else:
        await message.answer("Используйте кнопки для выбора действия.")

# Обработка удаления оборудования
@dp.message(BookingState.removing_items)
async def remove_items(message: Message, state: FSMContext):
    data = await state.get_data()
    items = data.get("items", {})
    
    if message.text.split(" (")[0] in items:
        item_name = message.text.split(" (")[0]
        
        if items[item_name] > 1:
            items[item_name] -= 1
            await state.update_data(items=items)
            await message.answer(f"Удалено: {item_name} ({items[item_name]} шт.)")
        else:
            del items[item_name]
            await state.update_data(items=items)
            await message.answer(f"Оборудование {item_name} полностью удалено")
        
        keyboard_buttons = []
        for item, quantity in items.items():
            keyboard_buttons.append([KeyboardButton(text=f"{item} ({quantity} шт.)")])
        
        keyboard_buttons.append([KeyboardButton(text="Назад")])
        keyboard = ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
        await message.answer("Выберите оборудование для удаления:", reply_markup=keyboard)
    elif message.text == "Назад":
        await show_confirmation(message, state)
    else:
        await message.answer("Используйте кнопки для выбора оборудования.")

# Обработка нажатия на кнопку "Занятые даты"
@dp.message(lambda message: message.text == "Занятые даты")
async def show_booked_dates(message: Message):
    cursor.execute("SELECT DISTINCT date FROM bookings")
    dates = cursor.fetchall()
    if dates:
        await message.answer("Занятые даты:\n" + "\n".join([date[0] for date in dates]))
    else:
        await message.answer("Нет занятых дат.")

# Обработка нажатия на кнопку "Мои бронирования"
@dp.message(lambda message: message.text == "Мои бронирования")
async def user_report(message: Message):
    await move_past_bookings_to_archive()
    
    cursor.execute("SELECT username, date, price FROM bookings WHERE user_id = ?", (message.from_user.id,))
    bookings = cursor.fetchall()
    
    if bookings:
        report = "📋 *Ваши бронирования:*\n\n"
        for booking in bookings:
            username, date, price = booking
            report += (
                f"👤 *Пользователь:* {username}\n"
                f"📅 *Дата:* {date}\n"
                f"💵 *Сумма:* {price} руб.\n"
                "————————————\n"
            )
        await message.answer(report, parse_mode="Markdown")
    else:
        await message.answer("У вас нет активных бронирований.")

# Обработка нажатия на кнопку "Все бронирования"
@dp.message(lambda message: message.text == "Все бронирования")
async def full_report(message: Message):
    await move_past_bookings_to_archive()
    
    cursor.execute("SELECT username, date, price FROM bookings")
    bookings = cursor.fetchall()
    if bookings:
        report = "📋 *Все бронирования:*\n\n"
        for booking in bookings:
            username, date, price = booking
            report += (
                f"👤 *Пользователь:* {username}\n"
                f"📅 *Дата:* {date}\n"
                f"💵 *Сумма:* {price} руб.\n"
                "————————————\n"
            )
        await message.answer(report, parse_mode="Markdown")
    else:
        await message.answer("Нет активных бронирований.")

# Обработка нажатия на кнопку "Архив бронирований"
@dp.message(lambda message: message.text == "Архив бронирований")
async def show_archive(message: Message):
    cursor.execute("SELECT username, date, price FROM archive_bookings WHERE user_id = ?", (message.from_user.id,))
    archive_bookings = cursor.fetchall()
    
    if archive_bookings:
        report = "📋 *Ваши архивные бронирования:*\n\n"
        for booking in archive_bookings:
            username, date, price = booking
            report += (
                f"👤 *Пользователь:* {username}\n"
                f"📅 *Дата:* {date}\n"
                f"💵 *Сумма:* {price} руб.\n"
                "————————————\n"
            )
        await message.answer(report, parse_mode="Markdown")
    else:
        await message.answer("У вас нет архивных бронирований.")

# Обработка нажатия на кнопку "Удалить бронь"
@dp.message(lambda message: message.text == "Удалить бронь")
async def start_deleting_booking(message: Message, state: FSMContext):
    await move_past_bookings_to_archive()
    
    cursor.execute("SELECT rowid, date, equipment FROM bookings WHERE user_id = ?", (message.from_user.id,))
    bookings = cursor.fetchall()
    
    if not bookings:
        await message.answer("У вас нет активных бронирований.")
        return
    
    builder = InlineKeyboardBuilder()
    for booking in bookings:
        rowid, date, equipment = booking
        equipment_list = equipment.split("\n")
        short_equipment = ", ".join(equipment_list[:3])
        if len(equipment_list) > 3:
            short_equipment += "..."
        builder.button(text=f"{date} - {short_equipment}", callback_data=f"delete_booking:{rowid}")
    builder.adjust(1)
    
    await message.answer("Выберите бронирование для удаления:", reply_markup=builder.as_markup())
    await state.set_state(DeletingBookingState.choosing_booking_to_delete)

# Обработка выбора бронирования для удаления
@dp.callback_query(DeletingBookingState.choosing_booking_to_delete, lambda c: c.data.startswith("delete_booking:"))
async def process_booking_deletion(callback_query: CallbackQuery, state: FSMContext):
    selected_id = int(callback_query.data.split(":")[1])
    
    cursor.execute("SELECT rowid, date, equipment FROM bookings WHERE rowid = ? AND user_id = ?", (selected_id, callback_query.from_user.id))
    selected_booking = cursor.fetchone()
    
    if not selected_booking:
        await callback_query.message.answer("Бронирование с таким ID не найдено или оно принадлежит другому пользователю.")
        return
    
    cursor.execute("DELETE FROM bookings WHERE rowid = ?", (selected_id,))
    conn.commit()
    
    await callback_query.message.answer(f"Бронирование на {selected_booking[1]} успешно удалено!", reply_markup=main_menu_keyboard)
    await state.clear()

    notification_message = (
        "❌ *Бронирование отменено!*\n\n"
        f"📅 *Дата:* {selected_booking[1]}\n"
        f"👤 *Пользователь:* @{callback_query.from_user.username}\n"
        f"📦 *Оборудование:* {selected_booking[2]}\n\n"
        "Оборудование снова доступно для бронирования! 🎉"
    )
    await send_notification_to_chat(notification_message)

# Обработка нажатия на кнопку "Редактировать бронь"
@dp.message(lambda message: message.text == "Редактировать бронь")
async def start_editing_booking(message: Message, state: FSMContext):
    await move_past_bookings_to_archive()
    
    cursor.execute("SELECT rowid, date, equipment FROM bookings WHERE user_id = ?", (message.from_user.id,))
    bookings = cursor.fetchall()
    
    if not bookings:
        await message.answer("У вас нет активных бронирований для редактирования.")
        return
    
    builder = InlineKeyboardBuilder()
    for booking in bookings:
        rowid, date, equipment = booking
        short_equipment = ", ".join(equipment.split("\n")[:3])
        builder.button(text=f"{date} - {short_equipment}", callback_data=f"edit_booking:{rowid}")
    builder.adjust(1)
    
    await message.answer("Выберите бронирование для редактирования:", reply_markup=builder.as_markup())
    await state.set_state(EditingBookingState.choosing_booking_to_edit)

# Обработка выбора брони для редактирования
@dp.callback_query(EditingBookingState.choosing_booking_to_edit, lambda c: c.data.startswith("edit_booking:"))
async def select_booking_to_edit(callback_query: CallbackQuery, state: FSMContext):
    booking_id = int(callback_query.data.split(":")[1])
    await state.update_data(booking_id=booking_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Изменить дату", callback_data="edit_date")
    builder.button(text="📦 Изменить оборудование", callback_data="edit_equipment")
    builder.button(text="❌ Отменить редактирование", callback_data="cancel_edit")
    builder.adjust(1)
    
    await callback_query.message.answer(
        "Что вы хотите изменить?",
        reply_markup=builder.as_markup()
    )
    await state.set_state(EditingBookingState.choosing_edit_action)

# Обработка изменения даты
@dp.callback_query(EditingBookingState.choosing_edit_action, lambda c: c.data == "edit_date")
async def edit_booking_date(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.answer(
        "Выберите новую дату бронирования:",
        reply_markup=await SimpleCalendar().start_calendar()
    )
    await state.set_state(EditingBookingState.editing_date)

@dp.callback_query(EditingBookingState.editing_date, SimpleCalendarCallback.filter())
async def process_date_edit(callback_query: CallbackQuery, callback_data: dict, state: FSMContext):
    selected, date = await SimpleCalendar().process_selection(callback_query, callback_data)
    if selected:
        new_date = date.date().strftime("%Y-%m-%d")
        data = await state.get_data()
        
        cursor.execute("SELECT equipment FROM bookings WHERE rowid = ?", (data['booking_id'],))
        equipment = cursor.fetchone()[0]
        
        if not await is_equipment_available(equipment, new_date, exclude_booking_id=data['booking_id']):
            await callback_query.message.answer(
                "Оборудование уже занято на выбранную дату. Пожалуйста, выберите другую дату."
            )
            return
        
        cursor.execute("UPDATE bookings SET date = ? WHERE rowid = ?", (new_date, data['booking_id']))
        conn.commit()
        
        await callback_query.message.answer(
            f"Дата бронирования успешно изменена на {new_date}!"
        )
        await state.clear()

# Обработка изменения оборудования
@dp.callback_query(EditingBookingState.choosing_edit_action, lambda c: c.data == "edit_equipment")
async def edit_booking_equipment(callback_query: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cursor.execute("SELECT date FROM bookings WHERE rowid = ?", (data['booking_id'],))
    booking_date = cursor.fetchone()[0]
    
    await state.update_data(current_date=booking_date)
    await state.set_state(BookingState.choosing_category)
    await callback_query.message.answer(
        "Теперь вы можете добавить новое оборудование. "
        "Старое оборудование будет заменено.\n"
        "Выберите категорию:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=cat)] for cat in EQUIPMENT.keys()] +
                     [[KeyboardButton(text="Отмена редактирования")]],
            resize_keyboard=True
        )
    )

# Обработка отмены редактирования
@dp.callback_query(EditingBookingState.choosing_edit_action, lambda c: c.data == "cancel_edit")
async def cancel_editing(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback_query.message.answer(
        "Редактирование отменено.",
        reply_markup=main_menu_keyboard
    )

# Подтверждение бронирования
async def confirm_booking(message: Message, state: FSMContext):
    data = await state.get_data()
    date = data["date"]
    items = data.get("items", {})
    
    total_price = 0
    booking_details = []
    for item, quantity in items.items():
        for category, equipment in EQUIPMENT.items():
            if item in equipment:
                price = equipment[item][1] * quantity
                total_price += price
                booking_details.append(f"{item} x{quantity}")
                break
    
    if 'booking_id' in data:
        booking_id = data['booking_id']
        cursor.execute("DELETE FROM bookings WHERE rowid = ?", (booking_id,))
        conn.commit()
    
    cursor.execute(
        "INSERT INTO bookings (user_id, username, date, equipment, quantity, price) VALUES (?, ?, ?, ?, ?, ?)",
        (message.from_user.id, message.from_user.username, date, "\n".join(booking_details), sum(items.values()), total_price)
    )
    conn.commit()
    
    user_friendly_details = []
    for item, quantity in items.items():
        for category, equipment in EQUIPMENT.items():
            if item in equipment:
                price = equipment[item][1] * quantity
                user_friendly_details.append(f"{item} x{quantity} ({price} руб.)")
                break
    
    await message.answer(f"Вы забронировали:\n" + "\n".join(user_friendly_details) + f"\nИтого: {total_price} руб.")
    await message.answer("Бронирование завершено, спасибо!", reply_markup=main_menu_keyboard)
    await state.clear()

    notification_message = (
        "📢 *Новое бронирование!*\n\n"
        f"📅 *Дата:* {date}\n"
        f"👤 *Пользователь:* @{message.from_user.username}\n"
        f"📦 *Оборудование:*\n" + "\n".join(user_friendly_details) + "\n"
        f"💵 *Итого:* {total_price} руб.\n\n"
    )
    await send_notification_to_chat(notification_message)

# Завершение работы бота
async def on_shutdown(dp):
    conn.close()
    logging.info("Закрытие соединения с базой данных")

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"Ошибка: {e}")
    finally:
        conn.close()
