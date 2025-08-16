import hashlib
import asyncio
import re
import shutil
import time
import os
import traceback
import sys
import gc
import json
from datetime import datetime, timedelta
import platform
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple, List, Set
import threading
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

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
    from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
except ImportError as e:
    print(f"ERROR: Missing required package: {str(e)}")
    print("Please install required packages using:")
    print("pip install python-telegram-bot selenium python-dotenv psutil chromedriver-autoinstaller")
    sys.exit(1)

# DEFINE IS_RENDER FIRST
IS_RENDER = os.getenv('IS_RENDER', 'false').lower() == 'true'

print(f"🚀 Starting Zealy Bot - TURBO 2GB VERSION")
print(f"📍 Working directory: {os.getcwd()}")
print(f"🐍 Python version: {sys.version}")
print(f"⚡ Performance Mode: ENABLED")

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
    sys.exit(1)

# Parse CHAT_ID
try:
    CHAT_ID = int(CHAT_ID_STR)
    print(f"✅ Chat ID parsed: {CHAT_ID}")
except ValueError:
    print(f"❌ CHAT_ID must be a number, got: '{CHAT_ID_STR}'")
    sys.exit(1)

# Chrome setup
print("🔧 Setting up Chrome...")
try:
    if not IS_RENDER:
        chromedriver_autoinstaller.install()
        print("✅ ChromeDriver installed")
except Exception as e:
    print(f"⚠️ ChromeDriver auto-install warning: {e}")

# TURBO Configuration for 2GB RAM
CHECK_INTERVAL = 10  # Check every 10 seconds
MAX_URLS = 50  # Support up to 50 URLs
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 20  # 20 second timeout
MAX_RETRIES = 2  # 2 retries max
RETRY_DELAY_BASE = 2  # 2 second base delay
FAILURE_THRESHOLD = 3  # Remove after 3 failures
PAGE_LOAD_TIMEOUT = 30  # 30 seconds max page load
ELEMENT_WAIT_TIMEOUT = 10  # 10 seconds element wait
REACT_WAIT_TIME = 3  # 3 seconds for React

# Performance Configuration
MAX_PARALLEL_CHECKS = 5  # Check 5 URLs simultaneously
MAX_DRIVER_POOL_SIZE = 3  # Keep 3 drivers in pool
DRIVER_REUSE_COUNT = 10  # Reuse each driver 10 times
BATCH_SIZE = 10  # Process in batches of 10
USE_DRIVER_POOL = True  # Enable driver pooling

# Memory Management Configuration - 2GB optimized
MEMORY_LIMIT_MB = 1800  # Alert at 1.8GB
MEMORY_WARNING_MB = 1500  # Warning at 1.5GB
MEMORY_CRITICAL_MB = 1700  # Critical at 1.7GB
MEMORY_CHECK_INTERVAL = 30  # Check every 30 seconds
STATE_FILE = "bot_state.json"

# Cache Configuration
CACHE_SIZE = 100  # LRU cache size
CONTENT_CACHE_TTL = 60  # Cache for 60 seconds

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

# Global driver pool
driver_pool = []
driver_pool_lock = threading.Lock()
driver_usage_count = {}

# Content cache
content_cache = {}
cache_lock = threading.Lock()

# Thread pool executor
executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL_CHECKS)

# Statistics tracking
stats = {
    'total_checks': 0,
    'total_changes': 0,
    'cache_hits': 0,
    'cache_misses': 0,
    'total_errors': 0,
    'start_time': time.time()
}

def format_time_ago(timestamp):
    """Format timestamp as time ago string"""
    if timestamp == 0:
        return "Never"
    
    seconds = int(time.time() - timestamp)
    if seconds < 60:
        return f"{seconds}s ago"
    elif seconds < 3600:
        return f"{seconds // 60}m ago"
    elif seconds < 86400:
        return f"{seconds // 3600}h ago"
    else:
        return f"{seconds // 86400}d ago"

def get_memory_usage():
    """Get current memory usage in MB"""
    try:
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        return memory_mb
    except Exception as e:
        print(f"⚠️ Error getting memory usage: {e}")
        return 0

def save_bot_state():
    """Save current bot state to file"""
    try:
        state = {
            "monitored_urls": {},
            "is_monitoring": is_monitoring,
            "timestamp": time.time(),
            "auto_restart": is_monitoring,
            "stats": stats
        }
        
        for url, url_data in monitored_urls.items():
            state["monitored_urls"][url] = asdict(url_data)
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        print(f"💾 State saved - {len(monitored_urls)} URLs")
        return True
    except Exception as e:
        print(f"❌ Error saving state: {e}")
        return False

def load_bot_state():
    """Load bot state from file"""
    global monitored_urls, is_monitoring, stats
    try:
        if not os.path.exists(STATE_FILE):
            print("📁 No previous state found")
            return False
        
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        monitored_urls.clear()
        for url, url_data_dict in state.get("monitored_urls", {}).items():
            monitored_urls[url] = URLData(**url_data_dict)
        
        # Load stats
        if 'stats' in state:
            stats.update(state['stats'])
        
        should_auto_restart = state.get("auto_restart", False)
        is_monitoring = False
        
        print(f"📁 Restored {len(monitored_urls)} URLs")
        return should_auto_restart
    except Exception as e:
        print(f"❌ Error loading state: {e}")
        return False

def cleanup_memory():
    """Force garbage collection and cleanup"""
    try:
        collected = gc.collect()
        print(f"🗑️ Garbage collected: {collected} objects")
        
        memory_mb = get_memory_usage()
        if memory_mb > MEMORY_WARNING_MB:
            with cache_lock:
                content_cache.clear()
            print("🧹 Cleared content cache")
        
        return get_memory_usage()
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        return get_memory_usage()

def get_chrome_options():
    """Get optimized Chrome options for SPEED"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    # Speed optimizations
    options.add_argument("--disable-images")
    options.add_argument("--disable-javascript-harmony-shipping")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-site-isolation-trials")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Performance flags
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-features=TranslateUI")
    options.add_argument("--disable-ipc-flooding-protection")
    
    # Page load strategy
    options.page_load_strategy = 'eager'
    
    # Prefs for speed
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,
            "plugins": 2,
            "popups": 2,
            "geolocation": 2,
            "notifications": 2,
            "media_stream": 2,
        }
    }
    options.add_experimental_option("prefs", prefs)
    
    if IS_RENDER:
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--single-process")
        options.add_argument("--no-zygote")
        options.add_argument("--js-flags=--max-old-space-size=1024")
    else:
        options.add_argument("--js-flags=--max-old-space-size=1024")
    
    if os.path.exists(CHROME_PATH):
        options.binary_location = CHROME_PATH
    
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
    total_changes: int = 0
    added_time: float = 0
    
    def update_response_time(self, response_time: float):
        if self.avg_response_time == 0:
            self.avg_response_time = response_time
        else:
            self.avg_response_time = 0.7 * self.avg_response_time + 0.3 * response_time

# Global variables
monitored_urls: Dict[str, URLData] = {}
is_monitoring = False
notification_queue = asyncio.Queue()

def create_driver():
    """Create an optimized Chrome driver"""
    try:
        options = get_chrome_options()
        
        if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
            driver = webdriver.Chrome(options=options)
        else:
            service = Service(executable_path=CHROMEDRIVER_PATH)
            driver = webdriver.Chrome(service=service, options=options)
        
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.implicitly_wait(3)
        
        # Block resources for speed
        driver.execute_cdp_cmd('Network.setBlockedURLs', {"urls": ["*.jpg", "*.png", "*.gif", "*.css"]})
        driver.execute_cdp_cmd('Network.enable', {})
        
        return driver
    except Exception as e:
        print(f"❌ Failed to create driver: {e}")
        return None

def get_driver_from_pool():
    """Get a driver from pool or create new"""
    global driver_pool, driver_usage_count
    
    if not USE_DRIVER_POOL:
        return create_driver(), False
    
    with driver_pool_lock:
        while driver_pool:
            driver = driver_pool.pop(0)
            driver_id = id(driver)
            
            try:
                usage = driver_usage_count.get(driver_id, 0)
                if usage < DRIVER_REUSE_COUNT:
                    driver.execute_script("return 1")
                    driver_usage_count[driver_id] = usage + 1
                    return driver, True
                else:
                    driver.quit()
                    del driver_usage_count[driver_id]
            except:
                try:
                    driver.quit()
                except:
                    pass
                if driver_id in driver_usage_count:
                    del driver_usage_count[driver_id]
        
        driver = create_driver()
        if driver:
            driver_usage_count[id(driver)] = 1
        return driver, False

def return_driver_to_pool(driver):
    """Return driver to pool for reuse"""
    if not USE_DRIVER_POOL or not driver:
        if driver:
            try:
                driver.quit()
            except:
                pass
        return
    
    with driver_pool_lock:
        driver_id = id(driver)
        usage = driver_usage_count.get(driver_id, 0)
        
        if usage < DRIVER_REUSE_COUNT and len(driver_pool) < MAX_DRIVER_POOL_SIZE:
            try:
                driver.delete_all_cookies()
                driver.execute_script("window.localStorage.clear();")
                driver.execute_script("window.sessionStorage.clear();")
                driver_pool.append(driver)
            except:
                try:
                    driver.quit()
                except:
                    pass
                if driver_id in driver_usage_count:
                    del driver_usage_count[driver_id]
        else:
            try:
                driver.quit()
            except:
                pass
            if driver_id in driver_usage_count:
                del driver_usage_count[driver_id]

def get_cached_content(url: str) -> Optional[Tuple[str, float]]:
    """Get cached content if available"""
    with cache_lock:
        if url in content_cache:
            hash_val, timestamp = content_cache[url]
            if time.time() - timestamp < CONTENT_CACHE_TTL:
                stats['cache_hits'] += 1
                return hash_val, timestamp
        stats['cache_misses'] += 1
        return None

def set_cached_content(url: str, hash_val: str):
    """Cache content hash"""
    with cache_lock:
        content_cache[url] = (hash_val, time.time())
        
        if len(content_cache) > CACHE_SIZE:
            sorted_items = sorted(content_cache.items(), key=lambda x: x[1][1])
            for old_url, _ in sorted_items[:len(content_cache) - CACHE_SIZE]:
                del content_cache[old_url]

def get_content_hash_optimized(url: str, use_cache: bool = True) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
    """Optimized content hash retrieval"""
    start_time = time.time()
    
    if use_cache:
        cached = get_cached_content(url)
        if cached:
            hash_val, _ = cached
            return hash_val, 0.1, None, None
    
    driver = None
    from_pool = False
    
    try:
        driver, from_pool = get_driver_from_pool()
        if not driver:
            return None, time.time() - start_time, "Failed to create driver", None
        
        driver.get(url)
        
        content = None
        strategies = [
            (By.CSS_SELECTOR, ZEALY_CONTAINER_SELECTOR, 5),
            (By.CSS_SELECTOR, "div[class*='flex']", 3),
            (By.TAG_NAME, "main", 2),
            (By.TAG_NAME, "body", 1)
        ]
        
        for by, selector, wait_time in strategies:
            try:
                element = WebDriverWait(driver, wait_time).until(
                    EC.presence_of_element_located((by, selector))
                )
                content = element.text
                if content and len(content.strip()) > 10:
                    break
            except TimeoutException:
                continue
        
        if not content or len(content.strip()) < 10:
            return None, time.time() - start_time, "No content found", None
        
        clean_content = re.sub(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', 
            '', 
            content
        )
        
        content_hash = hashlib.sha256(clean_content.strip().encode()).hexdigest()
        response_time = time.time() - start_time
        
        if use_cache:
            set_cached_content(url, content_hash)
        
        stats['total_checks'] += 1
        
        return content_hash, response_time, None, None
        
    except Exception as e:
        stats['total_errors'] += 1
        return None, time.time() - start_time, str(e), None
    finally:
        if driver:
            return_driver_to_pool(driver)

async def check_urls_parallel(bot):
    """Check URLs in parallel for maximum speed"""
    global monitored_urls
    
    if not monitored_urls:
        return
    
    current_time = time.time()
    urls_to_check = list(monitored_urls.items())
    total_urls = len(urls_to_check)
    
    print(f"🚀 Checking {total_urls} URLs in parallel")
    
    changes_detected = []
    urls_to_remove = []
    
    for batch_start in range(0, total_urls, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total_urls)
        batch = urls_to_check[batch_start:batch_end]
        
        print(f"📦 Batch {batch_start//BATCH_SIZE + 1}/{(total_urls + BATCH_SIZE - 1)//BATCH_SIZE}")
        
        memory_mb = get_memory_usage()
        if memory_mb > MEMORY_CRITICAL_MB:
            print(f"⚠️ Memory critical: {memory_mb:.1f}MB")
            cleanup_memory()
            await asyncio.sleep(2)
        
        loop = asyncio.get_event_loop()
        futures = []
        
        for url, url_data in batch:
            future = loop.run_in_executor(
                executor,
                get_content_hash_optimized,
                url,
                True
            )
            futures.append((url, url_data, future))
        
        for url, url_data, future in futures:
            try:
                hash_result, response_time, error, _ = await future
                
                if hash_result is None:
                    url_data.failures += 1
                    url_data.consecutive_successes = 0
                    url_data.last_error = error
                    
                    if url_data.failures > FAILURE_THRESHOLD:
                        urls_to_remove.append(url)
                        print(f"❌ {url} - {url_data.failures} failures")
                else:
                    url_data.failures = 0
                    url_data.consecutive_successes += 1
                    url_data.last_error = None
                    url_data.check_count += 1
                    url_data.update_response_time(response_time)
                    url_data.last_checked = current_time
                    
                    if url_data.hash != hash_result:
                        url_data.total_changes += 1
                        stats['total_changes'] += 1
                        old_hash = url_data.hash[:8]
                        url_data.hash = hash_result
                        
                        if current_time - url_data.last_notified > 60:
                            changes_detected.append({
                                'url': url,
                                'old_hash': old_hash,
                                'new_hash': hash_result[:8],
                                'response_time': response_time,
                                'check_count': url_data.check_count,
                                'total_changes': url_data.total_changes
                            })
                            url_data.last_notified = current_time
                        
                        print(f"🔔 Change: {url}")
                    else:
                        print(f"✓ No change: {url} ({response_time:.2f}s)")
                        
            except Exception as e:
                print(f"❌ Error: {url}: {e}")
                url_data.failures += 1
        
        if batch_end < total_urls:
            await asyncio.sleep(0.5)
    
    # Send detailed notifications for changes
    if changes_detected:
        for change in changes_detected:
            notification = (
                f"🚨 **CHANGE DETECTED!**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 **URL:** {change['url']}\n"
                f"🔄 **Hash Changed:**\n"
                f"  Old: `{change['old_hash']}...`\n"
                f"  New: `{change['new_hash']}...`\n"
                f"⚡ **Response Time:** {change['response_time']:.2f}s\n"
                f"📊 **Statistics:**\n"
                f"  • Check #{change['check_count']}\n"
                f"  • Total changes: {change['total_changes']}\n"
                f"🕐 **Time:** {datetime.now().strftime('%H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            await notification_queue.put((notification, True))
    
    # Remove failed URLs
    for url in urls_to_remove:
        url_data = monitored_urls[url]
        del monitored_urls[url]
        notification = (
            f"🔴 **URL REMOVED**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 **URL:** {url}\n"
            f"❌ **Reason:** Too many failures ({url_data.failures})\n"
            f"⚠️ **Last Error:** {url_data.last_error or 'Unknown'}\n"
            f"📊 **Stats before removal:**\n"
            f"  • Total checks: {url_data.check_count}\n"
            f"  • Avg response: {url_data.avg_response_time:.2f}s\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await notification_queue.put((notification, False))
    
    print(f"✅ Complete: {len(changes_detected)} changes, {len(urls_to_remove)} removed")
    save_bot_state()

async def notification_sender(bot):
    """Send notifications from queue"""
    while True:
        try:
            message, priority = await notification_queue.get()
            
            retries = 2 if priority else 1
            for attempt in range(retries):
                try:
                    await bot.send_message(
                        chat_id=CHAT_ID, 
                        text=message,
                        parse_mode='Markdown'
                    )
                    break
                except Exception as e:
                    if attempt == retries - 1:
                        print(f"❌ Failed to send: {e}")
                    else:
                        await asyncio.sleep(1)
                        
        except Exception as e:
            print(f"❌ Notification error: {e}")
            await asyncio.sleep(1)

async def memory_monitor():
    """Monitor memory usage"""
    while True:
        try:
            memory_mb = get_memory_usage()
            
            if memory_mb > MEMORY_LIMIT_MB:
                print(f"🚨 MEMORY ALERT: {memory_mb:.1f}MB")
                save_bot_state()
                
                with driver_pool_lock:
                    for driver in driver_pool:
                        try:
                            driver.quit()
                        except:
                            pass
                    driver_pool.clear()
                    driver_usage_count.clear()
                
                cleanup_memory()
                
            elif memory_mb > MEMORY_CRITICAL_MB:
                print(f"🔴 CRITICAL: {memory_mb:.1f}MB")
                cleanup_memory()
                await asyncio.sleep(5)
                continue
                
            elif memory_mb > MEMORY_WARNING_MB:
                print(f"🟡 WARNING: {memory_mb:.1f}MB")
                gc.collect()
                
            await asyncio.sleep(MEMORY_CHECK_INTERVAL)
            
        except Exception as e:
            print(f"❌ Memory monitor error: {e}")
            await asyncio.sleep(10)

# AUTH MIDDLEWARE
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if user_id != CHAT_ID:
        print(f"🚫 Unauthorized: {user_id}")
        await update.message.reply_text(f"🚫 Unauthorized! Your ID: {user_id}")
        raise ApplicationHandlerStop

# COMMAND HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_mb = get_memory_usage()
    uptime = int(time.time() - stats['start_time'])
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    
    welcome_msg = (
        "🚀 **ZEALY TURBO BOT v2.0**\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ **PERFORMANCE MODE ACTIVE**\n"
        f"• Parallel Checks: {MAX_PARALLEL_CHECKS} URLs\n"
        f"• Check Interval: {CHECK_INTERVAL}s\n"
        f"• Max URLs: {MAX_URLS}\n"
        f"• Driver Pool: {MAX_DRIVER_POOL_SIZE} drivers\n"
        f"• Cache Size: {CACHE_SIZE} entries\n\n"
        "📋 **COMMANDS:**\n"
        "`/add <url>` - Add Zealy URL\n"
        "`/remove <num>` - Remove URL\n"
        "`/list` - Show all URLs\n"
        "`/run` - Start monitoring\n"
        "`/stop` - Stop monitoring\n"
        "`/status` - Detailed statistics\n"
        "`/clear` - Clear cache & pools\n"
        "`/memory` - Memory usage\n"
        "`/help` - Show this message\n\n"
        f"💾 **SYSTEM STATUS:**\n"
        f"• Memory: {memory_mb:.1f}/{MEMORY_LIMIT_MB}MB\n"
        f"• Uptime: {hours}h {minutes}m\n"
        f"• Total Checks: {stats['total_checks']}\n"
        f"• Total Changes: {stats['total_changes']}\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(
            f"❌ **Maximum Capacity Reached**\n"
            f"Currently monitoring {MAX_URLS} URLs (limit)",
            parse_mode='Markdown'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Invalid Usage**\n\n"
            "**Correct format:**\n"
            "`/add https://zealy.io/cw/projectname`\n\n"
            "**Example:**\n"
            "`/add https://zealy.io/cw/myproject`",
            parse_mode='Markdown'
        )
        return
    
    url = context.args[0].lower()
    
    if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+', url):
        await update.message.reply_text(
            "❌ **Invalid Zealy URL Format**\n\n"
            "URL must be a valid Zealy community URL.\n"
            "**Format:** `https://zealy.io/cw/name`",
            parse_mode='Markdown'
        )
        return
    
    if url in monitored_urls:
        url_data = monitored_urls[url]
        await update.message.reply_text(
            f"ℹ️ **Already Monitoring**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 URL: {url}\n"
            f"📊 Stats:\n"
            f"  • Checks: {url_data.check_count}\n"
            f"  • Changes: {url_data.total_changes}\n"
            f"  • Avg Response: {url_data.avg_response_time:.2f}s\n"
            f"  • Last Check: {format_time_ago(url_data.last_checked)}",
            parse_mode='Markdown'
        )
        return
    
    msg = await update.message.reply_text(
        "⏳ **Verifying URL...**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Checking: {url}\n"
        f"⚡ Using Turbo Mode\n"
        f"Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        loop = asyncio.get_event_loop()
        hash_result, response_time, error, _ = await loop.run_in_executor(
            executor,
            get_content_hash_optimized,
            url,
            False
        )
        
        if not hash_result:
            await msg.edit_text(
                f"❌ **Failed to Add URL**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 URL: {url}\n"
                f"⚠️ Error: {error}\n\n"
                f"**Possible issues:**\n"
                f"• URL might be invalid\n"
                f"• Page might be private\n"
                f"• Network timeout",
                parse_mode='Markdown'
            )
            return
        
        monitored_urls[url] = URLData(
            hash=hash_result,
            last_notified=0,
            last_checked=time.time(),
            failures=0,
            consecutive_successes=1,
            check_count=1,
            avg_response_time=response_time,
            total_changes=0,
            added_time=time.time()
        )
        
        save_bot_state()
        
        await msg.edit_text(
            f"✅ **Successfully Added!**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 **URL:** {url}\n"
            f"⚡ **Initial Load:** {response_time:.2f}s\n"
            f"🔢 **Hash:** `{hash_result[:12]}...`\n"
            f"📊 **Status:**\n"
            f"  • Slot: {len(monitored_urls)}/{MAX_URLS}\n"
            f"  • Memory: {get_memory_usage():.1f}MB\n"
            f"  • Ready for monitoring\n\n"
            f"💡 Use `/run` to start monitoring",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        await msg.edit_text(
            f"❌ **Error Adding URL**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Error: {str(e)[:100]}",
            parse_mode='Markdown'
        )

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text(
            "📋 **No URLs Monitored**\n\n"
            "Use `/add <url>` to add URLs",
            parse_mode='Markdown'
        )
        return
    
    lines = [
        "📋 **MONITORED URLS**",
        "━━━━━━━━━━━━━━━━━━",
        ""
    ]
    
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        status_emoji = "🟢" if data.failures == 0 else "🟡" if data.failures < FAILURE_THRESHOLD else "🔴"
        url_short = url.replace("https://zealy.io/cw/", "")
        
        lines.append(f"**{idx}.** {status_emoji} **{url_short}**")
        lines.append(f"   ⚡ {data.avg_response_time:.1f}s | 📊 {data.check_count} checks")
        
        if data.total_changes > 0:
            lines.append(f"   🔄 {data.total_changes} changes detected")
        
        if data.failures > 0:
            lines.append(f"   ⚠️ {data.failures} failures")
        
        lines.append("")
    
    lines.extend([
        "━━━━━━━━━━━━━━━━━━",
        f"**📊 SUMMARY**",
        f"• Total URLs: {len(monitored_urls)}/{MAX_URLS}",
        f"• Memory: {get_memory_usage():.1f}MB",
        f"• Status: {'🟢 Monitoring' if is_monitoring else '⭕ Stopped'}",
        "",
        "💡 **Tips:**",
        "• `/remove <num>` to remove",
        "• `/status` for detailed stats",
        "• `/run` to start monitoring"
    ])
    
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode='Markdown')

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text(
            "❌ **No URLs to Remove**\n\n"
            "Add URLs first with `/add <url>`",
            parse_mode='Markdown'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Invalid Usage**\n\n"
            "**Correct format:**\n"
            "`/remove <number>`\n\n"
            "Use `/list` to see URL numbers",
            parse_mode='Markdown'
        )
        return
    
    try:
        idx = int(context.args[0]) - 1
        urls = list(monitored_urls.keys())
        
        if idx < 0 or idx >= len(urls):
            await update.message.reply_text(
                f"❌ **Invalid Number**\n\n"
                f"Please use a number between 1 and {len(urls)}",
                parse_mode='Markdown'
            )
            return
        
        url = urls[idx]
        url_data = monitored_urls[url]
        
        # Store stats before deletion
        stats_text = (
            f"📊 **Final Statistics:**\n"
            f"  • Total Checks: {url_data.check_count}\n"
            f"  • Total Changes: {url_data.total_changes}\n"
            f"  • Avg Response: {url_data.avg_response_time:.2f}s\n"
            f"  • Added: {format_time_ago(url_data.added_time)}"
        )
        
        del monitored_urls[url]
        
        # Clear from cache
        with cache_lock:
            if url in content_cache:
                del content_cache[url]
        
        save_bot_state()
        
        await update.message.reply_text(
            f"✅ **URL Removed**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 **URL:** {url}\n\n"
            f"{stats_text}\n\n"
            f"📋 Remaining: {len(monitored_urls)}/{MAX_URLS} URLs",
            parse_mode='Markdown'
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ **Invalid Number**\n\n"
            "Please provide a valid number",
            parse_mode='Markdown'
        )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text(
            "📊 **No URLs Being Monitored**\n\n"
            "Add URLs with `/add <url>`",
            parse_mode='Markdown'
        )
        return
    
    # Calculate statistics
    total_checks = sum(d.check_count for d in monitored_urls.values())
    total_failures = sum(d.failures for d in monitored_urls.values())
    total_changes = sum(d.total_changes for d in monitored_urls.values())
    avg_times = [d.avg_response_time for d in monitored_urls.values() if d.avg_response_time > 0]
    overall_avg = sum(avg_times) / len(avg_times) if avg_times else 0
    
    # Performance metrics
    with driver_pool_lock:
        pool_size = len(driver_pool)
        pool_usage = sum(driver_usage_count.values())
    
    with cache_lock:
        cache_size = len(content_cache)
    
    # Calculate cache hit rate
    total_cache_ops = stats['cache_hits'] + stats['cache_misses']
    cache_hit_rate = (stats['cache_hits'] / total_cache_ops * 100) if total_cache_ops > 0 else 0
    
    # System metrics
    memory_mb = get_memory_usage()
    memory_percent = (memory_mb / MEMORY_LIMIT_MB) * 100
    uptime = int(time.time() - stats['start_time'])
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    
    # Find best/worst performing URLs
    best_url = min(monitored_urls.items(), key=lambda x: x[1].avg_response_time) if monitored_urls else None
    worst_url = max(monitored_urls.items(), key=lambda x: x[1].avg_response_time) if monitored_urls else None
    most_changes = max(monitored_urls.items(), key=lambda x: x[1].total_changes) if monitored_urls else None
    
    status_text = (
        f"📊 **DETAILED STATUS REPORT**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"**📈 MONITORING STATS**\n"
        f"• Active URLs: {len(monitored_urls)}/{MAX_URLS}\n"
        f"• Total Checks: {total_checks}\n"
        f"• Total Changes: {total_changes}\n"
        f"• Total Failures: {total_failures}\n"
        f"• Avg Response: {overall_avg:.2f}s\n"
        f"• Status: {'🟢 Active' if is_monitoring else '🔴 Stopped'}\n\n"
        f"**⚡ PERFORMANCE METRICS**\n"
        f"• Driver Pool: {pool_size}/{MAX_DRIVER_POOL_SIZE} ready\n"
        f"• Pool Usage: {pool_usage} operations\n"
        f"• Cache Entries: {cache_size}/{CACHE_SIZE}\n"
        f"• Cache Hit Rate: {cache_hit_rate:.1f}%\n"
        f"• Parallel Workers: {MAX_PARALLEL_CHECKS}\n\n"
        f"**💾 SYSTEM RESOURCES**\n"
        f"• Memory: {memory_mb:.1f}MB / {MEMORY_LIMIT_MB}MB\n"
        f"• Memory Usage: {memory_percent:.1f}%\n"
        f"• Status: {'🟢 Healthy' if memory_percent < 60 else '🟡 Warning' if memory_percent < 80 else '🔴 Critical'}\n"
        f"• Uptime: {hours}h {minutes}m\n\n"
    )
    
    if best_url:
        status_text += (
            f"**🏆 TOP PERFORMERS**\n"
            f"• Fastest: {best_url[0].replace('https://zealy.io/cw/', '')[:20]}... ({best_url[1].avg_response_time:.1f}s)\n"
        )
    
    if worst_url and worst_url != best_url:
        status_text += f"• Slowest: {worst_url[0].replace('https://zealy.io/cw/', '')[:20]}... ({worst_url[1].avg_response_time:.1f}s)\n"
    
    if most_changes and most_changes[1].total_changes > 0:
        status_text += f"• Most Active: {most_changes[0].replace('https://zealy.io/cw/', '')[:20]}... ({most_changes[1].total_changes} changes)\n"
    
    status_text += "\n━━━━━━━━━━━━━━━━━━"
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all caches and pools"""
    with cache_lock:
        old_cache_size = len(content_cache)
        content_cache.clear()
    
    old_pool_size = 0
    with driver_pool_lock:
        old_pool_size = len(driver_pool)
        for driver in driver_pool:
            try:
                driver.quit()
            except:
                pass
        driver_pool.clear()
        driver_usage_count.clear()
    
    memory_before = get_memory_usage()
    gc.collect()
    cleanup_memory()
    memory_after = get_memory_usage()
    memory_freed = memory_before - memory_after
    
    await update.message.reply_text(
        f"🧹 **CACHE & POOLS CLEARED**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"**🗑️ Cleared:**\n"
        f"• Content Cache: {old_cache_size} entries\n"
        f"• Driver Pool: {old_pool_size} drivers\n"
        f"• Memory Freed: {memory_freed:.1f}MB\n\n"
        f"**💾 Memory Status:**\n"
        f"• Before: {memory_before:.1f}MB\n"
        f"• After: {memory_after:.1f}MB\n"
        f"• Available: {MEMORY_LIMIT_MB - memory_after:.1f}MB\n\n"
        f"✅ System optimized!",
        parse_mode='Markdown'
    )

async def memory_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory_mb = get_memory_usage()
    memory_percent = (memory_mb / MEMORY_LIMIT_MB) * 100
    
    # Get process info
    process = psutil.Process(os.getpid())
    cpu_percent = process.cpu_percent(interval=1)
    threads = process.num_threads()
    
    # Driver pool info
    with driver_pool_lock:
        pool_size = len(driver_pool)
        pool_memory = pool_size * 50
    
    # Cache info
    with cache_lock:
        cache_size = len(content_cache)
    
    # Memory health indicator
    if memory_percent < 50:
        health = "🟢 **Excellent**"
    elif memory_percent < 70:
        health = "🟡 **Good**"
    elif memory_percent < 85:
        health = "🟠 **Warning**"
    else:
        health = "🔴 **Critical**"
    
    await update.message.reply_text(
        f"💾 **MEMORY STATUS**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"**📊 CURRENT USAGE**\n"
        f"• RAM: {memory_mb:.1f}MB / {MEMORY_LIMIT_MB}MB\n"
        f"• Percentage: {memory_percent:.1f}%\n"
        f"• Health: {health}\n\n"
        f"**🖥️ PROCESS INFO**\n"
        f"• CPU Usage: {cpu_percent:.1f}%\n"
        f"• Active Threads: {threads}\n"
        f"• Driver Pool: {pool_size} ({pool_memory}MB est.)\n"
        f"• Cache Entries: {cache_size}\n\n"
        f"**⚠️ THRESHOLDS**\n"
        f"• Warning: {MEMORY_WARNING_MB}MB ({(MEMORY_WARNING_MB/MEMORY_LIMIT_MB*100):.0f}%)\n"
        f"• Critical: {MEMORY_CRITICAL_MB}MB ({(MEMORY_CRITICAL_MB/MEMORY_LIMIT_MB*100):.0f}%)\n"
        f"• Max Limit: {MEMORY_LIMIT_MB}MB (100%)\n\n"
        f"**💡 TIPS**\n"
        f"• Use `/clear` to free memory\n"
        f"• Bot auto-manages memory\n"
        f"• State saved automatically",
        parse_mode='Markdown'
    )

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    
    if is_monitoring:
        await update.message.reply_text(
            "⚠️ **Already Monitoring**\n\n"
            "Use `/stop` to stop first",
            parse_mode='Markdown'
        )
        return
    
    if not monitored_urls:
        await update.message.reply_text(
            "❌ **No URLs to Monitor**\n\n"
            "Add URLs first with `/add <url>`",
            parse_mode='Markdown'
        )
        return
    
    memory_mb = get_memory_usage()
    if memory_mb > MEMORY_CRITICAL_MB:
        await update.message.reply_text(
            f"⚠️ **Memory Too High**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Current: {memory_mb:.1f}MB\n"
            f"Critical: {MEMORY_CRITICAL_MB}MB\n\n"
            f"Use `/clear` to free memory first",
            parse_mode='Markdown'
        )
        return
    
    try:
        is_monitoring = True
        
        # Start background tasks
        memory_task = asyncio.create_task(memory_monitor())
        notification_task = asyncio.create_task(notification_sender(context.application.bot))
        monitor_task = asyncio.create_task(start_monitoring(context.application.bot))
        
        context.chat_data['tasks'] = {
            'memory': memory_task,
            'notification': notification_task,
            'monitor': monitor_task
        }
        
        await update.message.reply_text(
            f"🚀 **MONITORING STARTED**\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"**⚡ TURBO MODE ACTIVE**\n"
            f"• URLs: {len(monitored_urls)}\n"
            f"• Parallel Checks: {MAX_PARALLEL_CHECKS}\n"
            f"• Check Interval: Every {CHECK_INTERVAL}s\n"
            f"• Memory: {memory_mb:.1f}MB\n\n"
            f"**🔧 FEATURES ENABLED:**\n"
            f"✅ Driver Pooling ({MAX_DRIVER_POOL_SIZE}x)\n"
            f"✅ Content Caching ({CACHE_SIZE})\n"
            f"✅ Parallel Processing\n"
            f"✅ Auto Memory Management\n"
            f"✅ Smart Retries\n\n"
            f"📊 Use `/status` for live stats\n"
            f"🛑 Use `/stop` to stop monitoring",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(
            f"❌ **Failed to Start**\n\n"
            f"Error: {str(e)[:100]}",
            parse_mode='Markdown'
        )

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    
    if not is_monitoring:
        await update.message.reply_text(
            "⚠️ **Not Currently Monitoring**\n\n"
            "Use `/run` to start monitoring",
            parse_mode='Markdown'
        )
        return
    
    is_monitoring = False
    
    # Cancel all tasks
    cancelled_tasks = []
    if 'tasks' in context.chat_data:
        for name, task in context.chat_data['tasks'].items():
            try:
                task.cancel()
                cancelled_tasks.append(name)
            except:
                pass
        del context.chat_data['tasks']
    
    # Clear driver pool
    cleared_drivers = 0
    with driver_pool_lock:
        cleared_drivers = len(driver_pool)
        for driver in driver_pool:
            try:
                driver.quit()
            except:
                pass
        driver_pool.clear()
        driver_usage_count.clear()
    
    save_bot_state()
    memory_mb = get_memory_usage()
    
    await update.message.reply_text(
        f"🛑 **MONITORING STOPPED**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"**✅ Cleanup Complete:**\n"
        f"• Tasks Cancelled: {len(cancelled_tasks)}\n"
        f"• Drivers Closed: {cleared_drivers}\n"
        f"• State Saved: ✅\n"
        f"• Memory: {memory_mb:.1f}MB\n\n"
        f"📊 Final Stats:\n"
        f"• Total Checks: {stats['total_checks']}\n"
        f"• Total Changes: {stats['total_changes']}\n\n"
        f"Use `/run` to restart monitoring",
        parse_mode='Markdown'
    )

async def start_monitoring(bot):
    """Main monitoring loop"""
    global is_monitoring
    
    await notification_queue.put((
        f"🟢 **MONITORING ACTIVE**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Tracking {len(monitored_urls)} URLs\n"
        f"Turbo Mode: ON ⚡",
        True
    ))
    
    print("🚀 Starting turbo monitoring")
    cycle_count = 0
    
    while is_monitoring:
        try:
            cycle_count += 1
            memory_mb = get_memory_usage()
            
            print(f"\n🔄 Cycle #{cycle_count} | URLs: {len(monitored_urls)} | Memory: {memory_mb:.1f}MB")
            
            start_time = time.time()
            await check_urls_parallel(bot)
            elapsed = time.time() - start_time
            
            wait_time = max(CHECK_INTERVAL - elapsed, 1)
            print(f"✅ Cycle completed in {elapsed:.2f}s, waiting {wait_time:.2f}s")
            
            await asyncio.sleep(wait_time)
            
        except asyncio.CancelledError:
            print("🚫 Monitoring cancelled")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            await notification_queue.put((
                f"⚠️ **Monitoring Error**\n{str(e)[:100]}",
                False
            ))
            await asyncio.sleep(10)
    
    print("👋 Monitoring stopped")

async def auto_start_monitoring(application):
    """Auto-start after restart"""
    global is_monitoring
    
    if len(monitored_urls) > 0 and not is_monitoring:
        print(f"🔄 Auto-starting for {len(monitored_urls)} URLs")
        
        try:
            is_monitoring = True
            
            memory_task = asyncio.create_task(memory_monitor())
            notification_task = asyncio.create_task(notification_sender(application.bot))
            monitor_task = asyncio.create_task(start_monitoring(application.bot))
            
            await notification_queue.put((
                f"🔄 **AUTO-RESTART COMPLETE**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• Restored: {len(monitored_urls)} URLs\n"
                f"• Memory: {get_memory_usage():.1f}MB\n"
                f"• Mode: Turbo ⚡\n"
                f"• Status: Active 🟢",
                True
            ))
            
        except Exception as e:
            is_monitoring = False
            print(f"❌ Auto-start failed: {e}")

def cleanup_on_exit():
    """Cleanup on exit"""
    print("🧹 Cleaning up...")
    save_bot_state()
    
    with driver_pool_lock:
        for driver in driver_pool:
            try:
                driver.quit()
            except:
                pass
        driver_pool.clear()
    
    executor.shutdown(wait=False)
    print("✅ Cleanup complete")

def main():
    """Main function"""
    try:
        print(f"🚀 ZEALY TURBO BOT v2.0")
        print(f"📅 {datetime.now()}")
        print(f"💾 Memory: {MEMORY_LIMIT_MB}MB limit")
        print(f"⚡ Turbo Mode: ENABLED")
        
        should_auto_restart = load_bot_state()
        
        print(f"📊 Memory: {get_memory_usage():.1f}MB")
        print(f"📊 URLs: {len(monitored_urls)}")
        
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        print("🔧 Creating Telegram app...")
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .concurrent_updates(True)
            .read_timeout(20)
            .write_timeout(20)
            .connect_timeout(20)
            .pool_timeout(20)
            .build()
        )
        
        print("✅ App created")
        
        # Add handlers
        application.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
        
        handlers = [
            CommandHandler("start", start),
            CommandHandler("help", help_command),
            CommandHandler("add", add_url),
            CommandHandler("remove", remove_url),
            CommandHandler("list", list_urls),
            CommandHandler("run", run_monitoring),
            CommandHandler("stop", stop_monitoring),
            CommandHandler("status", status),
            CommandHandler("clear", clear_cache),
            CommandHandler("memory", memory_status)
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        print("✅ Handlers ready")
        
        if should_auto_restart:
            print("⏳ Auto-restart scheduled...")
            async def delayed_start():
                await asyncio.sleep(3)
                await auto_start_monitoring(application)
            asyncio.create_task(delayed_start())
        
        print("🚀 Bot starting...")
        print(f"📡 Chat ID: {CHAT_ID}")
        print("✅ Send /start to begin")
        
        application.run_polling(
            drop_pending_updates=True,
            read_timeout=20,
            write_timeout=20,
            connect_timeout=20,
            pool_timeout=20
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Shutdown")
        cleanup_on_exit()
    except Exception as e:
        print(f"❌ ERROR: {e}")
        print(traceback.format_exc())
        cleanup_on_exit()
    finally:
        print("👋 Goodbye!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Fatal: {e}")
        cleanup_on_exit()
        sys.exit(1)