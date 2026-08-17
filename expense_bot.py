import sys
import logging
from datetime import datetime

# Category labels contain emoji; Windows terminals often default to a
# legacy encoding (cp1252) that can't print them, which would otherwise
# crash logging.StreamHandler the first time a category gets logged.
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from config import EXPENSE_BOT_TOKEN
import telebot
from telebot import custom_filters
from telebot.states import State, StatesGroup
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
)

import expense_db as db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.FileHandler('expense_bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger('expense_bot')

bot = telebot.TeleBot(token=EXPENSE_BOT_TOKEN)
bot.add_custom_filter(custom_filters.StateFilter(bot))

db.init_db()

CATEGORIES = {
    'food': '🍔 Food',
    'transport': '🚌 Transport',
    'shopping': '🛍 Shopping',
    'bills': '🧾 Bills',
    'entertainment': '🎬 Entertainment',
    'other': '✏️ Other',
}

PAGE_SIZE = 5


class AddExpense(StatesGroup):
    amount = State()
    category = State()
    custom_category = State()
    note = State()


class BotExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        logger.exception("Unhandled exception: %s", exception)
        return True


bot.exception_handler = BotExceptionHandler()


# ---------------- /add flow ----------------

@bot.message_handler(commands=['add'])
def add_start(message):
    bot.set_state(message.from_user.id, AddExpense.amount, message.chat.id)
    bot.send_message(message.chat.id, "How much did you spend? (e.g. 12.50)")


@bot.message_handler(commands=['cancel'])
def cancel_flow(message):
    bot.delete_state(message.from_user.id, message.chat.id)
    bot.send_message(message.chat.id, "Cancelled.")


@bot.message_handler(state=AddExpense.amount)
def add_amount(message):
    try:
        amount = round(float(message.text.replace(',', '.')), 2)
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(message.chat.id, "That's not a valid amount. Try again (e.g. 12.50), or /cancel.")
        return

    with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
        data['amount'] = amount

    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(*[
        InlineKeyboardButton(label, callback_data=f"cat:{code}")
        for code, label in CATEGORIES.items()
    ])
    bot.set_state(message.from_user.id, AddExpense.category, message.chat.id)
    bot.send_message(message.chat.id, "Pick a category:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith('cat:'), state=AddExpense.category)
def add_category(call):
    code = call.data.split(':')[1]
    bot.answer_callback_query(call.id)

    if code == 'other':
        bot.set_state(call.from_user.id, AddExpense.custom_category, call.message.chat.id)
        bot.send_message(call.message.chat.id, "Type a category name:")
        return

    with bot.retrieve_data(call.from_user.id, call.message.chat.id) as data:
        data['category'] = CATEGORIES[code]
    bot.set_state(call.from_user.id, AddExpense.note, call.message.chat.id)
    bot.send_message(call.message.chat.id, "Add a note? Send it now, or /skip.")


@bot.message_handler(state=AddExpense.custom_category)
def add_custom_category(message):
    with bot.retrieve_data(message.from_user.id, message.chat.id) as data:
        data['category'] = message.text.strip()
    bot.set_state(message.from_user.id, AddExpense.note, message.chat.id)
    bot.send_message(message.chat.id, "Add a note? Send it now, or /skip.")


@bot.message_handler(commands=['skip'], state=AddExpense.note)
def add_note_skip(message):
    save_expense(message.from_user.id, message.chat.id, note=None)


@bot.message_handler(state=AddExpense.note)
def add_note(message):
    save_expense(message.from_user.id, message.chat.id, note=message.text.strip())


def save_expense(user_id, chat_id, note):
    with bot.retrieve_data(user_id, chat_id) as data:
        amount = data['amount']
        category = data['category']
    db.add_expense(user_id, amount, category, note)
    bot.delete_state(user_id, chat_id)
    logger.info("User %s logged %.2f in %s", user_id, amount, category)
    note_line = f"\nNote: {note}" if note else ""
    bot.send_message(chat_id, f"Saved: {amount:.2f} — {category}{note_line}")


# ---------------- /history (paginated) ----------------

def build_history_page(user_id, page):
    expenses = db.get_expenses(user_id)
    total_pages = max(1, -(-len(expenses) // PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    page_items = expenses[start:start + PAGE_SIZE]

    if not page_items:
        text = "No expenses logged yet. Use /add to log one."
    else:
        lines = [f"Page {page + 1}/{total_pages}", ""]
        for row in page_items:
            note_part = f" ({row['note']})" if row['note'] else ""
            when = datetime.fromisoformat(row['created_at']).strftime('%d %b %Y, %H:%M')
            lines.append(f"#{row['id']} — {row['amount']:.2f} — {row['category']}{note_part} — {when}")
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"hist:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"hist:{page + 1}"))
    if nav:
        keyboard.row(*nav)
    for row in page_items:
        keyboard.row(InlineKeyboardButton(f"🗑 Delete #{row['id']}", callback_data=f"del:{row['id']}:{page}"))

    return text, keyboard


@bot.message_handler(commands=['history'])
def show_history(message):
    text, keyboard = build_history_page(message.from_user.id, 0)
    bot.send_message(message.chat.id, text, reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith('hist:'))
def paginate_history(call):
    page = int(call.data.split(':')[1])
    text, keyboard = build_history_page(call.from_user.id, page)
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=keyboard)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('del:'))
def delete_expense(call):
    _, expense_id, page = call.data.split(':')
    deleted = db.delete_expense(call.from_user.id, int(expense_id))
    bot.answer_callback_query(call.id, "Deleted." if deleted else "Not found.")
    text, keyboard = build_history_page(call.from_user.id, int(page))
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=keyboard)


# ---------------- /summary ----------------

@bot.message_handler(commands=['summary'])
def show_summary(message):
    by_category, grand_total = db.get_summary(message.from_user.id)
    if not by_category:
        bot.send_message(message.chat.id, "No expenses logged yet. Use /add to log one.")
        return
    lines = [f"Total: {grand_total:.2f}", ""]
    for row in by_category:
        lines.append(f"{row['category']}: {row['total']:.2f}")
    bot.send_message(message.chat.id, "\n".join(lines))


# ---------------- /start, /help, command menu ----------------

@bot.message_handler(commands=['start', 'help'])
def send_help(message):
    bot.send_message(
        message.chat.id,
        "Expense tracker\n\n"
        "/add - log a new expense\n"
        "/history - browse past expenses\n"
        "/summary - totals by category\n"
        "/cancel - cancel the current /add flow"
    )


bot.set_my_commands([
    BotCommand("add", "Log a new expense"),
    BotCommand("history", "Browse past expenses"),
    BotCommand("summary", "Totals by category"),
    BotCommand("cancel", "Cancel the current /add flow"),
])


bot.polling()
