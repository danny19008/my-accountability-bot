import os
import logging
import sqlite3
import datetime
import re
from zoneinfo import ZoneInfo
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes, CallbackQueryHandler, PicklePersistence
)

# --- CONFIGURATION ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_CHAT_ID = -5102116450  # Ensure this matches your Group ID
NIGERIA_TZ = ZoneInfo("Africa/Lagos")

# KOYEB PERSISTENCE PATHS
# These point to the mounted Volume (usually /data)
DB_FILE = "/data/accountability.db"
PERSISTENCE_FILE = "/data/bot_persistence"

# --- STATES ---
WAKE, SLEEP, BIBLE, BIBLE_PASSAGE, PRAYER, LEARNING, INTEGRITY = range(7)

# --- DATABASE SETUP ---
def init_db():
    # Ensure the directory exists (Koyeb should handle this, but it's safe)
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (user_id INTEGER PRIMARY KEY, username TEXT, streak INTEGER DEFAULT 0, last_checkin TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS checkins 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date TEXT, 
                      wake TEXT, sleep TEXT, bible TEXT, passage TEXT, 
                      prayer TEXT, learning TEXT, integrity TEXT, sleep_hours REAL)''')
        conn.commit()

# --- UTILS ---
def calculate_sleep_hours(sleep_str, wake_str):
    try:
        fmt = "%H:%M"
        s = datetime.datetime.strptime(sleep_str, fmt)
        w = datetime.datetime.strptime(wake_str, fmt)
        if w <= s: 
            diff = (w + timedelta(days=1)) - s
        else:
            diff = w - s
        return round(diff.total_seconds() / 3600, 2)
    except Exception:
        return 0.0

# --- AUTOMATION JOBS ---

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_FILE) as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()
    for (uid,) in users:
        try:
            btn = [[InlineKeyboardButton("🌅 Start Check-in", callback_data="start_flow")]]
            await context.bot.send_message(uid, "Good morning! 🌅\nTime to log your daily habits.", reply_markup=InlineKeyboardMarkup(btn))
        except Exception: continue

async def daily_broadcasts(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.datetime.now(NIGERIA_TZ).date().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        insights = conn.execute("SELECT u.username, c.learning FROM checkins c JOIN users u ON c.user_id = u.user_id WHERE c.date = ?", (today,)).fetchall()
        if insights:
            msg = "💡 *Daily Community Insights*\n\n"
            for user, text in insights: msg += f"👤 *@{user}*: {text}\n"
            await context.bot.send_message(GROUP_CHAT_ID, msg, parse_mode='Markdown')
        
        missed = conn.execute("SELECT username FROM users WHERE user_id NOT IN (SELECT user_id FROM checkins WHERE date = ?)", (today,)).fetchall()
        if missed:
            names = ", ".join([f"@{m[0]}" for m in missed])
            await context.bot.send_message(GROUP_CHAT_ID, f"⚠️ *Missing Check-ins*: {names}", parse_mode='Markdown')

async def weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.datetime.now(NIGERIA_TZ).date()
    if today.weekday() != 6: return # Only Sunday
    
    start_date = (today - timedelta(days=6)).isoformat()
    report = f"📊 *Weekly Summary* ({start_date} to {today.isoformat()})\n\n"
    with sqlite3.connect(DB_FILE) as conn:
        users = conn.execute("SELECT user_id, username, streak FROM users").fetchall()
        for uid, name, streak in users:
            stats = conn.execute("SELECT AVG(sleep_hours), COUNT(CASE WHEN bible='Yes' THEN 1 END) FROM checkins WHERE user_id=? AND date BETWEEN ? AND ?", (uid, start_date, today.isoformat())).fetchone()
            report += f"👤 *@{name}*\n🔥 Streak: {streak}d | 🌙 Sleep: {round(stats[0] or 0, 1)}h | 📖 Bible: {stats[1]}/7\n\n"
    await context.bot.send_message(GROUP_CHAT_ID, report, parse_mode='Markdown')

# --- CONVERSATION HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user.id, user.username or user.first_name))
        conn.commit()
    btn = [[InlineKeyboardButton("🌅 Start Daily Check-in", callback_data="start_flow")]]
    txt = f"Good morning, {user.first_name}! 🌞\nReady for your accountability check?"
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(btn))
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(btn))
    return WAKE

async def handle_wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = [[InlineKeyboardButton("05:00", callback_data="time_05:00"), InlineKeyboardButton("06:00", callback_data="time_06:00")],
            [InlineKeyboardButton("07:00", callback_data="time_07:00"), InlineKeyboardButton("08:00", callback_data="time_08:00")]]
    if update.callback_query and update.callback_query.data == "start_flow":
        await update.callback_query.edit_message_text("Step 1/5: Wake-up time? 🌅", reply_markup=InlineKeyboardMarkup(btns))
        return WAKE
    val = update.callback_query.data.replace("time_", "") if update.callback_query else update.message.text
    if not re.match(r"^\d{2}:\d{2}$", val):
        await update.effective_message.reply_text("⚠️ Use HH:MM format.")
        return WAKE
    context.user_data['wake'] = val
    txt = f"✅ Recorded: {val}\nStep 2/5: Bedtime? (HH:MM) 🌙"
    if update.callback_query: await update.callback_query.edit_message_text(txt)
    else: await update.message.reply_text(txt)
    return SLEEP

async def handle_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text
    if not re.match(r"^\d{2}:\d{2}$", val):
        await update.message.reply_text("⚠️ Use HH:MM format.")
        return SLEEP
    context.user_data['sleep'] = val
    kb = [[InlineKeyboardButton("✅ Yes", callback_data="b_y"), InlineKeyboardButton("❌ No", callback_data="b_n")]]
    await update.message.reply_text("Step 3/5 📖 Studied your Bible?", reply_markup=InlineKeyboardMarkup(kb))
    return BIBLE

async def bible_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "b_y":
        context.user_data['bible'] = "Yes"
        await query.edit_message_text("Step 3/5 📖 Which book/passage?")
        return BIBLE_PASSAGE
    context.user_data['bible'] = "No"
    context.user_data['passage'] = "N/A"
    return await ask_prayer(query)

async def handle_passage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['passage'] = update.message.text
    return await ask_prayer(update.message)

async def ask_prayer(msg_obj):
    kb = [[InlineKeyboardButton("✅ Yes", callback_data="p_y"), InlineKeyboardButton("❌ No", callback_data="p_n")]]
    txt = "Step 4/5 🙏 Did you pray today?"
    if hasattr(msg_obj, 'edit_message_text'): await msg_obj.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    else: await msg_obj.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
    return PRAYER

async def prayer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['prayer'] = "Yes" if query.data == "p_y" else "No"
    await query.edit_message_text("Step 5/5 🧠 One thing you learned?")
    return LEARNING

async def handle_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['learning'] = update.message.text
    summary = (f"📋 *Review:*\n🌅 Wake: {context.user_data['wake']}\n🌙 Sleep: {context.user_data['sleep']}\n"
               f"📖 Bible: {context.user_data['bible']}\n🙏 Prayer: {context.user_data['prayer']}\n"
               f"🧠 Learned: {context.user_data['learning']}")
    kb = [[InlineKeyboardButton("✅ Confirm", callback_data="conf_y"), InlineKeyboardButton("✏️ Edit", callback_data="edit")]]
    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return INTEGRITY

async def integrity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "edit": return await start(update, context)

    uid = query.from_user.id
    today = datetime.datetime.now(NIGERIA_TZ).date()
    hrs = calculate_sleep_hours(context.user_data.get('sleep', '00:00'), context.user_data.get('wake', '00:00'))

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO checkins (user_id, date, wake, sleep, bible, passage, prayer, learning, integrity, sleep_hours) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (uid, today.isoformat(), context.user_data.get('wake'), context.user_data.get('sleep'), context.user_data.get('bible'), 
                        context.user_data.get('passage'), context.user_data.get('prayer'), context.user_data.get('learning'), "Yes", hrs))
        
        row = cursor.execute("SELECT last_checkin, streak FROM users WHERE user_id=?", (uid,)).fetchone()
        last_str, streak = row if row else (None, 0)
        new_streak = 1
        grace = ""

        if last_str:
            last_date = datetime.datetime.strptime(last_str, "%Y-%m-%d").date()
            diff = (today - last_date).days
            if diff == 0: new_streak = streak
            elif diff <= 2: # Streak or 1 Grace Day
                new_streak = streak + 1
                if diff == 2: grace = "⏳ Grace day applied!"
            else: grace = "⚠️ Streak reset."
        
        cursor.execute("UPDATE users SET streak=?, last_checkin=? WHERE user_id=?", (new_streak, today.isoformat(), uid))
        conn.commit()

    await query.edit_message_text(f"🔥 Recorded! Streak: {new_streak} days.\n{grace}")
    return ConversationHandler.END

# --- MAIN ---
def main():
    init_db()
    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
    app = ApplicationBuilder().token(TOKEN).persistence(persistence).build()

    # Scheduler
    jq = app.job_queue
    jq.run_daily(morning_job, time=datetime.time(6, 0, tzinfo=NIGERIA_TZ))
    jq.run_daily(daily_broadcasts, time=datetime.time(21, 0, tzinfo=NIGERIA_TZ))
    jq.run_daily(weekly_summary, time=datetime.time(20, 0, tzinfo=NIGERIA_TZ))

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start), CallbackQueryHandler(handle_wake, pattern="^start_flow$")],
        states={
            WAKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wake), CallbackQueryHandler(handle_wake, pattern="^time_")],
            SLEEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sleep)],
            BIBLE: [CallbackQueryHandler(bible_callback)],
            BIBLE_PASSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_passage)],
            PRAYER: [CallbackQueryHandler(prayer_callback)],
            LEARNING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_learning)],
            INTEGRITY: [CallbackQueryHandler(integrity_callback)],
        },
        fallbacks=[CommandHandler('start', start)],
        name="accountability_flow", persistent=True
    )

    app.add_handler(conv)
    app.run_polling()

if __name__ == "__main__":
    main()
