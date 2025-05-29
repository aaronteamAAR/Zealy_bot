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
import signal
import atexit

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
        NoSuchElementException,
        InvalidSessionIdException
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

# OPTIMIZED Configuration for speed and reliability
CHECK_INTERVAL = 10  # Reduced from 25 to 10 seconds for faster real-time monitoring
MAX_URLS = 20 
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 15  # Reduced from 30 to 15 seconds
PAGE_LOAD_TIMEOUT = 10  # Reduced page load timeout
ELEMENT_WAIT_TIMEOUT = 8  # Reduced element wait timeout
MAX_FAILURES_BEFORE_REMOVAL = 8  # Increased from 5 to 8 to prevent premature removal
CONCURRENT_CHECKS = 3  # Number of URLs to check simultaneously

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
driver_pool = []
SECURITY_LOG = "activity.log"

def cleanup_drivers():
    """Clean up all driver instances on exit"""
    global driver_pool
    print("🧹 Cleaning up WebDriver instances...")
    for driver in driver_pool:
        try:
            if driver:
                driver.quit()
        except:
            pass
    driver_pool.clear()

# Register cleanup on exit
atexit.register(cleanup_drivers)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    print(f"\n🛑 Received signal {signum}, shutting down gracefully...")
    cleanup_drivers()
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

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

def get_optimized_chrome_options():
    """Get optimized Chrome options for faster performance"""
    options = Options()
    
    # Essential options for headless operation
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    
    # Performance optimizations
    options.add_argument("--disable-features=VizDisplayCompositor,TranslateUI")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")  # Skip loading images for faster page loads
    options.add_argument("--disable-javascript")  # We don't need JS for static content
    options.add_argument("--disable-css")  # Skip CSS processing
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-background-networking")
    
    # Memory optimizations
    options.add_argument("--memory-pressure-off")
    options.add_argument("--max_old_space_size=2048")
    options.add_argument("--window-size=1280,720")  # Smaller window for less memory usage
    
    # Network optimizations
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    
    # User agent for better compatibility
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Add special options for Render.com
    if IS_RENDER:
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-zygote")
        options.add_argument("--single-process")
    
    # Set binary location
    if not IS_RENDER:
        if not os.path.exists(CHROME_PATH):
            print(f"⚠️ WARNING: Chrome not found at expected path: {CHROME_PATH}")
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
        options.binary_location = CHROME_PATH
        
    return options

def create_driver():
    """Create a new WebDriver instance with optimized settings"""
    try:
        options = get_optimized_chrome_options()
        
        if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
            driver = webdriver.Chrome(options=options)
        else:
            service = Service(executable_path=CHROMEDRIVER_PATH)
            driver = webdriver.Chrome(service=service, options=options)
        
        # Set optimized timeouts
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(2)  # Reduced implicit wait
        
        return driver
    except Exception as e:
        print(f"❌ Failed to create WebDriver: {str(e)}")
        return None

def get_content_hash(url):
    """Optimized content hash function with better error handling"""
    driver = None
    max_retries = 2  # Reduced from 3 to 2 for faster response
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            print(f"🌐 Checking URL: {url} (Attempt {retry_count + 1}/{max_retries})")
            
            # Create fresh driver for each check to avoid session issues
            driver = create_driver()
            if not driver:
                print(f"❌ Failed to create driver for {url}")
                retry_count += 1
                continue
            
            # Add driver to pool for cleanup
            driver_pool.append(driver)
            
            print(f"🌐 Loading URL: {url}")
            start_time = time.time()
            
            try:
                driver.get(url)
                load_time = time.time() - start_time
                print(f"📄 Page loaded in {load_time:.2f}s")
                
                # Try multiple selectors with shorter timeouts
                selectors_to_try = [
                    ZEALY_CONTAINER_SELECTOR,
                    "div[class*='flex'][class*='flex-col']",
                    "main",
                    "body"
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
                    print(f"❌ No suitable container found for {url}")
                    retry_count += 1
                    continue
                
                # Quick content extraction without additional wait
                content = container.text
                
                if not content or len(content.strip()) < 10:
                    print(f"⚠️ Content too short for {url}: {len(content)} chars")
                    retry_count += 1
                    continue
                
                print(f"📄 Content retrieved: {len(content)} chars")
                
                # Optimized content cleaning
                clean_content = re.sub(
                    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', 
                    '', 
                    content
                )
                
                content_hash = hashlib.sha256(clean_content.strip().encode()).hexdigest()
                total_time = time.time() - start_time
                print(f"🔢 Hash: {content_hash[:8]}... (Total time: {total_time:.2f}s)")
                
                return content_hash
                
            except TimeoutException:
                print(f"⚠️ Timeout loading {url}")
                retry_count += 1
                continue
            except (WebDriverException, InvalidSessionIdException) as e:
                print(f"⚠️ WebDriver error for {url}: {str(e)}")
                retry_count += 1
                continue
                
        except Exception as e:
            print(f"❌ Error checking {url}: {str(e)}")
            retry_count += 1
        finally:
            # Always clean up driver immediately
            if driver:
                try:
                    driver.quit()
                    if driver in driver_pool:
                        driver_pool.remove(driver)
                    driver = None
                except Exception as e:
                    print(f"⚠️ Error closing driver: {str(e)}")
    
    return None

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

async def check_single_url(url, url_data, bot):
    """Check a single URL asynchronously"""
    try:
        print(f"🔍 Checking URL: {url}")
        start_time = time.time()
        
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        current_hash = await loop.run_in_executor(None, get_content_hash, url)
        
        if not current_hash:
            url_data['failures'] += 1
            print(f"⚠️ Failed to get hash for {url} - Failure #{url_data['failures']}")
            if url_data['failures'] > MAX_FAILURES_BEFORE_REMOVAL:
                return 'remove', url
            return 'failed', None
            
        # Reset failure count on successful check
        url_data['failures'] = 0
        current_time = time.time()
        
        if url_data['hash'] != current_hash:
            print(f"🔔 Change detected for {url}")
            # Reduced notification cooldown for faster alerts
            if current_time - url_data.get('last_notified', 0) > 180:  # 3 minutes instead of 5
                success = await send_notification(
                    bot, f"🚨 CHANGE DETECTED!\n{url}\nResponse time: {time.time()-start_time:.2f}s")
                if success:
                    url_data.update({
                        'last_notified': current_time,
                        'hash': current_hash,
                        'last_checked': current_time
                    })
                    print(f"✅ Notification sent for {url}")
                    return 'changed', None
                else:
                    print(f"❌ Failed to send notification for {url}")
        else:
            print(f"✓ No changes for {url}")
        
        url_data['last_checked'] = current_time
        return 'success', None
        
    except Exception as e:
        print(f"⚠️ Error processing {url}: {str(e)}")
        return 'error', None

async def check_urls(bot):
    """Optimized URL checking with concurrent processing"""
    global monitored_urls
    
    if not monitored_urls:
        print("⚠️ No URLs to check")
        return
    
    print(f"🔄 Starting concurrent check of {len(monitored_urls)} URLs")
    
    # Create semaphore to limit concurrent checks
    semaphore = asyncio.Semaphore(CONCURRENT_CHECKS)
    
    async def check_with_semaphore(url, url_data):
        async with semaphore:
            return await check_single_url(url, url_data, bot)
    
    # Start all checks concurrently
    tasks = []
    urls_to_check = list(monitored_urls.items())
    
    for url, url_data in urls_to_check:
        task = asyncio.create_task(check_with_semaphore(url, url_data))
        tasks.append((url, task))
    
    # Process results as they complete
    urls_to_remove = []
    for url, task in tasks:
        try:
            result, remove_url = await task
            if result == 'remove':
                urls_to_remove.append(remove_url)
        except Exception as e:
            print(f"⚠️ Error in task for {url}: {str(e)}")
    
    # Remove failed URLs
    for url in urls_to_remove:
        if url in monitored_urls:
            del monitored_urls[url]
            await send_notification(bot, f"🔴 Removed from monitoring due to repeated failures: {url}")
            print(f"🗑️ Removed {url} after {MAX_FAILURES_BEFORE_REMOVAL} failures")
    
    print(f"✅ Completed checking {len(monitored_urls)} URLs")

# Command handlers (keeping original functionality)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Zealy Monitoring Bot (Optimized)\n\n"
        "Commands:\n"
        "/add <url> - Add monitoring URL\n"
        "/remove <number> - Remove URL by number\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/purge - Remove all URLs\n"
        f"Max URLs: {MAX_URLS}\n"
        f"Check Interval: {CHECK_INTERVAL}s (Real-time)"
    )

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("No monitored URLs")
        return
    
    message = ["📋 Monitored URLs:"]
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        failures = data.get('failures', 0)
        last_checked = data.get('last_checked', 0)
        if last_checked:
            last_check_str = f"({int(time.time() - last_checked)}s ago)"
        else:
            last_check_str = "(Never checked)"
        
        status = f"❌{failures}" if failures > 0 else "✅"
        message.append(f"{idx}. {status} {url} {last_check_str}")
    
    await update.message.reply_text("\n".join(message)[:4000])

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
            url_index = int(context.args[0]) - 1
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
        if not context.args or not context.args[0]:
            await update.message.reply_text("❌ Usage: /add <zealy-url>")
            return
            
        url = context.args[0].lower()
        print(f"📥 Attempting to add URL: {url}")
        
        if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+$', url):
            await update.message.reply_text("❌ Invalid Zealy URL format")
            return
            
        if url in monitored_urls:
            await update.message.reply_text("ℹ️ URL already monitored")
            return
            
        processing_msg = await update.message.reply_text("⏳ Verifying URL (optimized)...")
        
        try:
            loop = asyncio.get_event_loop()
            print(f"🔄 Getting initial hash for {url}")
            initial_hash = await loop.run_in_executor(None, get_content_hash, url)
            
            if not initial_hash:
                await processing_msg.edit_text("❌ Failed to verify URL content. Check console for details.")
                return
                
            monitored_urls[url] = {
                'hash': initial_hash,
                'last_notified': 0,
                'last_checked': time.time(),
                'failures': 0
            }
            
            print(f"✅ URL added successfully: {url}")
            await processing_msg.edit_text(
                f"✅ Added: {url}\n📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}\n⚡ Real-time monitoring active"
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
        is_monitoring = True
        monitor_task = asyncio.create_task(start_monitoring(context.application))
        context.chat_data['monitor_task'] = monitor_task
        await update.message.reply_text(f"✅ Real-time monitoring started!\n⚡ Checking every {CHECK_INTERVAL}s")
        print("✅ Optimized monitoring task started")
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(f"❌ Failed to start monitoring: {str(e)}")
        print(f"❌ Error starting monitoring: {str(e)}")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    
    if 'monitor_task' in context.chat_data:
        try:
            context.chat_data['monitor_task'].cancel()
            del context.chat_data['monitor_task']
            print("🛑 Monitoring task cancelled")
        except Exception as e:
            print(f"⚠️ Error cancelling task: {str(e)}")
    
    # Clean up any remaining drivers
    cleanup_drivers()
    await update.message.reply_text("🛑 Monitoring stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    monitored_urls.clear()
    cleanup_drivers()  # Clean up drivers when purging
    await update.message.reply_text("✅ All URLs purged!")

async def start_monitoring(application: Application):
    """Optimized monitoring loop with real-time performance"""
    global is_monitoring
    bot = application.bot
    await send_notification(bot, f"🔔 Real-time monitoring started! (Check interval: {CHECK_INTERVAL}s)")
    print("🔍 Entering optimized monitoring loop")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while is_monitoring:
        try:
            cycle_start = time.time()
            print(f"🔄 Starting optimized check cycle - {len(monitored_urls)} URLs")
            
            await check_urls(bot)
            
            cycle_time = time.time() - cycle_start
            print(f"✓ Cycle completed in {cycle_time:.2f}s")
            
            # Reset error counter on successful cycle
            consecutive_errors = 0
            
            # Calculate optimal wait time
            optimal_wait = max(CHECK_INTERVAL - cycle_time, 1)
            print(f"⏰ Waiting {optimal_wait:.2f}s before next cycle")
            await asyncio.sleep(optimal_wait)
            
        except asyncio.CancelledError:
            print("🚫 Monitoring task was cancelled")
            break
        except Exception as e:
            consecutive_errors += 1
            print(f"🚨 Monitoring error ({consecutive_errors}/{max_consecutive_errors}): {str(e)}")
            print(traceback.format_exc())
            
            if consecutive_errors >= max_consecutive_errors:
                print("🔴 Too many consecutive errors, stopping monitoring")
                await send_notification(bot, "🔴 Monitoring stopped due to repeated errors!")
                break
            
            # Shorter sleep on error to resume quickly
            await asyncio.sleep(min(10, CHECK_INTERVAL))
    
    print("👋 Exiting optimized monitoring loop")
    cleanup_drivers()  # Clean up on exit
    await send_notification(bot, "🔴 Real-time monitoring stopped!")

def main():
    try:
        global CHROME_PATH, CHROMEDRIVER_PATH
        
        print(f"🚀 Starting bot at {datetime.now()}")
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
        
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
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
            executor.shutdown()
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