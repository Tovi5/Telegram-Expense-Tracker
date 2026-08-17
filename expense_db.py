import sqlite3
from datetime import datetime

# DB_PATH = 'expenses.db'

import os
DB_PATH = os.getenv('DB_PATH', 'expenses.db')


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
        ''')


def add_expense(user_id, amount, category, note=None):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            'INSERT INTO expenses (user_id, amount, category, note, created_at) VALUES (?, ?, ?, ?, ?)',
            (user_id, amount, category, note, datetime.now().isoformat(timespec='seconds'))
        )
        return cursor.lastrowid


def get_expenses(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            'SELECT * FROM expenses WHERE user_id = ? ORDER BY id DESC', (user_id,)
        ).fetchall()


def delete_expense(user_id, expense_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            'DELETE FROM expenses WHERE id = ? AND user_id = ?', (expense_id, user_id)
        )
        return cursor.rowcount > 0


def get_summary(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        by_category = conn.execute(
            '''SELECT category, SUM(amount) AS total FROM expenses
               WHERE user_id = ? GROUP BY category ORDER BY total DESC''',
            (user_id,)
        ).fetchall()
        grand_total = conn.execute(
            'SELECT SUM(amount) FROM expenses WHERE user_id = ?', (user_id,)
        ).fetchone()[0] or 0
        return by_category, grand_total
