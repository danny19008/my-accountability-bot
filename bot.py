import os
import logging
import sqlite3
import datetime
import random
import re
import io

from datetime import timedelta, time
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    PicklePersistence,
    filters,
)

# ---------------- CONFIG ---------------- #

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NIGERIA_TZ = ZoneInfo("Africa/Lagos")
DB_FILE = "accountability.db"
PERSISTENCE_FILE = "bot_persistence"

WAKE, SLEEP, BIBLE, PASSAGE, PRAYER, LEARNING, SOURCE, INTEGRITY = range(8)

# ---------------- DATABASE ---------------- #

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            streak INTEGER DEFAULT 0,
            last_checkin TEXT,
            weekly_goal TEXT,
            home_group_id INTEGER
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS checkins(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            wake TEXT,
            sleep TEXT,
            bible TEXT,
            passage TEXT,
            prayer TEXT,
            learning TEXT,
            source TEXT,
            integrity TEXT,
            sleep_hours REAL,
            score INTEGER,
            UNIQUE(user_id,date)
        )
        """)
        conn.commit()

# ---------------- UTILS ---------------- #

def escape_md(text):
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

def validate_time_format(time_str):
    try:
        datetime.datetime.strptime(time_str, "%H:%M")
        return True
    except:
        return False

def calculate_sleep_hours(sleep_str, wake_str):
    fmt = "%H:%M"
    try:
        s = datetime.datetime.strptime(sleep_str, fmt)
        w = datetime.datetime.strptime(wake_str, fmt)
        diff = w - s
        if diff.total_seconds() <= 0:
            diff += timedelta(days=1)
        return round(diff.total_seconds()/3600,2)
    except:
        return 0.0

def calculate_score(data):
    score = 0
    try:
        wake_hour = int(data["wake"].split(":")[0])
        if wake_hour <=6: score+=2
    except: pass
    if data.get("bible")=="Yes": score+=2
    if data.get("prayer")=="Yes": score+=2
    if data.get("learning") and len(data.get("learning").strip())>3: score+=2
    if data.get("source") and len(data.get("source").strip())>3: score+=2
    if data.get("integrity")=="Yes": score+=2
    return score

def update_streak(user_id, today_iso):
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute("SELECT streak,last_checkin FROM users WHERE user_id=?",(user_id,)).fetchone()
        streak = 0
        last_checkin = None
        if row:
            streak,last_checkin=row
        yesterday=(datetime.datetime.now(NIGERIA_TZ).date()-timedelta(days=1)).isoformat()
        if last_checkin==yesterday:
            streak+=1
        else:
            streak=1
        conn.execute("UPDATE users SET streak=?, last_checkin=? WHERE user_id=?",(streak,today_iso,user_id))
        return streak

# ---------------- COMMANDS ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id,username) VALUES (?,?)",
            (user.id, user.username)
        )
    await update.message.reply_text(
        "👋 Welcome to the Accountability Bot!\n\n"
        "Use /checkin daily.\n"
        "Use /leaderboard to see streaks.\n"
        "Use /stats to see your trend.\n"
        "Use /goal to set weekly goal.\n"
        "Use /setgroup to register your chat for weekly tables.\n"
        "You can cancel any check-in with /cancel."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Check-in cancelled.")
    return ConversationHandler.END

async def set_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE users SET home_group_id=? WHERE user_id=?",(chat_id,user_id))
    await update.message.reply_text(f"✅ This chat is now set for your weekly progress tables.")

# ---------------- CHECK-IN FLOW ---------------- #

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkin"] = {}
    await update.message.reply_text("⏰ What time did you wake up? (HH:MM)")
    return WAKE

async def handle_wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not validate_time_format(text):
        await update.message.reply_text("❌ Invalid format. Enter wake-up time as HH:MM")
        return WAKE
    context.user_data["checkin"]["wake"] = text
    await update.message.reply_text("🌙 What time did you sleep? (HH:MM)")
    return SLEEP

async def handle_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not validate_time_format(text):
        await update.message.reply_text("❌ Invalid format. Enter sleep time as HH:MM")
        return SLEEP
    context.user_data["checkin"]["sleep"] = text
    keyboard=[[InlineKeyboardButton("Yes",callback_data="Yes"),
               InlineKeyboardButton("No",callback_data="No")]]
    await update.message.reply_text(
        "📖 Did you read the Bible today?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return BIBLE

async def bible_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["checkin"]["bible"]=query.data
    if query.data=="Yes":
        await query.message.reply_text("Which passage?")
        return PASSAGE
    context.user_data["checkin"]["passage"]=""
    return await ask_prayer(query, context)

async def handle_passage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkin"]["passage"]=update.message.text.strip()
    return await ask_prayer(update, context)

async def ask_prayer(update_obj, context):
    keyboard=[[InlineKeyboardButton("Yes",callback_data="Yes"),
               InlineKeyboardButton("No",callback_data="No")]]
    await update_obj.message.reply_text(
        "🙏 Did you pray today?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PRAYER

async def prayer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["checkin"]["prayer"]=query.data
    await query.message.reply_text("📚 What did you learn today?")
    return LEARNING

async def handle_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkin"]["learning"]=update.message.text.strip()
    await update.message.reply_text("📚 Source of learning?")
    return SOURCE

async def handle_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["checkin"]["source"]=update.message.text.strip()
    keyboard=[[InlineKeyboardButton("Yes",callback_data="Yes"),
               InlineKeyboardButton("No",callback_data="No")]]
    await update.message.reply_text(
        "🧭 Did you live with integrity today?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return INTEGRITY

async def integrity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    data=context.user_data["checkin"]
    data["integrity"]=query.data
    sleep_hours=calculate_sleep_hours(data["sleep"],data["wake"])
    score=calculate_score(data)
    today=datetime.datetime.now(NIGERIA_TZ).date().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
        INSERT OR REPLACE INTO checkins(
        user_id,date,wake,sleep,bible,passage,prayer,
        learning,source,integrity,sleep_hours,score
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,(query.from_user.id,today,data["wake"],data["sleep"],data["bible"],
             data["passage"],data["prayer"],data["learning"],data["source"],
             data["integrity"],sleep_hours,score))
    streak = update_streak(query.from_user.id, today)
    await query.message.reply_text(f"✅ Check-in saved! Score: {score}/12 | Current streak: {streak} days")
    return ConversationHandler.END

# ---------------- LEADERBOARD ---------------- #

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_FILE) as conn:
        users = conn.execute("SELECT username, streak FROM users ORDER BY streak DESC LIMIT 10").fetchall()
    if not users:
        await update.message.reply_text("No streaks yet!")
        return
    text = "🏆 Top Streaks\n\n"
    for i,(name,streak) in enumerate(users,1):
        medal="🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else "🔥"
        text+=f"{medal} {escape_md(name)}: {streak} days\n"
    await update.message.reply_text(text,parse_mode=constants.ParseMode.MARKDOWN_V2)

# ---------------- STATS ---------------- #

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    with sqlite3.connect(DB_FILE) as conn:
        data = conn.execute("SELECT date,score FROM checkins WHERE user_id=? ORDER BY date DESC LIMIT 7",(uid,)).fetchall()
    if len(data)<2:
        await update.message.reply_text("Need at least 2 days of data.")
        return
    dates=[d[0][-5:] for d in reversed(data)]
    scores=[d[1] for d in reversed(data)]
    plt.figure(figsize=(8,4))
    plt.plot(dates,scores,marker='o',color='#0088cc',linewidth=2)
    plt.title(f"Score Progress (Last {len(data)} Days)")
    plt.ylim(0,13)
    plt.grid(True,linestyle='--',alpha=0.6)
    buf=io.BytesIO()
    plt.savefig(buf,format='png')
    buf.seek(0)
    plt.close()
    await update.message.reply_photo(photo=buf,caption="📊 Your performance trend")

# ---------------- WEEKLY GOAL ---------------- #

async def set_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    goal_text=" ".join(context.args)
    if not goal_text:
        await update.message.reply_text("Usage: /goal Your weekly goal")
        return
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE users SET weekly_goal=? WHERE user_id=?",(goal_text,update.effective_user.id))
    await update.message.reply_text(f"🎯 Weekly goal set: *{escape_md(goal_text)}*",parse_mode=constants.ParseMode.MARKDOWN_V2)

# ---------------- DAILY KNOWLEDGE DIGEST ---------------- #

async def daily_knowledge_digest(context: ContextTypes.DEFAULT_TYPE):
    today=datetime.datetime.now(NIGERIA_TZ).date().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("""
        SELECT users.username,learning
        FROM checkins JOIN users ON users.user_id=checkins.user_id
        WHERE date=?
        """,(today,)).fetchall()
    if not rows: return
    text="🧠 Daily Knowledge Digest\n\n"
    for name,learn in rows:
        text+=f"• {escape_md(name)}: {escape_md(learn)}\n"
    groups = context.bot_data.get("groups",[])
    for gid in groups:
        await context.bot.send_message(chat_id=gid,text=text,parse_mode=constants.ParseMode.MARKDOWN_V2)

# ---------------- THROWBACK ---------------- #

async def throwback(context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("""
        SELECT users.username,learning,date
        FROM checkins JOIN users ON users.user_id=checkins.user_id
        WHERE learning!=''
        """).fetchall()
    if not rows: return
    name, learn, date = random.choice(rows)
    text=f"⏪ Throwback\n\n{escape_md(name)} on {date} learned:\n\n{escape_md(learn)}"
    groups=context.bot_data.get("groups",[])
    for gid in groups:
        await context.bot.send_message(chat_id=gid,text=text,parse_mode=constants.ParseMode.MARKDOWN_V2)

# ---------------- MOBILE-FRIENDLY WEEKLY TABLES ---------------- #

async def send_weekly_progress_tables(context: ContextTypes.DEFAULT_TYPE):
    today=datetime.datetime.now(NIGERIA_TZ).date()
    start=today-timedelta(days=6)
    with sqlite3.connect(DB_FILE) as conn:
        users=conn.execute("SELECT user_id,username,home_group_id FROM users").fetchall()
    messages_per_group={}
    for uid,username,group_id in users:
        if not group_id: continue
        with sqlite3.connect(DB_FILE) as conn:
            rows=conn.execute("""
            SELECT date,bible,prayer,learning,sleep_hours
            FROM checkins
            WHERE user_id=? AND date BETWEEN ? AND ?
            """,(uid,start.isoformat(),today.isoformat())).fetchall()
        data_map={r[0]:r[1:] for r in rows}
        table=[f"👤 {escape_md(username)}","```"]
        table.append("Day       | Bible Study   | Prayer | Learn | Sleep")
        table.append("----------|---------------|--------|-------|------")
        for i in range(7):
            day=start+timedelta(days=i)
            day_name=day.strftime("%a")
            record=data_map.get(day.isoformat())
            if record:
                bible,prayer,learning,sleep=record
                bible=escape_md(bible[:12]) if bible else ""
                prayer_icon="✅" if prayer=="Yes" else "❌"
                learning_icon="✅" if learning else "❌"
                sleep_val=f"{sleep}" if sleep else "❌"
            else:
                bible=""
                prayer_icon="❌"
                learning_icon="❌"
                sleep_val="❌"
            row=f"{day_name:<9}| {bible:<13}| {prayer_icon:^6}| {learning_icon:^5}| {sleep_val:^4}"
            table.append(row)
        table.append("```")
        table_text="\n".join(table)+"\n\n"
        if group_id not in messages_per_group:
            messages_per_group[group_id]=[]
        messages_per_group[group_id].append(table_text)
    for gid,tables in messages_per_group.items():
        message=""
        for table in tables:
            if len(message)+len(table)>4096:
                await context.bot.send_message(chat_id=gid,text=message,parse_mode=constants.ParseMode.MARKDOWN_V2)
                message=table
            else:
                message+=table
        if message:
            await context.bot.send_message(chat_id=gid,text=message,parse_mode=constants.ParseMode.MARKDOWN_V2)

# ---------------- MAIN ---------------- #

def main():
    init_db()
    persistence=PicklePersistence(filepath=PERSISTENCE_FILE)
    app=ApplicationBuilder().token(TOKEN).persistence(persistence).build()
    
    conv=ConversationHandler(
        entry_points=[CommandHandler("checkin",checkin)],
        states={
            WAKE:[MessageHandler(filters.TEXT & ~filters.COMMAND,handle_wake)],
            SLEEP:[MessageHandler(filters.TEXT & ~filters.COMMAND,handle_sleep)],
            BIBLE:[CallbackQueryHandler(bible_callback)],
            PASSAGE:[MessageHandler(filters.TEXT & ~filters.COMMAND,handle_passage)],
            PRAYER:[CallbackQueryHandler(prayer_callback)],
            LEARNING:[MessageHandler(filters.TEXT & ~filters.COMMAND,handle_learning)],
            SOURCE:[MessageHandler(filters.TEXT & ~filters.COMMAND,handle_source)],
            INTEGRITY:[CallbackQueryHandler(integrity_callback)]
        },
        fallbacks=[CommandHandler("cancel",cancel)]
    )
    
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("setgroup",set_group))
    app.add_handler(CommandHandler("leaderboard",leaderboard))
    app.add_handler(CommandHandler("stats",stats))
    app.add_handler(CommandHandler("goal",set_goal))
    app.add_handler(conv)
    
    if app.job_queue:
        app.job_queue.run_daily(daily_knowledge_digest,time=time(21,30,tzinfo=NIGERIA_TZ))
        app.job_queue.run_daily(throwback,time=time(15,0,tzinfo=NIGERIA_TZ))
        app.job_queue.run_daily(send_weekly_progress_tables,time=time(21,0,tzinfo=NIGERIA_TZ),days=(6,))
    
    app.run_polling()

if __name__=="__main__":
    main()
