import asyncio
import datetime
import html
import json
import logging
import os
import sys
import uuid
import threading
import time
import re
from typing import Dict, List, Optional

# --- REQUIRED DEPENDENCIES ---
# pip install python-telegram-bot[job-queue] fastapi uvicorn requests python-dotenv telethon

# Force UTF-8 for Windows console (prevents emoji errors)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import uvicorn
import requests
import httpx
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from pydantic import BaseModel
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.request import BaseRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telethon import TelegramClient, events

# --- ROBUST REQUEST ENGINE (Fix for Hugging Face Networking) ---
class RequestsRequest(BaseRequest):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Force IPv4 via global setting
        import urllib3.util.connection as connection
        connection.has_ipv6 = lambda: False

    @property
    def connect_timeout(self) -> Optional[float]: return 20.0
    @property
    def read_timeout(self) -> Optional[float]: return 20.0
    @property
    def write_timeout(self) -> Optional[float]: return 20.0
    @property
    def pool_timeout(self) -> Optional[float]: return 20.0
    @property
    def connection_pool_size(self) -> Optional[int]: return 1
    @property
    def proxy_url(self) -> Optional[str]: return None
    @property
    def http_version(self) -> str: return "1.1"

    async def do_request(self, url, method, data=None, files=None, **kwargs):
        def _sync_req():
            # Match the logic that works in your sync_with_botfather function
            if method.upper() == "GET" or (not data and not files):
                return requests.get(url, timeout=15)
            else:
                return requests.post(url, data=data, files=files, timeout=15)
        
        try:
            response = await asyncio.to_thread(_sync_req)
            return response.status_code, response.content
        except Exception as e:
            print(f"❌ Network Error in RequestsRequest: {e}")
            return 500, b'{"ok": false, "error": "Network Error"}'

    async def initialize(self): pass
    async def shutdown(self): pass

# Load environment variables
load_dotenv()

# --- 1. CORE CONFIGURATION ---
BOT_TOKEN = os.getenv("DASHBOARD_BOT_TOKEN", "").strip()
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
HUB_SECRET = os.getenv("HUB_SECRET", "super_secret_token_123")
PORT = int(os.getenv("PORT", "7860"))

# MTProto (User Account) Config
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE") # Optional, will prompt in terminal if not set

# --- 2. THE AGENT (SPOKE MODULE) ---
class DashboardAgent:
    def __init__(self, hub_url: str, hub_secret: str, bot_id: str, bot_name: str, platform: str = "Distributed"):
        self.hub_url = hub_url.rstrip("/")
        self.hub_secret = hub_secret
        self.bot_id, self.bot_name, self.platform = bot_id, bot_name, platform
        self.headers = {"X-Hub-Token": self.hub_secret}
        self.tokens_used = 0
        self.storage_used = 0
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        self._setup_log_interception()

    def _heartbeat_loop(self):
        while True:
            try:
                payload = {"bot_id": self.bot_id, "name": self.bot_name, "platform": self.platform,
                           "usage": {"tokens": self.tokens_used, "storage_bytes": self.storage_used}}
                requests.post(f"{self.hub_url}/heartbeat", json=payload, headers=self.headers, timeout=5)
            except: pass
            time.sleep(30)

    def report_usage(self, tokens: int = 0, storage_delta: int = 0):
        self.tokens_used += tokens
        self.storage_used += storage_delta

    def _setup_log_interception(self):
        class LogStream:
            def __init__(self, original, agent): self.original, self.agent = original, agent
            def write(self, msg):
                self.original.write(msg)
                if msg.strip(): self.agent._send_log(msg.strip())
            def flush(self): self.original.flush()
        sys.stdout = LogStream(sys.stdout, self)

    def _send_log(self, message: str):
        def _async_send():
            try: requests.post(f"{self.hub_url}/logs", json={"bot_id": self.bot_id, "message": message}, headers=self.headers, timeout=5)
            except: pass
        threading.Thread(target=_async_send, daemon=True).start()

# --- 3. THE HUB (MASTER DASHBOARD) ---
class BotState:
    def __init__(self, name: str, username: str, token: str = None):
        self.name, self.username, self.token = name, username, token
        self.description = "No description found."
        self.photo_id = None
        self.last_seen = None
        self.status = "Offline"
        self.usage = {"tokens": 0, "storage_bytes": 0}
        self.logs, self.max_logs = [], 30
        self.is_synced = False

    def add_log(self, text: str):
        self.logs.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {text}")
        if len(self.logs) > self.max_logs: self.logs.pop(0)

CONNECTED_BOTS: Dict[str, BotState] = {}
LIVE_LOG_SESSIONS: Dict[int, dict] = {} 

# FastAPI App
api_app = FastAPI()

@api_app.post("/heartbeat")
async def heartbeat(data: dict, x_hub_token: str = Header(...)):
    if x_hub_token != HUB_SECRET: raise HTTPException(status_code=403)
    bid = data["bot_id"]
    if bid not in CONNECTED_BOTS: 
        CONNECTED_BOTS[bid] = BotState(data["name"], "Unknown")
    bot = CONNECTED_BOTS[bid]
    bot.last_seen = datetime.datetime.now()
    bot.status = "Online"
    if "usage" in data: bot.usage.update(data["usage"])
    return {"status": "ok"}

@api_app.post("/logs")
async def receive_logs(data: dict, x_hub_token: str = Header(...)):
    if x_hub_token != HUB_SECRET: raise HTTPException(status_code=403)
    if data.get("bot_id") in CONNECTED_BOTS: CONNECTED_BOTS[data["bot_id"]].add_log(data.get("message", ""))
    return {"status": "ok"}

# --- 4. BOTFATHER SYNC LOGIC (MTPROTO) ---
async def sync_with_botfather():
    if not API_ID or not API_HASH:
        print("⚠️ Skipping BotFather Sync: API_ID/API_HASH not set.")
        return

    print("🔄 Syncing with @BotFather...")
    async with TelegramClient('hub_session', int(API_ID), API_HASH) as client:
        # Search for tokens in BotFather history
        async for message in client.iter_messages('BotFather', limit=200):
            if not message.text: continue
            
            # Regex for Telegram Bot Token
            tokens = re.findall(r'(\d+:[A-Za-z0-9_-]+)', message.text)
            for token in tokens:
                try:
                    # Verify token and get info
                    res = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5).json()
                    if res.get("ok"):
                        bot_info = res["result"]
                        bid = str(bot_info["id"])
                        if bid not in CONNECTED_BOTS:
                            CONNECTED_BOTS[bid] = BotState(bot_info["first_name"], bot_info["username"], token)
                            CONNECTED_BOTS[bid].is_synced = True
                            
                            # Extra: Fetch Description & Photo
                            try:
                                d_res = requests.get(f"https://api.telegram.org/bot{token}/getMyDescription").json()
                                if d_res.get("ok"): CONNECTED_BOTS[bid].description = d_res["result"]["description"] or "None"
                                
                                p_res = requests.get(f"https://api.telegram.org/bot{token}/getUserProfilePhotos?limit=1").json()
                                if p_res.get("ok") and p_res["result"]["total_count"] > 0:
                                    CONNECTED_BOTS[bid].photo_id = p_res["result"]["photos"][0][-1]["file_id"]
                            except: pass
                            
                            print(f"✅ Found Bot: @{bot_info['username']}")
                except: pass
    print("✨ Sync Complete.")

# --- 5. TELEGRAM UI ---
def clean_text(text: str) -> str:
    """Removes any potential breaking characters and non-ascii."""
    try:
        # Extreme cleaning: Keep only readable characters
        import re
        return re.sub(r'[^\x00-\x7F]+', '', str(text))
    except:
        return str(text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
        await update.effective_message.reply_text(f"⛔ Access Denied. ID: {update.effective_user.id}")
        return
    
    text = f"🏰 MASTER HUB (Total Bots: {len(CONNECTED_BOTS)})\n"
    text += f"━━━━━━━━━━━━━━━\n\n"
    
    if not CONNECTED_BOTS:
        text += "No bots found. Click Sync to scan BotFather."
    else:
        for bid, bot in CONNECTED_BOTS.items():
            is_active = bot.last_seen and (datetime.datetime.now() - bot.last_seen).seconds < 60
            s = "🟢" if is_active else "⚪"
            safe_name = clean_text(bot.name)
            safe_uname = clean_text(bot.username)
            text += f"{s} {safe_name} (@{safe_uname})\n"
            text += f"   🔑 {bot.token or 'No Token'}\n\n"
    
    btns = []
    # Compact Bot Management Buttons
    row = []
    for bid, bot in CONNECTED_BOTS.items():
        row.append(InlineKeyboardButton(f"⚙️ {bot.name[:10]}", callback_data=f"manage_{bid}"))
        if len(row) == 3: # 3 buttons per row to save space
            btns.append(row)
            row = []
    if row: btns.append(row)
    
    btns.append([InlineKeyboardButton("📊 Refresh", callback_data="refresh"), InlineKeyboardButton("🔄 Sync BotFather", callback_data="sync")])
    
    if not text.strip():
        text = "🏰 MASTER HUB\nInitializing dashboard... please refresh."
    
    # Debug logging
    print(f"📊 Dashboard UI generated ({len(text)} chars) - PLAIN TEXT MODE")
    print(f"🔍 DEBUG: First 50 chars: {text[:50]!r}")
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns))
        except Exception as e:
            print(f"⚠️ Edit Failed: {e}")
            try:
                await update.callback_query.message.delete()
            except: pass
            try:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(btns))
            except Exception as e2:
                print(f"❌ Critical Send Failure (Edit Fallback): {e2}")
    else:
        try:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(btns))
        except Exception as e:
            print(f"❌ Critical Send Failure (Initial): {e}")
            # Try sending without buttons as a last resort
            try:
                print("🔄 Attempting EMERGENCY TEST: Sending 'HELLO WORLD'...")
                await update.message.reply_text("⚠️ EMERGENCY TEST: Hello World. If you see this, the dashboard content is the problem.")
            except Exception as e4:
                print(f"💀 TOTAL SYSTEM FAILURE: Even Hello World failed: {e4}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("page_") or query.data == "refresh":
        await start(update, context)
    
    elif query.data == "sync":
        await query.edit_message_text("🔄 Syncing with @BotFather...\nCheck your terminal for login if needed.")
        await sync_with_botfather()
        await start(update, context)
    
    elif query.data.startswith("manage_"):
        bid = query.data.replace("manage_", "")
        bot = CONNECTED_BOTS.get(bid)
        safe_name = clean_text(bot.name)
        safe_uname = clean_text(bot.username)
        safe_desc = clean_text(bot.description[:100] + ("..." if len(bot.description)>100 else ""))
        
        text = f"🤖 BOT CONTROL PANEL\n"
        text += f"━━━━━━━━━━━━━━━\n"
        text += f"🏷️ Name: {safe_name}\n"
        text += f"📧 User: @{safe_uname}\n"
        text += f"📝 Desc: {safe_desc}\n"
        text += f"🔑 Token: {bot.token or 'Hidden'}\n"
        text += f"━━━━━━━━━━━━━━━\n"
        text += f"📊 AI Usage: {bot.usage.get('tokens',0)} tokens"
        
        btns = [[InlineKeyboardButton("📜 Live Logs", callback_data=f"logs_{bid}")], [InlineKeyboardButton("🔙 Back", callback_data="home")]]
        
        if bot.photo_id:
            try:
                await query.message.delete()
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=bot.photo_id, caption=text, reply_markup=InlineKeyboardMarkup(btns))
            except:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns))
        else:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns))
    
    elif query.data.startswith("logs_"):
        bid = query.data.replace("logs_", "")
        bot = CONNECTED_BOTS.get(bid)
        log_text = clean_text("\n".join(bot.logs[-15:])) if bot.logs else "No logs/Agent not connected."
        msg = await query.edit_message_text(f"📜 Logs: {bot.name}\n\n{log_text}", 
                                           reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop", callback_data="home")]]))
        LIVE_LOG_SESSIONS[query.from_user.id] = {"bot_id": bid, "message_id": msg.message_id, "chat_id": query.message.chat_id, "last_text": log_text}
    
    elif query.data in ["home", "refresh"]:
        LIVE_LOG_SESSIONS.pop(query.from_user.id, None)
        await start(update, context)

async def log_streamer_job(context: ContextTypes.DEFAULT_TYPE):
    for uid, s in list(LIVE_LOG_SESSIONS.items()):
        bot = CONNECTED_BOTS.get(s["bot_id"])
        safe_name = escape_md(bot.name)
        log_text = "\n".join(bot.logs[-15:]) if bot.logs else "Waiting for logs..."
        new_text = f"📜 *Logs: {safe_name}*\n```\n{log_text}\n```"
        if new_text != s["last_text"]:
            try:
                await context.bot.edit_message_text(chat_id=s["chat_id"], message_id=s["message_id"], text=new_text, parse_mode="Markdown", 
                                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop", callback_data="home")]]))
                s["last_text"] = new_text
            except: pass

# --- 6. RUN MODES ---
async def main_hub():
    if not BOT_TOKEN:
        print("❌ DASHBOARD_BOT_TOKEN is missing!")
        return
    
    print(f"🏰 Hub starting on port {PORT}...")
    
    # Using custom Requests engine because HTTPX times out on some HF spaces
    request_config = RequestsRequest()
    bot_app = ApplicationBuilder().token(BOT_TOKEN).request(request_config).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    if bot_app.job_queue: bot_app.job_queue.run_repeating(log_streamer_job, interval=3)
    
    # Retry initialization
    for attempt in range(3):
        try:
            print(f"🔄 Initializing bot (Attempt {attempt+1}/3)...")
            await bot_app.initialize()
            break
        except Exception as e:
            if "Conflict" in str(e):
                print("❌ CONFLICT ERROR: The bot is already running in another location!")
                print("👉 Please stop the bot on your local machine or other servers.")
                os._exit(1) # Force immediate exit to stop the loop
            if attempt == 2: raise e
            print(f"⚠️ Init failed, retrying in 5s... ({e})")
            await asyncio.sleep(5)
    
    # Try Sync AFTER initialization
    await sync_with_botfather()
    
    await bot_app.bot.set_my_commands([BotCommand("start", "Launch Dashboard")])
    await bot_app.start()
    
    try:
        await bot_app.updater.start_polling()
    except Exception as e:
        if "Conflict" in str(e):
            print("❌ CONFLICT ERROR: Another instance started while polling!")
            os._exit(1)

    config = uvicorn.Config(api_app, host="0.0.0.0", port=PORT, log_level="error")
    await uvicorn.Server(config).serve()

if __name__ == "__main__":
    if "--worker" in sys.argv:
        print("🧪 Mock Worker Starting...")
        import random
        agent = DashboardAgent(f"http://localhost:{PORT}", HUB_SECRET, "mock_bot", "Tester Bot")
        while True:
            print(f"Heartbeat at {datetime.datetime.now().strftime('%H:%M:%S')}")
            agent.report_usage(tokens=10)
            time.sleep(10)
    else:
        asyncio.run(main_hub())
