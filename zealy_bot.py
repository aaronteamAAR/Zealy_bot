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
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, ApplicationHandlerStop
    from telegram.error import TelegramError
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException
except ImportError as e:
    print(f"Missing package: {e}")
    sys.exit(1)

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = int(os.getenv('CHAT_ID'))
CHECK_INTERVAL = 20  # Balanced interval
MAX_URLS = 20
TIMEOUT = 12  # Reasonable timeout

# Global storage
monitored_urls = {}
is_monitoring = False
driver_pool = []
session = None


def create_speed_optimized_driver():
    """Ultra-fast Chrome driver - prioritizes speed over everything"""
    options = Options()
    
    # Basic setup
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")
    
    # AGGRESSIVE performance optimizations
    options.add_argument("--disable-images")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees,VizDisplayCompositor")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-gpu-logging")
    options.add_argument("--silent")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-background-networking")
    
    # Speed-focused network settings
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    
    # Minimal user agent
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(10)  # Very aggressive - 6 seconds max
    driver.implicitly_wait(1)        # Minimal wait
    
    return driver

def get_driver():
    """Get driver from pool or create new one with better cleanup"""
    if driver_pool:
        driver = driver_pool.pop()
        # Test if driver is still alive
        try:
            driver.current_url
            return driver
        except WebDriverException:
            # Driver is dead, create new one
            try:
                driver.quit()
            except:
                pass
    return create_speed_optimized_driver()

def return_driver(driver):
    """Return driver to pool with health check"""
    if not driver:
        return
    try:
        # Quick health check
        driver.current_url
        if len(driver_pool) < 2:  # Smaller pool
            driver_pool.append(driver)
        else:
            driver.quit()
    except:
        try:
            driver.quit()
        except:
            pass

async def immediate_content_check(url):
    """Ultra-fast content checking for immediate alerts"""
    driver = None
    try:
        driver = get_driver()
        
        print(f"⚡ Fast check: {url}")
        start_time = time.time()
        
        # Quick page load
        driver.get(url)
        
        # Try to get content ASAP - don't wait for full page load
        selectors_to_try = [
            "div.flex.flex-col.w-full.pt-100",  # Primary selector
            "main",
            "body"
        ]
        
        content = None
        for selector in selectors_to_try:
            try:
                # Very short wait - get content as soon as it appears
                element = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                content = element.text
                if content and len(content.strip()) > 30:  # Lower threshold
                    break
            except TimeoutException:
                continue
        
        if not content or len(content.strip()) < 15:  # Lower minimum
            print(f"⚠️ Quick check failed: {len(content) if content else 0} chars")
            return None
        
        # Fast hash generation
        clean_content = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|Updated \d+[smhd] ago', '', content)
        content_hash = hashlib.sha256(clean_content.strip().encode()).hexdigest()
        
        elapsed = time.time() - start_time
        print(f"⚡ Hash: {content_hash[:8]}... ({elapsed:.1f}s)")
        return content_hash
        
    except Exception as e:
        print(f"❌ Quick check error {url}: {e}")
        return None
    finally:
        return_driver(driver)

async def immediate_alert_monitoring(bot):
    """Ultra-responsive monitoring for immediate alerts"""
    global monitored_urls
    
    if not monitored_urls:
        return
    
    print(f"⚡ IMMEDIATE CHECK: {len(monitored_urls)} URLs...")
    start_time = time.time()
    
    # Higher concurrency for speed
    semaphore = asyncio.Semaphore(4)  # Increased from 2 to 4
    
    async def check_with_semaphore(url):
        async with semaphore:
            return await immediate_content_check(url)
    
    tasks = []
    urls = list(monitored_urls.keys())
    
    for url in urls:
        task = asyncio.create_task(check_with_semaphore(url))
        tasks.append((url, task))
    
    try:
        # Shorter timeout for immediate processing
        results = await asyncio.wait_for(
            asyncio.gather(*[task for _, task in tasks], return_exceptions=True),
            timeout=20  # Max 20 seconds total
        )
        
        current_time = time.time()
        notifications = []
        
        for i, (url, _) in enumerate(tasks):
            if i >= len(results):
                continue
                
            result = results[i]
            if isinstance(result, Exception) or not result:
                monitored_urls[url]['failures'] = monitored_urls[url].get('failures', 0) + 1
                print(f"⚠️ Failed: {url} (#{monitored_urls[url]['failures']})")
                
                # More lenient failure handling for speed
                if monitored_urls[url]['failures'] > 5:
                    del monitored_urls[url]
                    notifications.append(f"🔴 Removed {url[:40]}... (too many failures)")
                continue
            
            # Reset failures on success
            monitored_urls[url]['failures'] = 0
            
            # IMMEDIATE CHANGE DETECTION - NO COOLDOWN!
            if monitored_urls[url]['hash'] != result:
                # Format immediate notification with timestamp
                timestamp = datetime.now().strftime("%H:%M:%S")
                notifications.append(f"🚨 INSTANT ALERT [{timestamp}]\n🔗 {url}\n📊 Hash changed: {result[:8]}...")
                monitored_urls[url]['last_notified'] = current_time
                print(f"🔥 IMMEDIATE CHANGE: {url}")
                
                monitored_urls[url]['hash'] = result
            
            monitored_urls[url]['last_checked'] = current_time
        
        # Send notifications IMMEDIATELY
        if notifications:
            for notification in notifications:
                # Send each notification separately for maximum speed
                await bot.send_message(chat_id=CHAT_ID, text=notification[:4000])
                print(f"📱 Sent immediate alert!")
        
        elapsed = time.time() - start_time
        print(f"⚡ SPEED RUN: {len(urls)} URLs in {elapsed:.2f}s")
        
    except asyncio.TimeoutError:
        print("⚠️ Some checks timed out - but continuing for speed")


async def speed_monitoring_loop(application):
    """Ultra-fast monitoring loop for immediate alerts"""
    global is_monitoring
    
    bot = application.bot
    await bot.send_message(chat_id=CHAT_ID, text="⚡ IMMEDIATE ALERT MODE ACTIVATED!")
    
    try:
        while is_monitoring:
            await immediate_alert_monitoring(bot)
            # Short sleep for immediate responsiveness
            await asyncio.sleep(CHECK_INTERVAL)  # 10 seconds
    except Exception as e:
        print(f"❌ Speed monitoring error: {e}")
        await bot.send_message(chat_id=CHAT_ID, text=f"🚨 URGENT: Monitoring error: {e}")
    finally:
        # Cleanup
        while driver_pool:
            try:
                driver_pool.pop().quit()
            except:
                pass
        print("🧹 Speed monitoring cleanup completed")

# FIXED auth middleware
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Properly block unauthorized users"""
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("🚫 Unauthorized")
        raise ApplicationHandlerStop  # This actually stops processing

# Command handlers (keeping them simple and functional)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ Smart Zealy Monitor v2\n\n"
        "📋 Commands:\n"
        "/add <url> - Add monitoring URL\n"
        "/remove <#> - Remove URL by number\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/purge - Clear all URLs\n\n"
        f"📊 Limits: {MAX_URLS} URLs, {CHECK_INTERVAL}s interval"
    )

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(f"❌ Max {MAX_URLS} URLs reached")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /add <zealy-url>")
        return
    
    url = context.args[0].lower()
    
    # Validate Zealy URL
    if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+$', url):
        await update.message.reply_text("❌ Invalid Zealy URL format")
        return
    
    if url in monitored_urls:
        await update.message.reply_text("⚠️ Already monitoring this URL")
        return
    
    msg = await update.message.reply_text("⏳ Verifying URL...")
    
    try:
        # Get initial hash
        initial_hash = await immediate_content_check(url)
        if not initial_hash:
            await msg.edit_text("❌ Unable to access URL content")
            return
        
        monitored_urls[url] = {
            'hash': initial_hash,
            'last_notified': 0,
            'last_checked': time.time(),
            'failures': 0
        }
        
        await msg.edit_text(
            f"✅ Added successfully!\n"
            f"📊 Monitoring: {len(monitored_urls)}/{MAX_URLS} URLs"
        )
        print(f"➕ Added: {url}")
        
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls or not context.args:
        await update.message.reply_text("❌ Usage: /remove <number>\nUse /list to see numbers")
        return
    
    try:
        idx = int(context.args[0]) - 1
        urls = list(monitored_urls.keys())
        if 0 <= idx < len(urls):
            url = urls[idx]
            del monitored_urls[url]
            await update.message.reply_text(f"✅ Removed: {url[:50]}...")
            print(f"➖ Removed: {url}")
        else:
            await update.message.reply_text(f"❌ Invalid number (1-{len(urls)})")
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("📋 No URLs being monitored")
        return
    
    urls_list = []
    for i, (url, data) in enumerate(monitored_urls.items(), 1):
        status = "✅" if data.get('failures', 0) == 0 else f"⚠️({data['failures']})"
        last_check = data.get('last_checked', 0)
        if last_check > 0:
            ago = int((time.time() - last_check) / 60)
            time_str = f"{ago}m ago" if ago > 0 else "just now"
        else:
            time_str = "never"
        
        urls_list.append(f"{i}. {status} {url}\n   Last: {time_str}")
    
    message = f"📋 Monitored URLs ({len(monitored_urls)}/{MAX_URLS}):\n\n" + "\n\n".join(urls_list)
    await update.message.reply_text(message[:4000])

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    if is_monitoring:
        await update.message.reply_text("⚠️ Already monitoring")
        return
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to monitor")
        return
    
    is_monitoring = True
    asyncio.create_task(speed_monitoring_loop(context.application))
    await update.message.reply_text("✅ Monitoring started!")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    await update.message.reply_text("🛑 Monitoring stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    count = len(monitored_urls)
    monitored_urls.clear()
    await update.message.reply_text(f"✅ Cleared {count} URLs")

def main():
    if not TELEGRAM_BOT_TOKEN or not CHAT_ID:
        print("❌ Missing TELEGRAM_BOT_TOKEN or CHAT_ID in .env")
        return
    
    print("🚀 Starting Smart Zealy Monitor...")
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add FIXED auth middleware
    app.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
    
    # Add command handlers
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
    
    print("✅ Bot ready - starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()