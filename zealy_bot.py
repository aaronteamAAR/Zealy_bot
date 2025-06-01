import hashlib
import asyncio
import re
import shutil
import time
import os
import traceback
import sys
from datetime import datetime
import platform
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# First check if required packages are installed
try:
    import psutil
    from dotenv import load_dotenv
    import chromedriver_autoinstaller
    import concurrent.futures
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
        TimeoutException,
        NoSuchElementException
    )
    from selenium.webdriver.chrome.service import Service
except ImportError as e:
    print(f"ERROR: Missing required package: {str(e)}")
    print("Please install required packages using:")
    print("pip install python-telegram-bot selenium python-dotenv psutil chromedriver-autoinstaller")
    input("Press Enter to exit...")
    sys.exit(1)

# Try to load .env file
print("Loading environment variables...")
load_dotenv()

# Check if env variables exist
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID_STR = os.getenv('CHAT_ID')

if not TELEGRAM_BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN environment variable is missing!")
    print("Create a .env file in the same directory with:")
    print("TELEGRAM_BOT_TOKEN=your_telegram_bot_token")
    print("CHAT_ID=your_chat_id")
    input("Press Enter to exit...")
    sys.exit(1)

if not CHAT_ID_STR:
    print("ERROR: CHAT_ID environment variable is missing!")
    print("Create a .env file in the same directory with:")
    print("CHAT_ID=your_chat_id (must be a number)")
    input("Press Enter to exit...")
    sys.exit(1)

try:
    CHAT_ID = int(CHAT_ID_STR)
except ValueError:
    print(f"ERROR: CHAT_ID must be an integer, got: {CHAT_ID_STR}")
    input("Press Enter to exit...")
    sys.exit(1)

# Automatic chromedriver installation
print("Setting up ChromeDriver...")
try:
    chromedriver_autoinstaller.install()
    print("ChromeDriver installed successfully")
except Exception as e:
    print(f"Warning: ChromeDriver auto-installation failed: {e}")
    print("We'll try to use existing Chrome/ChromeDriver")

# OPTIMIZED Configuration for faster real-time monitoring
CHECK_INTERVAL = 10  # Reduced from 25 to 8 seconds for faster detection
MAX_URLS = 20 
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 20  # Reduced from 30 to 12 seconds
PAGE_LOAD_TIMEOUT = 20  # Reduced page load timeout
ELEMENT_WAIT_TIMEOUT = 15  # Reduced element wait timeout
MAX_FAILURES_THRESHOLD = 8  # Increased from 5 to 15 to prevent premature removal
MAX_CONCURRENT_CHECKS = 2  # Number of URLs to check simultaneously

# Set appropriate paths based on environment
IS_RENDER = os.getenv('IS_RENDER', 'false').lower() == 'true'

if IS_RENDER:
    # Render.com specific paths
    CHROME_PATH = '/usr/bin/chromium'
    CHROMEDRIVER_PATH = '/usr/bin/chromedriver'
elif platform.system() == "Windows":
    # Default Windows paths
    CHROME_PATH = os.getenv('CHROME_BIN', 
                          r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    
    CHROMEDRIVER_PATH = os.getenv('CHROME_DRIVER', 
                                shutil.which('chromedriver') or r"C:\Program Files\chromedriver\chromedriver.exe")
else:
    # Linux/Docker paths
    CHROME_PATH = os.getenv('CHROME_BIN', '/usr/bin/chromium')
    CHROMEDRIVER_PATH = os.getenv('CHROME_DRIVER', '/usr/lib/chromium/chromedriver')

# Global storage
monitored_urls = {}
is_monitoring = False
SECURITY_LOG = "activity.log"

# Thread pool for concurrent URL checking
url_check_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHECKS)

def kill_previous_instances():
    current_pid = os.getpid()
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.info['name'].lower():
                    cmdline = ' '.join(proc.info['cmdline'])
                    if 'zealy_bot.py' in cmdline and proc.info['pid'] != current_pid:
                        print(f"🚨 Killing previous instance (PID: {proc.info['pid']})")
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue
    except Exception as e:
        print(f"Warning: Error checking previous instances: {e}")

def get_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")  # Smaller window for faster rendering
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")  # Keep disabled for speed
    options.add_argument("--disable-javascript")  # Keep disabled for speed
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=TranslateUI")
    options.add_argument("--disable-iframes")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-component-update")
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--memory-pressure-off")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Add special options for Render.com
    if IS_RENDER:
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-zygote")
        options.add_argument("--single-process")
        options.add_argument("--max_old_space_size=2048")  # Reduced memory allocation
    
    # Use environment variables for paths
    print(f"🕵️ Using Chrome binary path: {CHROME_PATH}")
    print(f"🕵️ Using Chromedriver path: {CHROMEDRIVER_PATH}")
    
    # Check if we're in local development and paths should exist
    if not IS_RENDER:
        if not os.path.exists(CHROME_PATH):
            print(f"⚠️ WARNING: Chrome not found at expected path: {CHROME_PATH}")
            # Try to locate Chrome/Chromium
            if platform.system() == "Windows":
                possible_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ]
                for path in possible_paths:
                    if os.path.exists(path):
                        print(f"✅ Found Chrome at: {path}")
                        options.binary_location = path
                        break
        else:
            options.binary_location = CHROME_PATH
    else:
        # On Render, we trust the paths exist
        options.binary_location = CHROME_PATH
        
    return options

def get_content_hash(url):
    """Optimized content hash function with faster timeouts and fewer retries"""
    driver = None
    max_retries = 2  # Reduced from 3 to 2 retries
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            print(f"🌐 Checking URL: {url} (Attempt {retry_count + 1}/{max_retries})")
            options = get_chrome_options()
            
            try:
                if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
                    # On Render or if we can't find chromedriver, let Selenium find it automatically
                    driver = webdriver.Chrome(options=options)
                else:
                    # Use specified path when available
                    service = Service(executable_path=CHROMEDRIVER_PATH)
                    driver = webdriver.Chrome(service=service, options=options)
                    
                # Set faster timeouts
                driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
                driver.implicitly_wait(2)  # Reduced implicit wait
                
                start_time = time.time()
                driver.get(url)
                
                # Try multiple selectors with reduced timeout
                selectors_to_try = [
                    ZEALY_CONTAINER_SELECTOR
                ]
                
                container = None
                for selector in selectors_to_try:
                    try:
                        container = WebDriverWait(driver, ELEMENT_WAIT_TIMEOUT).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        print(f"✅ Found element with selector: {selector}")
                        break
                    except TimeoutException:
                        continue
                
                if not container:
                    print("❌ No suitable container found")
                    if retry_count < max_retries - 1:
                        retry_count += 1
                        time.sleep(2)  # Reduced retry wait
                        continue
                    return None
                
                # Minimal wait for content - reduced from 2 to 0.5 seconds
                time.sleep(0.5)
                content = container.text
                
                if not content or len(content.strip()) < 10:
                    print(f"⚠️ Content too short: {len(content)} chars")
                    if retry_count < max_retries - 1:
                        retry_count += 1
                        time.sleep(2)
                        continue
                    return None
                
                # Clean content and generate hash
                clean_content = re.sub(
                    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', 
                    '', 
                    content
                )
                content_hash = hashlib.sha256(clean_content.strip().encode()).hexdigest()
                
                elapsed = time.time() - start_time
                print(f"🔢 Hash generated in {elapsed:.2f}s: {content_hash[:8]}...")
                return content_hash
                
            except TimeoutException:
                print(f"⚠️ Timeout on {url}")
                if retry_count < max_retries - 1:
                    retry_count += 1
                    time.sleep(2)
                    continue
                return None
            except WebDriverException as e:
                print(f"⚠️ WebDriver error: {str(e)}")
                if retry_count < max_retries - 1:
                    retry_count += 1
                    time.sleep(2)
                    continue
                return None
        except Exception as e:
            print(f"❌ Content check error: {str(e)}")
            if retry_count < max_retries - 1:
                retry_count += 1
                time.sleep(2)
                continue
            return None
        finally:
            try:
                if driver:
                    driver.quit()
                    driver = None
            except Exception as e:
                print(f"⚠️ Error closing WebDriver: {str(e)}")
    
    return None

def check_single_url(url_data):
    """Check a single URL and return results"""
    url, data = url_data
    try:
        start_time = time.time()
        current_hash = get_content_hash(url)
        elapsed = time.time() - start_time
        
        return {
            'url': url,
            'hash': current_hash,
            'elapsed': elapsed,
            'success': current_hash is not None,
            'previous_hash': data['hash']
        }
    except Exception as e:
        print(f"❌ Error checking {url}: {str(e)}")
        return {
            'url': url,
            'hash': None,
            'elapsed': 0,
            'success': False,
            'error': str(e),
            'previous_hash': data['hash']
        }

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
            await asyncio.sleep(2)  # Reduced retry delay
    return False

async def check_urls(bot):
    """Optimized concurrent URL checking"""
    global monitored_urls
    current_time = time.time()
    
    if not monitored_urls:
        print("⚠️ No URLs to check")
        return
    
    print(f"🔍 Starting concurrent check of {len(monitored_urls)} URLs")
    
    # Prepare URL data for concurrent checking
    url_items = list(monitored_urls.items())
    
    # Submit all URL checks concurrently
    futures = []
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_CHECKS, len(url_items))) as executor:
        for url_data in url_items:
            future = executor.submit(check_single_url, url_data)
            futures.append(future)
        
        # Process results as they complete
        for future in as_completed(futures):
            try:
                result = future.result(timeout=REQUEST_TIMEOUT + 5)  # Add buffer to timeout
                url = result['url']
                
                # Skip if URL was removed during checking
                if url not in monitored_urls:
                    continue
                
                if result['success']:
                    # Reset failure count on successful check
                    monitored_urls[url]['failures'] = 0
                    monitored_urls[url]['last_checked'] = current_time
                    
                    # Check for changes
                    if monitored_urls[url]['hash'] != result['hash']:
                        print(f"🔔 CHANGE DETECTED: {url} (Response: {result['elapsed']:.2f}s)")
                        
                        # Send immediate notification (removed 300s cooldown for real-time updates)
                        success = await send_notification(
                            bot, 
                            f"🚨 CHANGE DETECTED!\n{url}\n⚡ Response time: {result['elapsed']:.2f}s\n🕐 Detected at: {datetime.now().strftime('%H:%M:%S')}"
                        )
                        
                        if success:
                            monitored_urls[url].update({
                                'last_notified': current_time,
                                'hash': result['hash']
                            })
                            print(f"✅ Notification sent for {url}")
                        else:
                            print(f"❌ Failed to send notification for {url}")
                    else:
                        print(f"✓ No changes: {url} ({result['elapsed']:.2f}s)")
                else:
                    # Handle failure
                    monitored_urls[url]['failures'] += 1
                    failure_count = monitored_urls[url]['failures']
                    print(f"⚠️ Failed to check {url} - Failure #{failure_count}/{MAX_FAILURES_THRESHOLD}")
                    
                    # Only remove after reaching the higher threshold
                    if failure_count >= MAX_FAILURES_THRESHOLD:
                        del monitored_urls[url]
                        await send_notification(bot, f"🔴 Removed from monitoring after {MAX_FAILURES_THRESHOLD} consecutive failures: {url}")
                        print(f"🗑️ Removed {url} after {MAX_FAILURES_THRESHOLD} failures")
                        
            except concurrent.futures.TimeoutError:
                print(f"⚠️ Timeout processing URL check result")
            except Exception as e:
                print(f"⚠️ Error processing result: {str(e)}")
    
    print(f"✅ Concurrent check completed for {len(monitored_urls)} URLs")

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Zealy Monitoring Bot (Optimized for Real-time)\n\n"
        "Commands:\n"
        "/add <url> - Add monitoring URL\n"
        "/remove <number> - Remove URL by number\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/purge - Remove all URLs\n"
        f"Max URLs: {MAX_URLS}\n"
        f"⚡ Check interval: {CHECK_INTERVAL}s\n"
        f"🛡️ Failure threshold: {MAX_FAILURES_THRESHOLD}"
    )

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("No monitored URLs")
        return
    
    message_parts = ["📋 Monitored URLs:"]
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        failures = data.get('failures', 0)
        last_checked = data.get('last_checked', 0)
        if last_checked:
            time_ago = int(time.time() - last_checked)
            time_str = f"({time_ago}s ago)"
        else:
            time_str = "(never)"
        
        status = "🔴" if failures > 0 else "🟢"
        message_parts.append(f"{idx}. {status} {url} {time_str}")
        if failures > 0:
            message_parts.append(f"   ⚠️ Failures: {failures}/{MAX_FAILURES_THRESHOLD}")
    
    message = "\n".join(message_parts)[:4000]
    await update.message.reply_text(message)

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to remove")
        return
        
    try:
        if not context.args or not context.args[0]:
            await update.message.reply_text("❌ Usage: /remove <number>\nUse /list to see URL numbers")
            return
            
        try:
            url_index = int(context.args[0]) - 1  # Convert to 0-based index
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")
            return
            
        url_list = list(monitored_urls.keys())
        
        if url_index < 0 or url_index >= len(url_list):
            await update.message.reply_text(f"❌ Invalid number. Use a number between 1 and {len(url_list)}")
            return
            
        url_to_remove = url_list[url_index]
        del monitored_urls[url_to_remove]
        
        await update.message.reply_text(
            f"✅ Removed: {url_to_remove}\n📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}"
        )
        print(f"🗑️ Manually removed URL: {url_to_remove}")
        
    except Exception as e:
        print(f"⚠️ Error in remove_url: {str(e)}")
        await update.message.reply_text(f"❌ Error removing URL: {str(e)}")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(f"❌ Maximum URLs limit ({MAX_URLS}) reached")
        return
        
    try:
        # Check if args exist
        if not context.args or not context.args[0]:
            await update.message.reply_text("❌ Usage: /add <zealy-url>")
            return
            
        url = context.args[0].lower()
        print(f"📥 Attempting to add URL: {url}")
        
        # Validate URL format
        if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+$', url):
            await update.message.reply_text("❌ Invalid Zealy URL format")
            return
            
        # Check if already monitoring
        if url in monitored_urls:
            await update.message.reply_text("ℹ️ URL already monitored")
            return
            
        # Show processing message
        processing_msg = await update.message.reply_text("⏳ Verifying URL (fast check)...")
        
        # Get initial hash in a separate thread
        try:
            loop = asyncio.get_event_loop()
            print(f"🔄 Getting initial hash for {url}")
            start_time = time.time()
            
            initial_hash = await loop.run_in_executor(url_check_executor, get_content_hash, url)
            elapsed = time.time() - start_time
            
            if not initial_hash:
                await processing_msg.edit_text("❌ Failed to verify URL content. Check console for details.")
                return
                
            # Add to monitored URLs
            monitored_urls[url] = {
                'hash': initial_hash,
                'last_notified': 0,
                'last_checked': time.time(),
                'failures': 0
            }
            
            print(f"✅ URL added successfully: {url}")
            await processing_msg.edit_text(
                f"✅ Added: {url}\n📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}\n⚡ Initial check: {elapsed:.2f}s"
            )
            
        except Exception as e:
            print(f"❌ Error while getting initial hash: {str(e)}")
            await processing_msg.edit_text(f"❌ Failed to add URL: {str(e)}")
            
    except IndexError:
        await update.message.reply_text("❌ Usage: /add <zealy-url>")
    except Exception as e:
        print(f"⚠️ Error in add_url: {str(e)}")
        await update.message.reply_text(f"❌ Internal server error: {str(e)}")

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    if is_monitoring:
        await update.message.reply_text("⚠️ Already monitoring")
        return
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to monitor")
        return
    
    try:
        # Set flag first
        is_monitoring = True
        # Create monitoring task
        monitor_task = asyncio.create_task(start_monitoring(context.application))
        # Store task in context for reference
        context.chat_data['monitor_task'] = monitor_task
        await update.message.reply_text(
            f"✅ Real-time monitoring started!\n"
            f"⚡ Check interval: {CHECK_INTERVAL}s\n"
            f"🔗 Monitoring {len(monitored_urls)} URLs\n"
            f"🚀 Concurrent checks: {MAX_CONCURRENT_CHECKS}"
        )
        print("✅ Optimized monitoring task created and started")
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(f"❌ Failed to start monitoring: {str(e)}")
        print(f"❌ Error starting monitoring: {str(e)}")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    
    # Try to cancel the task if it exists
    if 'monitor_task' in context.chat_data:
        try:
            context.chat_data['monitor_task'].cancel()
            del context.chat_data['monitor_task']
            print("🛑 Monitoring task cancelled")
        except Exception as e:
            print(f"⚠️ Error cancelling task: {str(e)}")
    
    await update.message.reply_text("🛑 Monitoring stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    monitored_urls.clear()
    await update.message.reply_text("✅ All URLs purged!")

async def start_monitoring(application: Application):
    global is_monitoring
    bot = application.bot
    await send_notification(bot, f"🔔 Real-time monitoring started! (Check every {CHECK_INTERVAL}s)")
    print("🔍 Entering optimized monitoring loop")
    
    while is_monitoring:
        try:
            print(f"🔄 Running concurrent URL check - {len(monitored_urls)} URLs")
            start_time = time.time()
            await check_urls(bot)
            elapsed = time.time() - start_time
            
            # Adaptive wait time to maintain consistent interval
            wait_time = max(CHECK_INTERVAL - elapsed, 1)  # Minimum 1 second wait
            print(f"✓ Check cycle complete in {elapsed:.2f}s, waiting {wait_time:.2f}s")
            
            await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            print("🚫 Monitoring task was cancelled")
            break
        except Exception as e:
            print(f"🚨 Monitoring error: {str(e)}")
            print(traceback.format_exc())
            # Shorter sleep on error to maintain responsiveness
            await asyncio.sleep(10)
    
    print("👋 Exiting monitoring loop")
    await send_notification(bot, "🔴 Monitoring stopped!")

def main():
    try:
        global CHROME_PATH, CHROMEDRIVER_PATH
        
        print(f"🚀 Starting optimized bot at {datetime.now()}")
        print(f"⚡ Check interval: {CHECK_INTERVAL}s")
        print(f"🔗 Max concurrent checks: {MAX_CONCURRENT_CHECKS}")
        print(f"🛡️ Failure threshold: {MAX_FAILURES_THRESHOLD}")
        
        kill_previous_instances()

        # Debug environment info
        print(f"🌍 Operating System: {platform.system()}")
        print(f"🌍 Running on Render: {IS_RENDER}")
        print(f"💾 Chrome path: {CHROME_PATH}")
        print(f"💾 Chromedriver path: {CHROMEDRIVER_PATH}")
        
        # Only check files locally, not on Render
        if not IS_RENDER:
            print(f"📂 Chrome exists: {os.path.exists(CHROME_PATH)}")
            print(f"📂 Chromedriver exists: {os.path.exists(CHROMEDRIVER_PATH)}")
            
            # Try to find Chrome and Chromedriver if not at expected locations
            chrome_path_to_use = CHROME_PATH
            chromedriver_path_to_use = CHROMEDRIVER_PATH
            
            if not os.path.exists(chrome_path_to_use):
                if platform.system() == "Windows":
                    chrome_possible_paths = [
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                    ]
                    for path in chrome_possible_paths:
                        if os.path.exists(path):
                            print(f"✅ Found Chrome at: {path}")
                            chrome_path_to_use = path
                            break
            
            if not os.path.exists(chromedriver_path_to_use):
                chromedriver_in_path = shutil.which('chromedriver')
                if chromedriver_in_path:
                    print(f"✅ Found Chromedriver in PATH: {chromedriver_in_path}")
                    chromedriver_path_to_use = chromedriver_in_path
                    
            # Update global variables with found paths
            if chrome_path_to_use != CHROME_PATH or chromedriver_path_to_use != CHROMEDRIVER_PATH:
                CHROME_PATH = chrome_path_to_use
                CHROMEDRIVER_PATH = chromedriver_path_to_use
                print(f"📌 Using Chrome at: {CHROME_PATH}")
                print(f"📌 Using Chromedriver at: {CHROMEDRIVER_PATH}")
        
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        print("Creating Telegram application...")
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .concurrent_updates(True)
            .post_init(lambda app: app.bot.delete_webhook(drop_pending_updates=True))
            .build()
        )

        print("Adding handlers...")
        application.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
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
            application.add_handler(handler)

        print("Starting polling...")
        application.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Graceful shutdown")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {str(e)}")
        print(traceback.format_exc())
        input("Press Enter to exit...")
    finally:
        try:
            url_check_executor.shutdown()
        except:
            pass
        print("🧹 Cleaning up...")

if __name__ == "__main__":
    print("Script starting...")
    try:
        main()
    except Exception as e:
        print(f"❌ CRITICAL ERROR in __main__: {str(e)}")
        print(traceback.format_exc())
        input("Press Enter to exit...")