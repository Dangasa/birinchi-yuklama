import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "moliya.db")
TZ = ZoneInfo("Asia/Tashkent")

MENU = ReplyKeyboardMarkup(
    [["💵 Kirim qo‘shish", "💸 Chiqim qo‘shish"],
     ["📊 Oylik hisobot", "📅 Kunlik hisobot"],
     ["💰 Balans", "📂 Kategoriyalar"],
     ["📜 Tarix"]],
    resize_keyboard=True
)
OUT_CATEGORIES = ["🍔 Oziq-ovqat", "⛽ Benzin", "🚗 Avtomobil", "🛍 Xaridlar",
                  "🏠 Uy-ro‘zg‘or", "☕ Kafe va dam olish", "📱 Telefon", "🧾 To‘lovlar", "📦 Boshqa"]
IN_CATEGORIES = ["💼 Oylik", "🧰 Qo‘shimcha ish", "🛒 Savdo", "🎁 Sovg‘a", "📦 Boshqa"]
CANCEL = "❌ Bekor qilish"

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        amount REAL NOT NULL,
        category TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    return conn

def money(value):
    return f"{value:,.0f}".replace(",", " ") + " so‘m"

def is_owner(update):
    return OWNER_ID != 0 and update.effective_user and update.effective_user.id == OWNER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("Bu bot shaxsiy foydalanish uchun.")
        return
    context.user_data.clear()
    await update.message.reply_text(
        "Assalomu alaykum! Moliya hisobchingiz tayyor.\n"
        "Kirim/chiqimni tanlang, kategoriya, summa va izohni kiriting.",
        reply_markup=MENU
    )

async def begin_entry(update, context, kind):
    context.user_data.clear()
    context.user_data["pending_kind"] = kind
    cats = IN_CATEGORIES if kind == "in" else OUT_CATEGORIES
    context.user_data["categories"] = cats
    keyboard = [[c] for c in cats] + [[CANCEL]]
    await update.message.reply_text(
        ("💵 Kirim" if kind == "in" else "💸 Chiqim") + ": kategoriyani tanlang.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("Bu bot shaxsiy foydalanish uchun.")
        return
    text = update.message.text.strip()

    if text == CANCEL:
        context.user_data.clear()
        await update.message.reply_text("Bekor qilindi.", reply_markup=MENU)
        return

    if context.user_data.get("pending_kind"):
        stage = context.user_data.get("stage", "category")
        if stage == "category":
            if text not in context.user_data["categories"]:
                await update.message.reply_text("Iltimos, ro‘yxatdan kategoriya tanlang.")
                return
            context.user_data["category"] = text
            context.user_data["stage"] = "amount"
            await update.message.reply_text("Summani so‘mda kiriting. Masalan: 150000")
            return
        if stage == "amount":
            raw = text.replace(" ", "").replace(",", "").replace(".", "")
            try:
                amount = float(raw)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("Summani raqam bilan kiriting. Masalan: 150000")
                return
            context.user_data["amount"] = amount
            context.user_data["stage"] = "note"
            await update.message.reply_text("Nima uchun yoki qayerdan tushganini yozing. Izoh kerak bo‘lmasa — yozing: -")
            return
        if stage == "note":
            kind = context.user_data["pending_kind"]
            amount = context.user_data["amount"]
            category = context.user_data["category"]
            note = text if text != "-" else "Izohsiz"
            now = datetime.now(TZ).isoformat(timespec="seconds")
            with db() as conn:
                conn.execute(
                    "INSERT INTO transactions(owner_id,kind,amount,category,note,created_at) VALUES(?,?,?,?,?,?)",
                    (OWNER_ID, kind, amount, category, note, now)
                )
            context.user_data.clear()
            await update.message.reply_text(
                f"✅ Saqlandi!\n{'💵 Kirim' if kind == 'in' else '💸 Chiqim'}: {money(amount)}\n"
                f"📂 Kategoriya: {category}\n📝 Izoh: {note}",
                reply_markup=MENU
            )
            return

    if text == "💵 Kirim qo‘shish":
        await begin_entry(update, context, "in")
    elif text == "💸 Chiqim qo‘shish":
        await begin_entry(update, context, "out")
    elif text == "📊 Oylik hisobot":
        await report(update, context, "month")
    elif text == "📅 Kunlik hisobot":
        await report(update, context, "day")
    elif text == "💰 Balans":
        await balance(update, context)
    elif text == "📂 Kategoriyalar":
        await category_report(update, context)
    elif text == "📜 Tarix":
        await history(update, context)
    else:
        await update.message.reply_text("Menyudan birini tanlang.", reply_markup=MENU)

def totals(owner_id, start=None, end=None):
    query = "SELECT kind, COALESCE(SUM(amount),0) FROM transactions WHERE owner_id=?"
    args = [owner_id]
    if start:
        query += " AND created_at>=?"
        args.append(start)
    if end:
        query += " AND created_at<?"
        args.append(end)
    query += " GROUP BY kind"
    with db() as conn:
        rows = dict(conn.execute(query, args).fetchall())
    return rows.get("in", 0), rows.get("out", 0)

async def report(update, context, period):
    now = datetime.now(TZ)
    if period == "month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt.replace(year=start_dt.year+1, month=1) if start_dt.month == 12
                  else start_dt.replace(month=start_dt.month+1))
        title = now.strftime("%B %Y")
    else:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=1)
        title = now.strftime("%d.%m.%Y")
    income, expense = totals(OWNER_ID, start_dt.isoformat(), end_dt.isoformat())
    await update.message.reply_text(
        f"📊 {title} hisoboti\n\n💵 Kirim: {money(income)}\n"
        f"💸 Chiqim: {money(expense)}\n💰 Farq: {money(income-expense)}",
        reply_markup=MENU
    )

async def balance(update, context):
    income, expense = totals(OWNER_ID)
    await update.message.reply_text(
        f"💰 Umumiy hisob\n\n💵 Jami kirim: {money(income)}\n"
        f"💸 Jami chiqim: {money(expense)}\n📌 Balans: {money(income-expense)}",
        reply_markup=MENU
    )

async def category_report(update, context):
    now = datetime.now(TZ)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    with db() as conn:
        rows = conn.execute(
            "SELECT category, SUM(amount) FROM transactions "
            "WHERE owner_id=? AND kind='out' AND created_at>=? "
            "GROUP BY category ORDER BY SUM(amount) DESC", (OWNER_ID, start)
        ).fetchall()
    if not rows:
        msg = "Bu oycha chiqimlar kiritilmagan."
    else:
        msg = "📂 Shu oydagi xarajatlar kategoriyalar bo‘yicha:\n\n"
        msg += "\n".join(f"{cat}: {money(amount)}" for cat, amount in rows)
    await update.message.reply_text(msg, reply_markup=MENU)

async def history(update, context):
    with db() as conn:
        rows = conn.execute(
            "SELECT kind,amount,category,note,created_at FROM transactions "
            "WHERE owner_id=? ORDER BY id DESC LIMIT 10", (OWNER_ID,)
        ).fetchall()
    if not rows:
        await update.message.reply_text("Hozircha yozuvlar yo‘q.", reply_markup=MENU)
        return
    lines = ["📜 Oxirgi 10 ta operatsiya:"]
    for kind, amount, category, note, created in rows:
        sign = "➕" if kind == "in" else "➖"
        date = datetime.fromisoformat(created).strftime("%d.%m %H:%M")
        lines.append(f"{sign} {money(amount)} | {category}\n{note} · {date}")
    await update.message.reply_text("\n\n".join(lines), reply_markup=MENU)

def main():
    if not TOKEN or OWNER_ID == 0:
        raise RuntimeError("BOT_TOKEN va OWNER_ID muhit o‘zgaruvchilarini sozlang.")
    db().close()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()

if __name__ == "__main__":
    main()
