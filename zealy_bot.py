import hashlib
import asyncio
import aiohttp
import time
import os, re
import sys
from datetime import datetime
import concurrent.futures

try:
    from dotenv import load_dotenv
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
    from telegram.error import TelegramError
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
except ImportError as e:
    print(f"Missing package: {e}")
    sys.exit(1)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = int(os.getenv('CHAT_ID'))
CHECK_INTERVAL = 15  # Reduced from 25
MAX_URLS = 20
TIMEOUT = 10  # Reduced from 30

# Global storage
monitored_urls = {}
is_monitoring = False
driver_pool = []
session = None

def create_fast_driver():
    """Create optimized Chrome driver"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-images")
    options.add_argument("--disable-javascript")  # Most speed gain here
    options.add_argument("--disable-css")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=800,600")  # Smaller window
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--memory-pressure-off")
    
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(TIMEOUT)
    driver.implicitly_wait(2)  # Reduced wait time
    return driver

def get_driver():
    """Get driver from pool or create new one"""
    if driver_pool:
        return driver_pool.pop()
    return create_fast_driver()

def return_driver(driver):
    """Return driver to pool"""
    if len(driver_pool) < 3:  # Max 3 drivers in pool
        driver_pool.append(driver)
    else:
        try:
            driver.quit()
        except:
            pass

async def fast_content_check(url):
    """Fast content checking with minimal waiting"""
    driver = None
    try:
        # Try HTTP request first (fastest)
        if session:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        text = await response.text()
                        if len(text) > 1000:  # Has substantial content
                            # Quick hash of raw HTML with regex cleaning
                            clean_text = text.replace('\n', '').replace('\r', '').replace('\t', '')
                            clean_text = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', '', clean_text)
                            return hashlib.sha256(clean_text.encode()).hexdigest()
            except:
                pass  # Fall back to Selenium
        
        # Selenium fallback (when HTTP fails)
        driver = get_driver()
        driver.get(url)
        
        # Quick element grab - try main container first
        try:
            element = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.flex.flex-col.w-full.pt-100"))
            )
            content = element.text
            if content and len(content) > 50:
                clean_content = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', '', content)
                return hashlib.sha256(clean_content.encode()).hexdigest()
        except TimeoutException:
            # Last resort - get page source
            content = driver.page_source
            if content:
                clean_content = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', '', content)
                return hashlib.sha256(clean_content.encode()).hexdigest()
        
        return None
    except Exception as e:
        print(f"Error checking {url}: {e}")
        return None
    finally:
        if driver:
            return_driver(driver)

async def check_all_urls_parallel(bot):
    """Check all URLs in parallel"""
    global monitored_urls
    
    if not monitored_urls:
        return
    
    print(f"🔍 Checking {len(monitored_urls)} URLs in parallel...")
    start_time = time.time()
    
    # Create tasks for all URLs
    tasks = []
    urls = list(monitored_urls.keys())
    
    for url in urls:
        task = asyncio.create_task(fast_content_check(url))
        tasks.append((url, task))
    
    # Wait for all tasks with timeout
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[task for _, task in tasks], return_exceptions=True),
            timeout=TIMEOUT + 5
        )
        
        # Process results
        current_time = time.time()
        notifications = []
        
        for i, (url, _) in enumerate(tasks):
            if i >= len(results):
                continue
                
            result = results[i]
            if isinstance(result, Exception) or not result:
                monitored_urls[url]['failures'] = monitored_urls[url].get('failures', 0) + 1
                if monitored_urls[url]['failures'] > 3:  # Faster removal
                    del monitored_urls[url]
                    notifications.append(f"🔴 Removed {url} (too many failures)")
                continue
            
            # Reset failures on success
            monitored_urls[url]['failures'] = 0
            
            # Check for changes
            if monitored_urls[url]['hash'] != result:
                if current_time - monitored_urls[url].get('last_notified', 0) > 180:  # 3min cooldown
                    notifications.append(f"🚨 CHANGE: {url}")
                    monitored_urls[url]['last_notified'] = current_time
                
                monitored_urls[url]['hash'] = result
            
            monitored_urls[url]['last_checked'] = current_time
        
        # Send all notifications at once
        if notifications:
            await bot.send_message(chat_id=CHAT_ID, text="\n".join(notifications))
        
        elapsed = time.time() - start_time
        print(f"✅ Checked {len(urls)} URLs in {elapsed:.2f}s")
        
    except asyncio.TimeoutError:
        print("⚠️ Some URL checks timed out")

async def monitoring_loop(application):
    """Fast monitoring loop"""
    global is_monitoring, session
    
    # Create persistent HTTP session
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=5),
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
    )
    
    bot = application.bot
    await bot.send_message(chat_id=CHAT_ID, text="🚀 Fast monitoring started!")
    
    try:
        while is_monitoring:
            await check_all_urls_parallel(bot)
            await asyncio.sleep(CHECK_INTERVAL)
    except Exception as e:
        print(f"Monitoring error: {e}")
    finally:
        if session:
            await session.close()
        # Cleanup drivers
        while driver_pool:
            try:
                driver_pool.pop().quit()
            except:
                pass

# Fixed auth middleware function
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user is authorized"""
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("🚫 Unauthorized")
        return  # Stop processing this update

# Simplified command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ Fast Zealy Monitor\n"
        "/add <url> - Add URL\n"
        "/remove <#> - Remove URL\n"
        "/list - Show URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop\n"
        "/purge - Clear all"
    )

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(f"❌ Max {MAX_URLS} URLs")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /add <url>")
        return
    
    url = context.args[0].lower()
    if url in monitored_urls:
        await update.message.reply_text("⚠️ Already monitoring")
        return
    
    # Quick validation and initial check
    msg = await update.message.reply_text("⏳ Adding...")
    
    try:
        initial_hash = await fast_content_check(url)
        if not initial_hash:
            await msg.edit_text("❌ Can't access URL")
            return
        
        monitored_urls[url] = {
            'hash': initial_hash,
            'last_notified': 0,
            'last_checked': time.time(),
            'failures': 0
        }
        
        await msg.edit_text(f"✅ Added! ({len(monitored_urls)}/{MAX_URLS})")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls or not context.args:
        await update.message.reply_text("❌ Usage: /remove <number>")
        return
    
    try:
        idx = int(context.args[0]) - 1
        urls = list(monitored_urls.keys())
        if 0 <= idx < len(urls):
            url = urls[idx]
            del monitored_urls[url]
            await update.message.reply_text(f"✅ Removed: {url}")
        else:
            await update.message.reply_text("❌ Invalid number")
    except ValueError:
        await update.message.reply_text("❌ Invalid number")

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("📋 No URLs")
        return
    
    urls = [f"{i}. {url}" for i, url in enumerate(monitored_urls.keys(), 1)]
    await update.message.reply_text("📋 URLs:\n" + "\n".join(urls)[:4000])

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    if is_monitoring:
        await update.message.reply_text("⚠️ Already running")
        return
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs")
        return
    
    is_monitoring = True
    asyncio.create_task(monitoring_loop(context.application))
    await update.message.reply_text("✅ Started!")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    await update.message.reply_text("🛑 Stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    monitored_urls.clear()
    await update.message.reply_text("✅ All URLs cleared")

def main():
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("❌ Missing TELEGRAM_BOT_TOKEN or CHAT_ID in .env")
        return
    
    print("🚀 Starting fast Zealy monitor...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add auth middleware - FIXED VERSION
    app.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
    
    # Add handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("add", add_url),
        CommandHandler("remove", remove_url),
        CommandHandler("list", list_urls),
        CommandHandler("run", run_monitoring),
        CommandHandler("stop", stop_monitoring),
        CommandHandler("purge", purge_urls)
    ]
    
    for handler in handlers:
        app.add_handler(handler)
    
    print("✅ Bot ready - polling...")
    app.run_polling()

if __name__ == "__main__":
    main()