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
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
import threading
from queue import Queue, Empty

# First check if required packages are installed
try:
    import psutil
    from dotenv import load_dotenv
    import chromedriver_autoinstaller
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
    if not os.getenv('IS_RENDER', 'false').lower() == 'true':
        input("Press Enter to exit...")
    sys.exit(1)

# DEFINE IS_RENDER FIRST
IS_RENDER = os.getenv('IS_RENDER', 'false').lower() == 'true'

print(f"🚀 Starting Zealy Bot - {'Render' if IS_RENDER else 'Local'} Mode")
print(f"📍 Working directory: {os.getcwd()}")
print(f"🐍 Python version: {sys.version}")

# Load environment variables
if not IS_RENDER:
    print("Loading .env file...")
    load_dotenv()

print("🔍 Loading environment variables...")

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID_STR = os.getenv('CHAT_ID')

print(f"✅ Environment Check:")
print(f"   IS_RENDER: {IS_RENDER}")
print(f"   BOT_TOKEN exists: {bool(TELEGRAM_BOT_TOKEN)}")
print(f"   CHAT_ID exists: {bool(CHAT_ID_STR)}")

if TELEGRAM_BOT_TOKEN:
    print(f"   Bot token length: {len(TELEGRAM_BOT_TOKEN)}")
    print(f"   Bot token preview: {TELEGRAM_BOT_TOKEN[:10]}...")

if CHAT_ID_STR:
    print(f"   Chat ID value: '{CHAT_ID_STR}'")

# Check for missing variables
missing_vars = []
if not TELEGRAM_BOT_TOKEN:
    missing_vars.append("TELEGRAM_BOT_TOKEN")
if not CHAT_ID_STR:
    missing_vars.append("CHAT_ID")

if missing_vars:
    print(f"\n❌ Missing environment variables: {', '.join(missing_vars)}")
    if IS_RENDER:
        print("\n🔧 Render Setup Instructions:")
        print("1. Go to your Render dashboard")
        print("2. Click on your service")
        print("3. Go to Environment tab")
        print("4. Add these variables:")
        for var in missing_vars:
            if var == "TELEGRAM_BOT_TOKEN":
                print(f"   {var} = your_bot_token_from_@BotFather")
            elif var == "CHAT_ID":
                print(f"   {var} = your_chat_id_number")
        print("5. Save and redeploy")
    else:
        print("\n🔧 Local Setup Instructions:")
        print("Create a .env file with:")
        for var in missing_vars:
            if var == "TELEGRAM_BOT_TOKEN":
                print(f"{var}=your_bot_token")
            elif var == "CHAT_ID":
                print(f"{var}=your_chat_id")
    
    print(f"\n💡 After adding variables, restart the bot")
    if not IS_RENDER:
        input("Press Enter to exit...")
    sys.exit(1)

# Parse CHAT_ID
try:
    CHAT_ID = int(CHAT_ID_STR)
    print(f"✅ Chat ID parsed: {CHAT_ID}")
except ValueError:
    print(f"❌ CHAT_ID must be a number, got: '{CHAT_ID_STR}'")
    if not IS_RENDER:
        input("Press Enter to exit...")
    sys.exit(1)

# Chrome setup
print("🔧 Setting up Chrome...")
try:
    if not IS_RENDER:
        chromedriver_autoinstaller.install()
        print("✅ ChromeDriver installed")
except Exception as e:
    print(f"⚠️ ChromeDriver auto-install warning: {e}")

# Configuration - GENEROUS timeouts for reliability
CHECK_INTERVAL = 30
MAX_URLS = 10
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 60  # Generous timeout
MAX_RETRIES = 3  # More retries
RETRY_DELAY_BASE = 5  # Longer delays
FAILURE_THRESHOLD = 5
PAGE_LOAD_TIMEOUT = 120  # 2 minutes for page load
ELEMENT_WAIT_TIMEOUT = 30  # 30 seconds for elements
REACT_WAIT_TIME = 8  # 8 seconds for React to load

# Set Chrome paths
if IS_RENDER:
    CHROME_PATH = '/usr/bin/chromium'
    CHROMEDRIVER_PATH = '/usr/bin/chromedriver'
elif platform.system() == "Windows":
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    CHROMEDRIVER_PATH = shutil.which('chromedriver') or r"C:\chromedriver\chromedriver.exe"
else:
    CHROME_PATH = '/usr/bin/google-chrome'
    CHROMEDRIVER_PATH = shutil.which('chromedriver') or '/usr/bin/chromedriver'

def kill_previous_instances():
    """Kill any previous bot instances"""
    current_pid = os.getpid()
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.info['name'].lower():
                    cmdline = ' '.join(proc.info['cmdline'])
                    if 'zealy' in cmdline.lower() and proc.info['pid'] != current_pid:
                        print(f"🚨 Killing previous instance (PID: {proc.info['pid']})")
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue
    except Exception as e:
        print(f"Warning: Error checking previous instances: {e}")

def get_chrome_options():
    """Get Chrome options optimized for RELIABILITY, not speed"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")  # Larger window for better rendering
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # KEEP JAVASCRIPT ENABLED - Zealy needs it!
    # Only disable non-essential features
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    
    # Memory management without breaking functionality
    options.add_argument("--memory-pressure-off")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    
    if IS_RENDER:
        # Render-specific settings - minimal but necessary
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-infobars")
        options.add_argument("--single-process")
        options.add_argument("--no-zygote")
        options.add_argument("--disable-dev-tools")
        # Generous memory limits
        options.add_argument("--max_old_space_size=1024")  # 1GB heap
        options.add_argument("--js-flags=--max-old-space-size=1024")
    else:
        # Local development - even more generous
        options.add_argument("--max_old_space_size=2048")  # 2GB heap
        options.add_argument("--js-flags=--max-old-space-size=2048")
    
    # Set Chrome binary path
    if os.path.exists(CHROME_PATH):
        options.binary_location = CHROME_PATH
    elif not IS_RENDER and platform.system() == "Windows":
        # Try common Windows Chrome paths
        possible_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
        ]
        for path in possible_paths:
            if os.path.exists(path):
                options.binary_location = path
                break
    
    return options

@dataclass
class URLData:
    hash: str
    last_notified: float
    last_checked: float
    failures: int
    consecutive_successes: int
    last_error: Optional[str] = None
    check_count: int = 0
    avg_response_time: float = 0.0
    
    def update_response_time(self, response_time: float):
        """Update average response time"""
        if self.avg_response_time == 0:
            self.avg_response_time = response_time
        else:
            self.avg_response_time = 0.7 * self.avg_response_time + 0.3 * response_time

# Global variables
monitored_urls: Dict[str, URLData] = {}
is_monitoring = False
notification_queue = Queue()

def create_driver():
    """Create a reliable Chrome driver instance with generous timeouts"""
    try:
        print("🔧 Creating Chrome driver with generous timeouts...")
        options = get_chrome_options()
        
        if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
            driver = webdriver.Chrome(options=options)
        else:
            service = Service(executable_path=CHROMEDRIVER_PATH)
            driver = webdriver.Chrome(service=service, options=options)
        
        # Set generous timeouts
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(10)  # 10 seconds implicit wait
        
        print("✅ Driver created successfully with generous timeouts")
        return driver
        
    except Exception as e:
        print(f"❌ Failed to create driver: {e}")
        print(f"❌ Full error details: {traceback.format_exc()}")
        return None

def get_content_hash_fast(url: str, debug_mode: bool = False) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
    """Get content hash for URL with RELIABLE settings (not fast)"""
    driver = None
    start_time = time.time()
    
    try:
        print(f"🌐 Loading URL with generous timeouts: {url}")
        driver = create_driver()
        
        if not driver:
            return None, time.time() - start_time, "Failed to create driver", None
        
        print(f"🔄 Navigating to URL...")
        driver.get(url)
        
        print(f"⏳ Waiting {REACT_WAIT_TIME} seconds for React to fully render...")
        time.sleep(REACT_WAIT_TIME)
        
        print("⏳ Looking for page elements with generous timeouts...")
        # Try different selectors with generous timeouts
        selectors = [
            ZEALY_CONTAINER_SELECTOR,
            "div[class*='flex'][class*='flex-col']",
            "main",
            "body"
        ]
        
        container = None
        for selector in selectors:
            try:
                print(f"🔍 Trying selector: {selector}")
                container = WebDriverWait(driver, ELEMENT_WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                print(f"✅ Found element with selector: {selector}")
                break
            except TimeoutException:
                print(f"⚠️ Selector {selector} not found after {ELEMENT_WAIT_TIMEOUT}s, trying next...")
                continue
        
        if not container:
            print(f"❌ No suitable container found after trying all selectors")
            return None, time.time() - start_time, "No suitable container found", None
        
        print("⏳ Additional wait for content to stabilize...")
        time.sleep(3)  # Additional wait for content stability
        
        content = container.text
        
        if not content or len(content.strip()) < 10:
            print(f"⚠️ Content too short: {len(content)} chars")
            return None, time.time() - start_time, f"Content too short: {len(content)} chars", None
        
        print(f"📄 Content retrieved successfully, length: {len(content)} chars")
        
        # Enhanced content cleaning to remove dynamic elements
        clean_content = content
        
        # Remove timestamps and dates
        clean_content = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z?', '', clean_content)
        clean_content = re.sub(r'\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?', '', clean_content)
        clean_content = re.sub(r'(?:\d+\s*(?:seconds?|mins?|minutes?|hours?|days?|weeks?|months?|years?)\s*ago)', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:just now|moments? ago|recently)', '', clean_content, flags=re.IGNORECASE)
        
        # Remove XP and point systems
        clean_content = re.sub(r'\d+\s*(?:XP|points?|pts)', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:XP|points?|pts)\s*:\s*\d+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove UUIDs and session identifiers
        clean_content = re.sub(r'\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'\b[a-f0-9]{32}\b', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'\b[a-f0-9]{40}\b', '', clean_content, flags=re.IGNORECASE)
        
        # Remove view counts and engagement metrics
        clean_content = re.sub(r'\d+\s*(?:views?|likes?|shares?|comments?|replies?)', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:views?|likes?|shares?|comments?|replies?)\s*:\s*\d+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove online/active user counts
        clean_content = re.sub(r'\d+\s*(?:online|active|members?|users?)', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:online|active|members?|users?)\s*:\s*\d+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove progress indicators and percentages
        clean_content = re.sub(r'\d+%|\d+/\d+', '', clean_content)
        clean_content = re.sub(r'(?:progress|completed|remaining)\s*:\s*\d+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove dynamic counters
        clean_content = re.sub(r'\d+\s*(?:total|count|number)', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:total|count|number)\s*:\s*\d+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove rank and position indicators
        clean_content = re.sub(r'(?:rank|position)\s*#?\d+', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'#\d+\s*(?:rank|position)', '', clean_content, flags=re.IGNORECASE)
        
        # Remove session-specific data
        clean_content = re.sub(r'session\s*[a-f0-9]+', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'token\s*[a-f0-9]+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove loading states
        clean_content = re.sub(r'(?:loading|refreshing|updating)\.{0,3}', '', clean_content, flags=re.IGNORECASE)
        
        # Normalize whitespace
        clean_content = re.sub(r'\s+', ' ', clean_content)
        clean_content = clean_content.strip()
        
        # Additional Zealy-specific filtering
        clean_content = re.sub(r'(?:quest|task)\s+\d+\s*(?:of|/)\s*\d+', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:day|week|month)\s+\d+', '', clean_content, flags=re.IGNORECASE)
        
        print(f"📄 Content cleaned, original: {len(content)} chars, cleaned: {len(clean_content)} chars")
        
        content_hash = hashlib.sha256(clean_content.encode()).hexdigest()
        response_time = time.time() - start_time
        
        # Return sample for debugging if requested
        content_sample = content[:500] if debug_mode else None
        
        print(f"🔢 Hash generated: {content_hash[:8]}... in {response_time:.2f}s")
        return content_hash, response_time, None, content_sample
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        print(f"❌ {error_msg}")
        print(f"❌ Full traceback: {traceback.format_exc()}")
        return None, time.time() - start_time, error_msg, None
        
    finally:
        if driver:
            try:
                print("🔄 Closing driver...")
                driver.quit()
                print("✅ Driver closed successfully")
            except Exception as e:
                print(f"⚠️ Error closing driver: {e}")

async def check_single_url(url: str, url_data: URLData) -> Tuple[str, bool, Optional[str]]:
    """Check a single URL for changes with generous retry logic"""
    retry_count = 0
    last_error = None
    
    while retry_count < MAX_RETRIES:
        try:
            print(f"🔄 Checking URL (attempt {retry_count + 1}/{MAX_RETRIES}): {url}")
            loop = asyncio.get_event_loop()
            hash_result, response_time, error, content_sample = await loop.run_in_executor(
                None, get_content_hash_fast, url, False
            )
            
            if hash_result is None:
                retry_count += 1
                last_error = error or "Unknown error"
                
                if retry_count < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE * retry_count
                    print(f"⏳ Retrying {url} in {delay:.1f}s (attempt {retry_count + 1}/{MAX_RETRIES})")
                    print(f"⚠️ Last error: {last_error}")
                    await asyncio.sleep(delay)
                    continue
                else:
                    url_data.failures += 1
                    url_data.consecutive_successes = 0
                    url_data.last_error = last_error
                    print(f"❌ Max retries reached for {url}. Failure #{url_data.failures}")
                    print(f"❌ Final error: {last_error}")
                    return url, False, last_error
            
            # Success case
            url_data.failures = 0
            url_data.consecutive_successes += 1
            url_data.last_error = None
            url_data.check_count += 1
            url_data.update_response_time(response_time)
            url_data.last_checked = time.time()
            
            # Check for changes
            has_changes = url_data.hash != hash_result
            if has_changes:
                print(f"🔔 Change detected for {url}")
                url_data.hash = hash_result
                return url, True, None
            else:
                print(f"✓ No changes for {url} (avg: {url_data.avg_response_time:.2f}s)")
                return url, False, None
                
        except Exception as e:
            retry_count += 1
            last_error = f"Unexpected error: {str(e)}"
            print(f"⚠️ Error checking {url}: {last_error}")
            print(f"⚠️ Full traceback: {traceback.format_exc()}")
            
            if retry_count < MAX_RETRIES:
                delay = RETRY_DELAY_BASE * retry_count
                print(f"⏳ Retrying after error in {delay}s...")
                await asyncio.sleep(delay)
            else:
                url_data.failures += 1
                url_data.consecutive_successes = 0
                url_data.last_error = last_error
                return url, False, last_error
    
    return url, False, last_error

async def send_notification(bot, message: str, priority: bool = False):
    """Send Telegram notification with retry logic"""
    retries = 0
    backoff_delay = 1
    
    while retries < 3:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"✅ Sent notification: {message[:50]}...")
            return True
        except (TelegramError, NetworkError) as e:
            print(f"📡 Network error: {str(e)} - Retry {retries+1}/3")
            retries += 1
            if retries < 3:
                await asyncio.sleep(backoff_delay)
                backoff_delay *= 2
    
    print(f"❌ Failed to send notification after 3 retries")
    return False

async def check_urls_sequential(bot):
    """Check URLs sequentially (one by one) with detailed logging"""
    global monitored_urls
    current_time = time.time()
    
    if not monitored_urls:
        print("⚠️ No URLs to check")
        return
    
    print(f"🔍 Checking {len(monitored_urls)} URLs sequentially...")
    
    changes_detected = 0
    urls_to_remove = []
    
    for idx, (url, url_data) in enumerate(list(monitored_urls.items()), 1):
        try:
            print(f"\n🔄 Processing URL {idx}/{len(monitored_urls)}: {url}")
            result = await check_single_url(url, url_data)
            
            if isinstance(result, Exception):
                print(f"⚠️ Task exception: {result}")
                continue
                
            url, has_changes, error = result
            
            if url not in monitored_urls:
                print(f"⚠️ URL {url} was removed during processing")
                continue
                
            url_data = monitored_urls[url]
            
            if has_changes:
                changes_detected += 1
                # Check rate limiting for notifications
                if current_time - url_data.last_notified > 60:
                    await send_notification(
                        bot, 
                        f"🚨 CHANGE DETECTED!\n{url}\nAvg response: {url_data.avg_response_time:.2f}s\nCheck #{url_data.check_count}",
                        priority=True
                    )
                    url_data.last_notified = current_time
                else:
                    print(f"🔕 Change detected but notification rate limited")
            
            # Handle failures with generous threshold
            if url_data.failures > FAILURE_THRESHOLD:
                urls_to_remove.append(url)
                print(f"🗑️ Marking {url} for removal after {url_data.failures} failures")
            elif url_data.failures > 3 and url_data.consecutive_successes == 0:
                await send_notification(
                    bot,
                    f"⚠️ Monitoring issues for {url}\nFailures: {url_data.failures}/{FAILURE_THRESHOLD}\nLast error: {url_data.last_error or 'Unknown'}"
                )
                
        except Exception as e:
            print(f"⚠️ Error processing URL {url}: {e}")
            print(f"⚠️ Full traceback: {traceback.format_exc()}")
    
    # Remove problematic URLs
    for url in urls_to_remove:
        del monitored_urls[url]
        await send_notification(
            bot, 
            f"🔴 Removed from monitoring (too many failures): {url}",
            priority=True
        )
        print(f"🗑️ Removed {url} after {FAILURE_THRESHOLD} failures")
    
    print(f"✅ Sequential check complete: {changes_detected} changes, {len(urls_to_remove)} removed")

# AUTH MIDDLEWARE
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    print(f"📨 Message from chat ID: {user_id}")
    print(f"📨 Expected chat ID: {CHAT_ID}")
    print(f"📨 Match: {user_id == CHAT_ID}")
    
    if user_id != CHAT_ID:
        print(f"🚫 Unauthorized access from chat ID: {user_id}")
        await update.message.reply_text(f"🚫 Unauthorized access! Your chat ID: {user_id}")
        raise ApplicationHandlerStop
    else:
        print(f"✅ Authorized access from chat ID: {user_id}")

# COMMAND HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("📨 /start command received!")
    await update.message.reply_text(
        "🚀 Zealy Monitoring Bot (RELIABLE MODE)\n\n"
        "Commands:\n"
        "/add <url> - Add Zealy URL to monitor\n"
        "/remove <number> - Remove URL by number\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/status - Show monitoring statistics\n"
        "/debug <number> - Debug URL content\n"
        "/purge - Remove all URLs\n"
        f"\nMax URLs: {MAX_URLS}\n"
        f"Check interval: {CHECK_INTERVAL}s\n"
        f"Page timeout: {PAGE_LOAD_TIMEOUT}s\n"
        f"Element timeout: {ELEMENT_WAIT_TIMEOUT}s\n"
        "Configured for RELIABILITY over speed!"
    )

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("📨 /add command received!")
    
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(f"❌ Maximum URLs limit ({MAX_URLS}) reached")
        return
    
    if not context.args or not context.args[0]:
        await update.message.reply_text("❌ Usage: /add <zealy-url>")
        return
    
    url = context.args[0].lower()
    print(f"📥 Attempting to add URL: {url}")
    
    if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+', url):
        await update.message.reply_text("❌ Invalid Zealy URL format")
        return
    
    if url in monitored_urls:
        await update.message.reply_text("ℹ️ URL already monitored")
        return
    
    processing_msg = await update.message.reply_text(
        f"⏳ Verifying URL with generous timeouts...\n"
        f"This may take up to {PAGE_LOAD_TIMEOUT + ELEMENT_WAIT_TIMEOUT + REACT_WAIT_TIME} seconds.\n"
        f"Please be patient for reliability!"
    )
    
    try:
        loop = asyncio.get_event_loop()
        print(f"🔄 Getting initial hash for {url} with generous timeouts...")
        
        # Update user with progress
        try:
            await processing_msg.edit_text(
                f"⏳ Loading {url}...\n"
                f"Step 1/3: Creating browser session..."
            )
        except:
            pass
        
        initial_hash, response_time, error, content_sample = await loop.run_in_executor(
            None, get_content_hash_fast, url, False
        )
        
        if not initial_hash:
            await processing_msg.edit_text(f"❌ Failed to verify URL: {error}")
            return
        
        monitored_urls[url] = URLData(
            hash=initial_hash,
            last_notified=0,
            last_checked=time.time(),
            failures=0,
            consecutive_successes=1,
            check_count=1,
            avg_response_time=response_time
        )
        
        print(f"✅ URL added successfully: {url}")
        await processing_msg.edit_text(
            f"✅ Successfully added: {url}\n"
            f"📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}\n"
            f"⚡ Initial load time: {response_time:.2f}s\n"
            f"🔢 Content hash: {initial_hash[:12]}..."
        )
        
    except Exception as e:
        print(f"❌ Error while getting initial hash: {str(e)}")
        print(f"❌ Full traceback: {traceback.format_exc()}")
        try:
            await processing_msg.edit_text(f"❌ Failed to add URL: {str(e)}")
        except:
            print(f"❌ Could not edit message: {str(e)}")

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("📋 No URLs monitored")
        return
    
    message_lines = ["📋 Monitored URLs:\n"]
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        status = "✅" if data.failures == 0 else f"⚠️({data.failures})"
        avg_time = f" | {data.avg_response_time:.1f}s" if data.avg_response_time > 0 else ""
        message_lines.append(f"{idx}. {status} {url}{avg_time}")
    
    message_lines.append(f"\n📊 Using {len(monitored_urls)}/{MAX_URLS} slots")
    message_lines.append(f"⚙️ Reliable mode: {PAGE_LOAD_TIMEOUT}s page timeout")
    message = "\n".join(message_lines)[:4000]
    await update.message.reply_text(message)

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to remove")
        return
    
    if not context.args or not context.args[0]:
        await update.message.reply_text("❌ Usage: /remove <number>\nUse /list to see URL numbers")
        return
    
    try:
        url_index = int(context.args[0]) - 1
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
        
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")
    except Exception as e:
        print(f"⚠️ Error in remove_url: {str(e)}")
        await update.message.reply_text(f"❌ Error removing URL: {str(e)}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("📊 No URLs being monitored")
        return
    
    status_lines = ["📊 Monitoring Statistics:\n"]
    
    total_checks = 0
    total_failures = 0
    avg_response_times = []
    
    for url, data in monitored_urls.items():
        total_checks += data.check_count
        total_failures += data.failures
        if data.avg_response_time > 0:
            avg_response_times.append(data.avg_response_time)
        
        status_lines.append(
            f"🔗 {url[:45]}...\n"
            f"   ✅ Checks: {data.check_count} | Failures: {data.failures}\n"
            f"   ⚡ Avg time: {data.avg_response_time:.2f}s\n"
            f"   🕐 Last: {time.time() - data.last_checked:.0f}s ago"
        )
        
        if data.last_error:
            status_lines.append(f"   ❌ Error: {data.last_error[:40]}...")
        
        status_lines.append("")
    
    # Summary statistics
    overall_avg = sum(avg_response_times) / len(avg_response_times) if avg_response_times else 0
    status_lines.append(f"📈 Total checks: {total_checks} | Total failures: {total_failures}")
    status_lines.append(f"📈 Overall avg response: {overall_avg:.2f}s")
    status_lines.append(f"🔄 Monitoring: {'✅ Active' if is_monitoring else '❌ Stopped'}")
    status_lines.append(f"⚙️ Reliable mode: {PAGE_LOAD_TIMEOUT}s timeout")
    
    message = "\n".join(status_lines)[:4000]
    await update.message.reply_text(message)

async def debug_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to see what content is being monitored for a URL"""
    if not context.args or not context.args[0]:
        await update.message.reply_text("❌ Usage: /debug <number>\nUse /list to see URL numbers")
        return
    
    try:
        url_index = int(context.args[0]) - 1
        url_list = list(monitored_urls.keys())
        
        if url_index < 0 or url_index >= len(url_list):
            await update.message.reply_text(f"❌ Invalid number. Use a number between 1 and {len(url_list)}")
            return
        
        url = url_list[url_index]
        processing_msg = await update.message.reply_text(
            f"🔍 Debugging content for: {url}\n"
            f"⏳ This will use reliable mode with generous timeouts..."
        )
        
        # Get content in debug mode
        loop = asyncio.get_event_loop()
        hash_result, response_time, error, content_sample = await loop.run_in_executor(
            None, get_content_hash_fast, url, True  # Debug mode ON
        )
        
        if hash_result:
            current_data = monitored_urls[url]
            debug_info = [
                f"🔍 Debug Info for URL #{url_index + 1}:",
                f"📄 Current hash: {current_data.hash[:16]}...",
                f"📄 New hash: {hash_result[:16]}...",
                f"🔄 Hashes match: {'✅ Yes' if current_data.hash == hash_result else '❌ No - CHANGE DETECTED!'}",
                f"⚡ Response time: {response_time:.2f}s",
                f"📊 Check count: {current_data.check_count}",
                f"❌ Failures: {current_data.failures}",
                f"🕐 Last checked: {time.time() - current_data.last_checked:.0f}s ago",
                "",
                "📝 Content sample (first 500 chars):",
                f"```{content_sample[:500] if content_sample else 'No sample available'}```"
            ]
            
            debug_message = "\n".join(debug_info)
            await processing_msg.edit_text(debug_message[:4000])
        else:
            await processing_msg.edit_text(f"❌ Failed to get content: {error}")
            
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")
    except Exception as e:
        print(f"❌ Debug error: {str(e)}")
        print(f"❌ Full traceback: {traceback.format_exc()}")
        await update.message.reply_text(f"❌ Debug error: {str(e)}")

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
        monitor_task = asyncio.create_task(start_monitoring(context.application.bot))
        context.chat_data['monitor_task'] = monitor_task
        
        await update.message.reply_text(
            f"✅ Monitoring started in RELIABLE mode!\n"
            f"🔍 Checking {len(monitored_urls)} URLs every {CHECK_INTERVAL}s\n"
            f"⚙️ Page timeout: {PAGE_LOAD_TIMEOUT}s\n"
            f"⚙️ Element timeout: {ELEMENT_WAIT_TIMEOUT}s\n"
            f"💾 Sequential processing for stability"
        )
        print("✅ Monitoring tasks created and started in reliable mode")
        
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(f"❌ Failed to start monitoring: {str(e)}")
        print(f"❌ Error starting monitoring: {str(e)}")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    
    # Cancel monitoring task
    if 'monitor_task' in context.chat_data:
        try:
            context.chat_data['monitor_task'].cancel()
            del context.chat_data['monitor_task']
            print("🛑 Monitor task cancelled")
        except Exception as e:
            print(f"⚠️ Error cancelling monitor task: {str(e)}")
    
    await update.message.reply_text("🛑 Monitoring stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    count = len(monitored_urls)
    monitored_urls.clear()
    await update.message.reply_text(f"✅ All {count} URLs purged!")

async def start_monitoring(bot):
    """Main monitoring loop with detailed logging"""
    global is_monitoring
    await send_notification(bot, "🔔 Monitoring started in RELIABLE mode!")
    print("🔍 Entering monitoring loop with generous timeouts")
    
    cycle_count = 0
    
    while is_monitoring:
        try:
            cycle_count += 1
            print(f"\n🔄 Starting monitoring cycle #{cycle_count}")
            print(f"🔄 Checking {len(monitored_urls)} URLs with reliable settings")
            start_time = time.time()
            
            await check_urls_sequential(bot)
            
            elapsed = time.time() - start_time
            wait_time = max(CHECK_INTERVAL - elapsed, 5)  # Minimum 5 second wait
            print(f"✓ Cycle #{cycle_count} complete in {elapsed:.2f}s, waiting {wait_time:.2f}s")
            
            await asyncio.sleep(wait_time)
            
        except asyncio.CancelledError:
            print("🚫 Monitoring task was cancelled")
            break
        except Exception as e:
            print(f"🚨 Monitoring error in cycle #{cycle_count}: {str(e)}")
            print(f"🚨 Full traceback: {traceback.format_exc()}")
            await send_notification(bot, f"⚠️ Monitoring error: {str(e)[:100]}...")
            print("⏳ Waiting 30 seconds before retry...")
            await asyncio.sleep(30)
    
    print("👋 Exiting monitoring loop")
    await send_notification(bot, "🔴 Monitoring stopped!")

def main():
    """Main function with comprehensive setup"""
    try:
        global CHROME_PATH, CHROMEDRIVER_PATH
        
        print(f"🚀 Starting bot at {datetime.now()}")
        print(f"🌍 Operating System: {platform.system()}")
        print(f"🌍 Running on Render: {IS_RENDER}")
        print(f"💾 Chrome path: {CHROME_PATH}")
        print(f"💾 Chromedriver path: {CHROMEDRIVER_PATH}")
        print(f"⚙️ RELIABLE MODE - Generous timeouts enabled")
        print(f"⚙️ Page load timeout: {PAGE_LOAD_TIMEOUT}s")
        print(f"⚙️ Element wait timeout: {ELEMENT_WAIT_TIMEOUT}s")
        print(f"⚙️ React wait time: {REACT_WAIT_TIME}s")
        print(f"⚙️ Max retries: {MAX_RETRIES}")
        print(f"⚙️ Failure threshold: {FAILURE_THRESHOLD}")
        
        # Kill previous instances
        kill_previous_instances()
        
        if not IS_RENDER:
            print(f"📂 Chrome exists: {os.path.exists(CHROME_PATH)}")
            print(f"📂 Chromedriver exists: {os.path.exists(CHROMEDRIVER_PATH)}")
            
            # Try to find Chrome if not at expected path
            if not os.path.exists(CHROME_PATH) and platform.system() == "Windows":
                chrome_possible_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ]
                for path in chrome_possible_paths:
                    if os.path.exists(path):
                        print(f"✅ Found Chrome at: {path}")
                        CHROME_PATH = path
                        break
            
            # Try to find ChromeDriver if not at expected path
            if not os.path.exists(CHROMEDRIVER_PATH):
                chromedriver_in_path = shutil.which('chromedriver')
                if chromedriver_in_path:
                    print(f"✅ Found Chromedriver in PATH: {chromedriver_in_path}")
                    CHROMEDRIVER_PATH = chromedriver_in_path
        
        # Set event loop policy for Windows
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        print("🔧 Creating Telegram application...")
        print(f"🤖 Bot token (first 10 chars): {TELEGRAM_BOT_TOKEN[:10]}...")
        print(f"💬 Target chat ID: {CHAT_ID}")
        
        # Create Telegram application
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .concurrent_updates(True)
            .read_timeout(30)
            .write_timeout(30)
            .connect_timeout(30)
            .pool_timeout(30)
            .build()
        )
        
        print("✅ Telegram application created successfully")
        
        # Add auth middleware first
        application.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
        
        # Add command handlers
        handlers = [
            CommandHandler("start", start),
            CommandHandler("add", add_url),
            CommandHandler("remove", remove_url),
            CommandHandler("list", list_urls),
            CommandHandler("run", run_monitoring),
            CommandHandler("stop", stop_monitoring),
            CommandHandler("purge", purge_urls),
            CommandHandler("status", status),
            CommandHandler("debug", debug_url)
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        print("✅ All handlers added")

        print("🚀 Starting polling with reliable settings...")
        print(f"📡 Bot will respond to chat ID: {CHAT_ID}")
        print("✅ Bot is ready! Send /start to test.")
        print("⚙️ RELIABLE MODE: Taking time to ensure quality results!")
        
        # Start polling with proper cleanup and generous timeouts
        application.run_polling(
            drop_pending_updates=True,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Graceful shutdown requested")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {str(e)}")
        print(f"❌ Full traceback: {traceback.format_exc()}")
        if not IS_RENDER:
            input("Press Enter to exit...")
    finally:
        print("🧹 Cleanup complete")

if __name__ == "__main__":
    print("🚀 Starting Zealy monitoring bot in RELIABLE mode...")
    try:
        main()
    except Exception as e:
        print(f"❌ CRITICAL ERROR in __main__: {str(e)}")
        print(f"❌ Full traceback: {traceback.format_exc()}")
        if not IS_RENDER:
            input("Press Enter to exit...")
    finally:
        print("👋 Bot shutdown complete")