import hashlib
import asyncio
import re
import shutil
import time
import os
import psutil
import stat
from dotenv import load_dotenv
import chromedriver_autoinstaller
import concurrent.futures
import sys
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ApplicationHandlerStop,
    MessageHandler,
    filters
)
from telegram.error import TelegramError, NetworkError
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    WebDriverException,
    TimeoutException
)
from selenium.webdriver.chrome.service import Service
import platform

load_dotenv()
chromedriver_autoinstaller.install() 
# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = int(os.getenv('CHAT_ID'))  # Convert to integer
CHECK_INTERVAL = 120
MAX_URLS = 10
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 20

# Global storage
monitored_urls = {}
is_monitoring = False
SECURITY_LOG = "activity.log"

def kill_previous_instances():
    current_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if 'python' in proc.info['name'].lower():
                cmdline = ' '.join(proc.info['cmdline'])
                if 'zealy_bot.py' in cmdline and proc.info['pid'] != current_pid:
                    print(f"🚨 Killing previous instance (PID: {proc.info['pid']})")
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue

def get_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    # Hardcoded known good path for Chrome in Docker
    chrome_path = "/usr/bin/chromium"
    print("🕵️ Forcing Chrome binary path:", chrome_path)

    if not os.path.exists(chrome_path):
        raise FileNotFoundError(f"Chrome missing at expected path: {chrome_path}")

    options.binary_location = chrome_path
    return options

def get_content_hash(url):
    driver = None
    try:
        options = get_chrome_options()
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(url)
        print(driver.page_source[:1000]) 
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ZEALY_CONTAINER_SELECTOR))
        )
        container = driver.find_element(By.CSS_SELECTOR, ZEALY_CONTAINER_SELECTOR)
        content = container.text
        clean_content = re.sub(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', 
            '', 
            content
        )
        return hashlib.sha256(clean_content.strip().encode()).hexdigest()
    except Exception as e:
        print(f"Content check error: {str(e)}")
        return None
    finally:
        try:
            driver.quit()
        except:
            pass

async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("🚫 Unauthorized access!")
        raise ApplicationHandlerStop

async def send_notification(bot, message):
    retries = 0
    while retries < 3:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"✅ Sent notification: {message[:50]}...")
            return True
        except (TelegramError, NetworkError) as e:
            print(f"📡 Network error: {str(e)} - Retry {retries+1}/3")
            retries += 1
            await asyncio.sleep(5)
    return False

async def check_urls(bot):
    global monitored_urls
    current_time = time.time()
    for url in list(monitored_urls.keys()):
        try:
            start_time = time.time()
            current_hash = get_content_hash(url)
            if not current_hash:
                monitored_urls[url]['failures'] += 1
                if monitored_urls[url]['failures'] > 3:
                    del monitored_urls[url]
                    await send_notification(bot, f"🔴 Removed from monitoring: {url}")
                continue
            monitored_urls[url]['failures'] = 0
            if monitored_urls[url]['hash'] != current_hash:
                if current_time - monitored_urls[url].get('last_notified', 0) > 300:
                    success = await send_notification(
                        bot, f"🚨 CHANGE DETECTED!\n{url}\nResponse time: {time.time()-start_time:.2f}s")
                    if success:
                        monitored_urls[url].update({
                            'last_notified': current_time,
                            'hash': current_hash,
                            'last_checked': current_time
                        })
            monitored_urls[url]['last_checked'] = current_time
        except Exception as e:
            print(f"⚠️ Error processing {url}: {str(e)}")

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Zealy Monitoring Bot\n\n"
        "Commands:\n"
        "/add <url> - Add monitoring URL\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/purge - Remove all URLs\n"
        f"Max URLs: {MAX_URLS}"
    )

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("No monitored URLs")
        return
    message = ["📋 Monitored URLs:"] + [f"{idx}. {url}" for idx, url in enumerate(monitored_urls.keys(), 1)]
    await update.message.reply_text("\n".join(message)[:4000])

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    try:
        url = context.args[0].lower()
        if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+$', url):
            await update.message.reply_text("❌ Invalid Zealy URL format")
            return
        if url in monitored_urls:
            await update.message.reply_text("ℹ️ URL already monitored")
            return
        processing_msg = await update.message.reply_text("⏳ Verifying URL...")
        loop = asyncio.get_event_loop()
        initial_hash = await loop.run_in_executor(None, get_content_hash, url)
        if not initial_hash:
            await processing_msg.edit_text("❌ Failed to verify URL content")
            return
        monitored_urls[url] = {
            'hash': initial_hash,
            'last_notified': 0,
            'last_checked': time.time(),
            'failures': 0
        }
        await processing_msg.edit_text(
            f"✅ Added: {url}\n📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}"
        )
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: /add <zealy-url>")
    except Exception as e:
        print(f"⚠️ Error in add_url: {str(e)}")
        await update.message.reply_text("❌ Internal server error")

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    if is_monitoring:
        await update.message.reply_text("⚠️ Already monitoring")
        return
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to monitor")
        return
    is_monitoring = True
    asyncio.create_task(start_monitoring(context.application))
    await update.message.reply_text("✅ Monitoring started!")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    await update.message.reply_text("🛑 Monitoring stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    monitored_urls.clear()
    await update.message.reply_text("✅ All URLs purged!")

async def start_monitoring(application: Application):
    global is_monitoring
    bot = application.bot
    is_monitoring = True
    await send_notification(bot, "🔔 Monitoring started!")
    while is_monitoring:
        try:
            start_time = time.time()
            await check_urls(bot)
            await asyncio.sleep(max(CHECK_INTERVAL - (time.time() - start_time), 5))
        except Exception as e:
            print(f"🚨 Monitoring error: {str(e)}")
            await asyncio.sleep(30)

def main():
    print(f"🚀 Starting bot at {datetime.now()}")
    kill_previous_instances()

    if platform.system() == "Linux":
        if not os.path.exists("/usr/bin/google-chrome-stable"):
            print("❌ Chrome not found at /usr/bin/google-chrome-stable")
            exit(1)
        if not os.path.exists("/usr/bin/chromedriver"):
            print("❌ Chromedriver not found at /usr/bin/chromedriver")
            exit(1)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(lambda app: app.bot.delete_webhook(drop_pending_updates=True))
        .build()
    )

    application.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
    handlers = [
        CommandHandler("start", start),
        CommandHandler("add", add_url),
        CommandHandler("list", list_urls),
        CommandHandler("run", run_monitoring),
        CommandHandler("stop", stop_monitoring),
        CommandHandler("purge", purge_urls)
    ]
    for handler in handlers:
        application.add_handler(handler)

    try:
        application.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Graceful shutdown")
    finally:
        executor.shutdown()
        print("🧹 Cleaning up...")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"🔥 Critical error: {str(e)}")
    finally:
        print("✅ Bot terminated")
