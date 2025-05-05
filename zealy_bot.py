import hashlib
import asyncio
import re
import time
import os
from dotenv import load_dotenv
import sys
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError, NetworkError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException,
    TimeoutException
)
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv() 

# Configuration - Use environment variables in production!
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
CHECK_INTERVAL = 120  # Seconds
MAX_URLS = 10
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 15  # Seconds for page load

# Global storage (use database in production)
monitored_urls = {}
is_monitoring = False

# --------------------------
# Chrome Configuration
# --------------------------


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_interval
    try:
        new_interval = int(context.args[0])
        if 60 <= new_interval <= 86400:  # 1 minute to 24 hours
            current_interval = new_interval
            await update.message.reply_text(f"🕒 Check interval set to {new_interval} seconds")
        else:
            await update.message.reply_text("❌ Interval must be between 60-86400 seconds")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: /interval [seconds]")
        
        
def get_chrome_options():
    """Configure Chrome options with version-aware settings"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    # Headless mode configuration
    try:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
    except WebDriverException:
        options.add_argument("--headless")
    
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.binary_location = "/usr/bin/chromium-browser" 
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    return options

# --------------------------
# Core Monitoring Logic
# --------------------------
def get_content_hash(url):
    """Get stable hash of target content with enhanced error handling"""
    retries = 0
    while retries < 3:
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(
                service=service, 
                options=get_chrome_options()
            )
            driver.set_page_load_timeout(REQUEST_TIMEOUT)
            driver.get(url)
            
            # Wait for element existence
            try:
                container = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, ZEALY_CONTAINER_SELECTOR)
                    )
                )
            except TimeoutException:
                print(f"⏳ Timeout loading {url}")
                return None

            # Clean dynamic content
            content = container.text
            clean_content = re.sub(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', 
                '', 
                content
            )
            
            return hashlib.sha256(clean_content.strip().encode()).hexdigest()

        except (StaleElementReferenceException, WebDriverException) as e:
            print(f"♻️ Retrying {url} - {str(e)}")
            retries += 1
            time.sleep(2)
        except Exception as e:
            print(f"🚨 Critical error checking {url}: {str(e)}")
            return None
        finally:
            try:
                driver.quit()
            except:
                pass
    return None

async def send_notification(bot, message):
    """Robust notification sending with retry logic"""
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
    """Enhanced URL checking with failure tracking"""
    global monitored_urls
    current_time = time.time()
    
    for url in list(monitored_urls.keys()):
        try:
            start_time = time.time()
            current_hash = get_content_hash(url)
            
            if not current_hash:
                monitored_urls[url]['failures'] += 1
                if monitored_urls[url]['failures'] > 3:
                    print(f"⚠️ Removing faulty URL: {url}")
                    del monitored_urls[url]
                    await send_notification(bot, f"🔴 Removed from monitoring: {url}")
                continue
            
            # Reset failure counter on success
            monitored_urls[url]['failures'] = 0
            
            if monitored_urls[url]['hash'] != current_hash:
                if current_time - monitored_urls[url].get('last_notified', 0) > 300:
                    success = await send_notification(
                        bot, 
                        f"🚨 CHANGE DETECTED!\n{url}\n" +
                        f"Response time: {time.time()-start_time:.2f}s"
                    )
                    if success:
                        monitored_urls[url].update({
                            'last_notified': current_time,
                            'hash': current_hash,
                            'last_checked': current_time
                        })
            
            monitored_urls[url]['last_checked'] = current_time
            
        except Exception as e:
            print(f"⚠️ Error processing {url}: {str(e)}")

# --------------------------
# Command Handlers
# --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message with bot instructions"""
    if update.effective_chat.id != int(CHAT_ID):
        return
    
    help_text = (
        "🚀 Zealy Monitoring Bot\n\n"
        "Available Commands:\n"
        "/add <url> - Add a Zealy URL to monitor\n"
        "/list - Show currently monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        f"Maximum URLs: {MAX_URLS}"
    )
    
    try:
        await update.message.reply_text(help_text)
    except TelegramError as e:
        print(f"⚠️ Failed to send help: {str(e)}")

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all monitored URLs"""
    if update.effective_chat.id != int(CHAT_ID):
        return

    if not monitored_urls:
        await update.message.reply_text("No URLs currently being monitored")
        return

    message = ["📋 Monitored URLs:"]
    for idx, url in enumerate(monitored_urls.keys(), 1):
        message.append(f"{idx}. {url}")
    
    try:
        await update.message.reply_text("\n".join(message)[:4000])
    except TelegramError as e:
        print(f"⚠️ Failed to list URLs: {str(e)}")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Secure URL addition with validation"""
    if update.effective_chat.id != int(CHAT_ID):
        return
    
    try:
        url = context.args[0].lower()
        if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+$', url):
            await update.message.reply_text("❌ Invalid Zealy URL format")
            return
        
        if url in monitored_urls:
            await update.message.reply_text("ℹ️ URL already monitored")
            return
            
        # Initial verification
        if not (initial_hash := get_content_hash(url)):
            await update.message.reply_text("❌ Failed to verify URL content")
            return
            
        monitored_urls[url] = {
            'hash': initial_hash,
            'last_notified': 0,
            'last_checked': time.time(),
            'failures': 0
        }
        
        await update.message.reply_text(
            f"✅ Added: {url}\n"
            f"📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}"
        )
        
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: /add <zealy-url>")
    except Exception as e:
        print(f"⚠️ Unexpected error in add_url: {str(e)}")
        await update.message.reply_text("❌ Internal server error")

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the monitoring process"""
    global is_monitoring
    if update.effective_chat.id != int(CHAT_ID):
        return

    if is_monitoring:
        await update.message.reply_text("⚠️ Monitoring is already active")
        return

    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to monitor")
        return

    is_monitoring = True
    application = context.application
    asyncio.create_task(start_monitoring(application))
    
    try:
        await update.message.reply_text("✅ Monitoring started!")
    except TelegramError as e:
        print(f"⚠️ Failed to send start confirmation: {str(e)}")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the monitoring process"""
    global is_monitoring
    if update.effective_chat.id != int(CHAT_ID):
        return

    is_monitoring = False
    try:
        await update.message.reply_text("🛑 Monitoring stopped")
    except TelegramError as e:
        print(f"⚠️ Failed to send stop confirmation: {str(e)}")

# --------------------------
# Monitoring Loop
# --------------------------
async def start_monitoring(application: Application):
    """Monitoring loop with health checks"""
    global is_monitoring
    bot = application.bot
    is_monitoring = True
    
    await send_notification(bot, "🔔 Monitoring started!")
    print(f"📊 Initial monitoring state: {len(monitored_urls)} URLs")
    
    while is_monitoring:
        try:
            start_time = time.time()
            await check_urls(bot)
            elapsed = time.time() - start_time
            sleep_time = max(CHECK_INTERVAL - elapsed, 5)
            await asyncio.sleep(sleep_time)
        except Exception as e:
            print(f"🚨 Monitoring loop error: {str(e)}")
            await asyncio.sleep(30)

# --------------------------
# Main Application Setup
# --------------------------
def main():
    """Main entry point with proper cleanup"""
    print(f"🚀 Starting bot at {datetime.now()}")
    
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register command handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("add", add_url),
        CommandHandler("list", list_urls),
        CommandHandler("run", run_monitoring),
        CommandHandler("stop", stop_monitoring),
        CommandHandler("interval", set_interval) 
    ]
    
    for handler in handlers:
        application.add_handler(handler)
    
    try:
        application.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Graceful shutdown initiated")
    finally:
        print("🧹 Cleaning up resources...")
        # Add any additional cleanup logic here

if __name__ == '__main__':
    main()
