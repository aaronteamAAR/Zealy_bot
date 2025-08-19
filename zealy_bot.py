#!/usr/bin/env python3
"""
Zealy Bot v2.0 - Fixed and Cleaned Version
Monitor Zealy.io URLs for changes
"""

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
import platform
import threading
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple, List, Set
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

# Third-party imports
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

# ============================================================================
# ENVIRONMENT CONFIGURATION
# ============================================================================

IS_RENDER = os.getenv('IS_RENDER', 'false').lower() == 'true'

print(f"🚀 Starting Zealy Bot v2.0 - FIXED VERSION")
print(f"📍 Working directory: {os.getcwd()}")
print(f"🐍 Python version: {sys.version}")
print(f"⚡ Performance Mode: OPTIMIZED")

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

# ============================================================================
# CONFIGURATION CONSTANTS
# ============================================================================

# Chrome setup
print("🔧 Setting up Chrome...")
try:
    if not IS_RENDER:
        chromedriver_autoinstaller.install()
        print("✅ ChromeDriver installed")
except Exception as e:
    print(f"⚠️ ChromeDriver auto-install warning: {e}")

# Configuration for speed with reliability
CHECK_INTERVAL = 30  # Check every 30 seconds
MAX_URLS = 50  # Support up to 50 URLs
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 30  # 30 second timeout
MAX_RETRIES = 2  # 2 retries max
RETRY_DELAY_BASE = 3  # 3 second base delay
FAILURE_THRESHOLD = 5  # Remove after 5 failures
PAGE_LOAD_TIMEOUT = 60  # 60 seconds max page load
ELEMENT_WAIT_TIMEOUT = 15  # 15 seconds element wait
REACT_WAIT_TIME = 4  # 4 seconds for React

# Performance Configuration
MAX_PARALLEL_CHECKS = 5  # Check 5 URLs simultaneously
MAX_DRIVER_POOL_SIZE = 5  # Keep 5 drivers in pool
DRIVER_REUSE_COUNT = 10  # Reuse each driver 10 times
BATCH_SIZE = 10  # Process in batches of 10
USE_DRIVER_POOL = True  # Enable driver pooling
USE_SEQUENTIAL_MODE = False  # Use parallel mode for speed

# Memory Management Configuration
MEMORY_LIMIT_MB = 1800  # Alert at 1.8GB
MEMORY_WARNING_MB = 1500  # Warning at 1.5GB
MEMORY_CRITICAL_MB = 1700  # Critical at 1.7GB
MEMORY_CHECK_INTERVAL = 30  # Check every 30 seconds
STATE_FILE = "bot_state.json"

# Cache Configuration
CACHE_SIZE = 100  # LRU cache size
CONTENT_CACHE_TTL = 60  # Cache for 60 seconds

# Chrome paths
if IS_RENDER:
    CHROME_PATH = '/usr/bin/chromium'
    CHROMEDRIVER_PATH = '/usr/bin/chromedriver'
elif platform.system() == "Windows":
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    CHROMEDRIVER_PATH = shutil.which('chromedriver') or r"C:\chromedriver\chromedriver.exe"
else:
    CHROME_PATH = '/usr/bin/google-chrome'
    CHROMEDRIVER_PATH = shutil.which('chromedriver') or '/usr/bin/chromedriver'

# ============================================================================
# GLOBAL VARIABLES
# ============================================================================

# Driver pool
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

# Monitoring state
monitored_urls: Dict[str, 'URLData'] = {}
is_monitoring = False
notification_queue = asyncio.Queue()

# ============================================================================
# DATA CLASSES
# ============================================================================

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

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

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
        
        # Kill hanging Chrome processes
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                if 'chrome' in proc.info['name'].lower() or 'chromium' in proc.info['name'].lower():
                    try:
                        proc.kill()
                        print(f"🔪 Killed hanging Chrome process: {proc.info['pid']}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except Exception as e:
            print(f"⚠️ Error cleaning Chrome processes: {e}")
        
        return get_memory_usage()
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        return get_memory_usage()

# ============================================================================
# CHROME DRIVER FUNCTIONS
# ============================================================================

def get_chrome_options():
    """Get optimized Chrome options"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    # Disable non-essential features (keep JavaScript enabled!)
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    
    # Memory optimization
    options.add_argument("--memory-pressure-off")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
    
    options.page_load_strategy = 'normal'
    
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
        driver.implicitly_wait(10)
        
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

# ============================================================================
# CONTENT PROCESSING FUNCTIONS
# ============================================================================

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

def clean_zealy_content(content: str) -> str:
    """Clean Zealy content to remove dynamic elements"""
    # Remove timestamps
    clean_content = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', '', content)
    
    # Remove XP values
    clean_content = re.sub(r'\d+\s*XP', '', clean_content)
    
    # Remove UUIDs
    clean_content = re.sub(r'\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', '', clean_content, flags=re.IGNORECASE)
    
    # Remove relative time
    clean_content = re.sub(r'\d+\s*(hours?|minutes?|seconds?|days?|weeks?|months?)\s*ago', '', clean_content, flags=re.IGNORECASE)
    
    # Remove time displays
    clean_content = re.sub(r'\d{1,2}:\d{2}\s*(AM|PM|am|pm)?', '', clean_content)
    
    # Remove member counts
    clean_content = re.sub(r'\d+\s*members?', '', clean_content, flags=re.IGNORECASE)
    
    # Remove dates
    clean_content = re.sub(r'\d{1,2}/\d{1,2}/\d{2,4}', '', clean_content)
    clean_content = re.sub(r'\d{1,2}-\d{1,2}-\d{2,4}', '', clean_content)
    
    # Remove long numbers (likely IDs)
    clean_content = re.sub(r'\b\d{4,}\b', '', clean_content)
    
    # Remove extra whitespace
    clean_content = re.sub(r'\s+', ' ', clean_content)
    
    return clean_content.strip()

def get_content_hash_optimized(url: str, use_cache: bool = True, debug_mode: bool = False) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
    """Get content hash with retry logic and content cleaning"""
    start_time = time.time()
    
    if use_cache and not debug_mode:
        cached = get_cached_content(url)
        if cached:
            hash_val, _ = cached
            return hash_val, 0.1, None, None
    
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        driver = None
        from_pool = False
        
        try:
            print(f"🌐 Loading URL: {url} (Attempt {retry_count + 1}/{max_retries})")
            driver, from_pool = get_driver_from_pool()
            if not driver:
                return None, time.time() - start_time, "Failed to create driver", None
            
            print(f"🔄 Navigating to URL...")
            driver.get(url)
            
            # Wait for React to load
            print(f"⏳ Waiting {REACT_WAIT_TIME}s for React to load...")
            time.sleep(REACT_WAIT_TIME)
            
            print("🔍 Looking for page elements...")
            content = None
            strategies = [
                (By.CSS_SELECTOR, ZEALY_CONTAINER_SELECTOR, ELEMENT_WAIT_TIMEOUT),
                (By.CSS_SELECTOR, "div[class*='flex'][class*='flex-col']", 15),
                (By.CSS_SELECTOR, "main", 10),
                (By.TAG_NAME, "body", 5)
            ]
            
            for by, selector, wait_time in strategies:
                try:
                    print(f"   Trying selector: {selector} (wait: {wait_time}s)")
                    element = WebDriverWait(driver, wait_time).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    
                    # Wait for content to stabilize
                    time.sleep(2)
                    
                    content = element.text
                    if content and len(content.strip()) > 10:
                        print(f"   ✅ Found content with selector: {selector} ({len(content)} chars)")
                        break
                except TimeoutException:
                    print(f"   ⚠️ Selector {selector} not found after {wait_time}s")
                    continue
            
            if not content or len(content.strip()) < 10:
                print(f"⚠️ Content too short: {len(content) if content else 0} chars")
                if retry_count < max_retries - 1:
                    retry_count += 1
                    time.sleep(RETRY_DELAY_BASE)
                    continue
                return None, time.time() - start_time, "No content found", None
            
            print(f"📄 Raw content length: {len(content)} chars")
            
            # Clean content
            clean_content = clean_zealy_content(content)
            print(f"📄 Cleaned content length: {len(clean_content)} chars")
            
            # Generate hash
            content_hash = hashlib.sha256(clean_content.encode()).hexdigest()
            response_time = time.time() - start_time
            
            # Return sample for debugging
            content_sample = f"RAW:\n{content[:250]}\n\nCLEANED:\n{clean_content[:250]}" if debug_mode else None
            
            if use_cache and not debug_mode:
                set_cached_content(url, content_hash)
            
            stats['total_checks'] += 1
            
            print(f"🔢 Hash generated: {content_hash[:16]}... in {response_time:.2f}s")
            return content_hash, response_time, None, content_sample
            
        except TimeoutException:
            print(f"⚠️ Timeout waiting for page on {url}")
            if retry_count < max_retries - 1:
                retry_count += 1
                time.sleep(RETRY_DELAY_BASE)
                continue
            stats['total_errors'] += 1
            return None, time.time() - start_time, "Timeout waiting for page", None
        except WebDriverException as e:
            print(f"⚠️ WebDriver error: {str(e)}")
            if retry_count < max_retries - 1:
                retry_count += 1
                time.sleep(RETRY_DELAY_BASE)
                continue
            stats['total_errors'] += 1
            return None, time.time() - start_time, f"WebDriver error: {str(e)}", None
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            if retry_count < max_retries - 1:
                retry_count += 1
                time.sleep(RETRY_DELAY_BASE)
                continue
            stats['total_errors'] += 1
            return None, time.time() - start_time, str(e), None
        finally:
            if driver:
                return_driver_to_pool(driver)
                gc.collect()
    
    return None, time.time() - start_time, "Max retries reached", None

# ============================================================================
# URL CHECKING FUNCTIONS
# ============================================================================

async def check_single_url(url: str, url_data: URLData) -> Tuple[str, bool, Optional[str]]:
    """Check a single URL for changes"""
    retry_count = 0
    last_error = None
    
    while retry_count < MAX_RETRIES:
        try:
            print(f"\n🔄 Checking URL (attempt {retry_count + 1}/{MAX_RETRIES}): {url}")
            loop = asyncio.get_event_loop()
            
            # Don't use cache when checking for changes!
            hash_result, response_time, error, _ = await loop.run_in_executor(
                None,
                get_content_hash_optimized,
                url,
                False,  # Don't use cache
                False   # Not debug mode
            )
            
            if hash_result is None:
                retry_count += 1
                last_error = error or "Unknown error"
                
                if retry_count < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE * retry_count
                    print(f"⏳ Retrying {url} in {delay:.1f}s")
                    print(f"⚠️ Last error: {last_error}")
                    await asyncio.sleep(delay)
                    continue
                else:
                    url_data.failures += 1
                    url_data.consecutive_successes = 0
                    url_data.last_error = last_error
                    print(f"❌ Max retries reached. Failure #{url_data.failures}/{FAILURE_THRESHOLD}")
                    return url, False, last_error
            
            # Success - Update statistics
            url_data.failures = 0
            url_data.consecutive_successes += 1
            url_data.last_error = None
            url_data.check_count += 1
            url_data.update_response_time(response_time)
            url_data.last_checked = time.time()
            
            # Check for changes
            has_changes = False
            if url_data.hash and url_data.hash != hash_result:
                has_changes = True
                print(f"🔔 CHANGE DETECTED for {url}")
                print(f"   Old hash: {url_data.hash[:16]}...")
                print(f"   New hash: {hash_result[:16]}...")
                url_data.total_changes += 1
                stats['total_changes'] += 1
            else:
                print(f"✓ No changes for {url}")
                print(f"   Current hash: {hash_result[:16]}...")
                print(f"   Response time: {response_time:.2f}s")
            
            # Update hash
            url_data.hash = hash_result
            
            return url, has_changes, None
                
        except Exception as e:
            retry_count += 1
            last_error = f"Unexpected error: {str(e)}"
            print(f"⚠️ Error checking {url}: {last_error}")
            
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

async def check_urls_parallel(bot):
    """Check URLs in parallel for maximum speed"""
    global monitored_urls
    
    if not monitored_urls:
        return
    
    current_time = time.time()
    changes_detected = []
    urls_to_remove = []
    
    print(f"\n{'='*60}")
    print(f"🚀 PARALLEL CHECK: {len(monitored_urls)} URLs")
    print(f"{'='*60}")
    
    # Create tasks for parallel execution
    tasks = []
    for url, url_data in list(monitored_urls.items()):
        task = asyncio.create_task(check_single_url(url, url_data))
        tasks.append(task)
    
    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    for result in results:
        if isinstance(result, Exception):
            print(f"❌ Task exception: {result}")
        else:
            url, has_changes, error = result
            if url in monitored_urls:
                url_data = monitored_urls[url]
                
                if has_changes:
                    if current_time - url_data.last_notified > 60:
                        changes_detected.append({
                            'url': url,
                            'response_time': url_data.avg_response_time,
                            'check_count': url_data.check_count,
                            'total_changes': url_data.total_changes
                        })
                        url_data.last_notified = current_time
                
                if url_data.failures > FAILURE_THRESHOLD:
                    urls_to_remove.append(url)
    
    # Send notifications
    for change in changes_detected:
        notification = (
            f"🚨 **CHANGE DETECTED!**\n"
            f"📍 **URL:** {change['url']}\n"
            f"⚡ **Response Time:** {change['response_time']:.2f}s\n"
            f"📊 **Check #{change['check_count']}**\n"
            f"🔄 **Total changes:** {change['total_changes']}\n"
            f"🕐 **Time:** {datetime.now().strftime('%H:%M:%S')}\n"
        )
        await notification_queue.put((notification, True))
    
    # Remove failed URLs
    for url in urls_to_remove:
        if url in monitored_urls:
            del monitored_urls[url]
            notification = (
                f"🔴 **URL REMOVED**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 **URL:** {url}\n"
                f"❌ **Reason:** Too many failures\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            await notification_queue.put((notification, False))
    
    print(f"✅ Parallel check complete: {len(changes_detected)} changes")
    save_bot_state()

async def check_urls_sequential(bot):
    """Check URLs sequentially for reliability"""
    global monitored_urls
    
    if not monitored_urls:
        return
    
    current_time = time.time()
    changes_detected = []
    urls_to_remove = []
    
    print(f"\n{'='*60}")
    print(f"🔍 SEQUENTIAL CHECK: {len(monitored_urls)} URLs")
    print(f"{'='*60}")
    
    for url, url_data in list(monitored_urls.items()):
        url, has_changes, error = await check_single_url(url, url_data)
        
        if url in monitored_urls:
            url_data = monitored_urls[url]
            
            if has_changes:
                if current_time - url_data.last_notified > 60:
                    changes_detected.append({
                        'url': url,
                        'response_time': url_data.avg_response_time,
                        'check_count': url_data.check_count,
                        'total_changes': url_data.total_changes
                    })
                    url_data.last_notified = current_time
            
            if url_data.failures > FAILURE_THRESHOLD:
                urls_to_remove.append(url)
    
    # Send notifications
    for change in changes_detected:
        notification = (
            f"🚨 **CHANGE DETECTED!**\n"
            f"📍 **URL:** {change['url']}\n"
            f"⚡ **Response Time:** {change['response_time']:.2f}s\n"
            f"📊 **Check #{change['check_count']}**\n"
            f"🔄 **Total changes:** {change['total_changes']}\n"
            f"🕐 **Time:** {datetime.now().strftime('%H:%M:%S')}\n"
        )
        await notification_queue.put((notification, True))
    
    # Remove failed URLs
    for url in urls_to_remove:
        if url in monitored_urls:
            del monitored_urls[url]
            notification = (
                f"🔴 **URL REMOVED**\n"
                f"📍 **URL:** {url}\n"
                f"❌ **Reason:** Too many failures\n"
            )
            await notification_queue.put((notification, False))
    
    print(f"✅ Sequential check complete: {len(changes_detected)} changes")
    save_bot_state()

# ============================================================================
# BACKGROUND TASKS
# ============================================================================

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
                
            elif memory_mb > MEMORY_WARNING_MB:
                print(f"🟡 WARNING: {memory_mb:.1f}MB")
                gc.collect()
                
            await asyncio.sleep(MEMORY_CHECK_INTERVAL)
            
        except Exception as e:
            print(f"❌ Memory monitor error: {e}")
            await asyncio.sleep(10)

async def start_monitoring(bot):
    """Main monitoring loop"""
    global is_monitoring
    
    mode = "Parallel" if not USE_SEQUENTIAL_MODE else "Sequential"
    
    await notification_queue.put((
        f"🟢 **MONITORING ACTIVE**\n"
        f"Tracking {len(monitored_urls)} URLs\n"
        f"Mode: {mode}\n"
        f"Check Interval: {CHECK_INTERVAL}s",
        True
    ))
    
    print(f"🚀 Starting monitoring ({mode})")
    cycle_count = 0
    
    while is_monitoring:
        try:
            cycle_count += 1
            memory_mb = get_memory_usage()
            
            print(f"\n{'='*60}")
            print(f"🔄 MONITORING CYCLE #{cycle_count}")
            print(f"📊 URLs: {len(monitored_urls)} | Memory: {memory_mb:.1f}MB")
            print(f"{'='*60}")
            
            start_time = time.time()
            
            # Choose checking method
            if USE_SEQUENTIAL_MODE:
                await check_urls_sequential(bot)
            else:
                await check_urls_parallel(bot)
            
            elapsed = time.time() - start_time
            wait_time = max(CHECK_INTERVAL - elapsed, 1)
            
            print(f"\n📊 CYCLE STATS:")
            print(f"  • Completed in: {elapsed:.2f}s")
            print(f"  • Next check in: {wait_time:.2f}s")
            
            await asyncio.sleep(wait_time)
            
        except asyncio.CancelledError:
            print("🚫 Monitoring cancelled")
            break
        except Exception as e:
            print(f"❌ Error in monitoring cycle: {e}")
            print(f"❌ Full traceback: {traceback.format_exc()}")
            await notification_queue.put((
                f"⚠️ **Monitoring Error**\n{str(e)[:100]}",
                False
            ))
            await asyncio.sleep(10)
    
    print("👋 Monitoring stopped")

# ============================================================================
# TELEGRAM COMMAND HANDLERS
# ============================================================================

async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authentication middleware"""
    user_id = update.effective_chat.id
    if user_id != CHAT_ID:
        print(f"🚫 Unauthorized: {user_id}")
        await update.message.reply_text(f"🚫 Unauthorized! Your ID: {user_id}")
        raise ApplicationHandlerStop

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    memory_mb = get_memory_usage()
    mode = "Parallel 🚀" if not USE_SEQUENTIAL_MODE else "Sequential 🔒"
    
    welcome_msg = (
        "🚀 **ZEALY BOT v2.0 FIXED**\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"⚡ **Mode: {mode}**\n"
        f"• Check Interval: {CHECK_INTERVAL}s\n"
        f"• Max URLs: {MAX_URLS}\n\n"
        "📋 **COMMANDS:**\n"
        "`/add <url>` - Add Zealy URL\n"
        "`/remove <num>` - Remove URL\n"
        "`/list` - Show all URLs\n"
        "`/run` - Start monitoring\n"
        "`/stop` - Stop monitoring\n"
        "`/status` - Statistics\n"
        "`/debug <num>` - Debug URL\n"
        "`/clear` - Clear cache\n"
        "`/memory` - Memory usage\n"
        "`/mode` - Toggle mode\n"
        "`/help` - Show this message\n\n"
        f"💾 **Memory:** {memory_mb:.1f}/{MEMORY_LIMIT_MB}MB\n"
        "━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    await start(update, context)

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add URL command"""
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(
            f"❌ **Maximum Capacity**\n"
            f"Currently monitoring {MAX_URLS} URLs",
            parse_mode='Markdown'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:**\n"
            "`/add https://zealy.io/cw/projectname`",
            parse_mode='Markdown'
        )
        return
    
    url = context.args[0].lower()
    
    if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+', url):
        await update.message.reply_text(
            "❌ **Invalid Zealy URL**\n"
            "Format: `https://zealy.io/cw/name`",
            parse_mode='Markdown'
        )
        return
    
    if url in monitored_urls:
        await update.message.reply_text(
            f"ℹ️ **Already Monitoring**\n{url}",
            parse_mode='Markdown'
        )
        return
    
    msg = await update.message.reply_text(
        f"⏳ **Verifying URL...**\n{url}",
        parse_mode='Markdown'
    )
    
    try:
        loop = asyncio.get_event_loop()
        hash_result, response_time, error, _ = await loop.run_in_executor(
            None,
            get_content_hash_optimized,
            url,
            False,
            False
        )
        
        if not hash_result:
            await msg.edit_text(
                f"❌ **Failed to Add**\n"
                f"Error: {error}",
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
            f"✅ **Added Successfully!**\n"
            f"📍 {url}\n"
            f"⚡ Load time: {response_time:.2f}s\n"
            f"📊 Slot: {len(monitored_urls)}/{MAX_URLS}",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        await msg.edit_text(
            f"❌ **Error**\n{str(e)[:100]}",
            parse_mode='Markdown'
        )

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List URLs command"""
    if not monitored_urls:
        await update.message.reply_text(
            "📋 **No URLs Monitored**\n"
            "Use `/add <url>` to add",
            parse_mode='Markdown'
        )
        return
    
    lines = ["📋 **MONITORED URLS**"]
    
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        status = "🟢" if data.failures == 0 else "🟡" if data.failures < FAILURE_THRESHOLD else "🔴"
        url_short = url.replace("https://zealy.io/cw/", "")
        
        lines.append(f"**{idx}.** {status} **{url_short}**")
        lines.append(f"   ⚡ {data.avg_response_time:.1f}s | 📊 {data.check_count} checks")
        
        if data.total_changes > 0:
            lines.append(f"   🔄 {data.total_changes} changes")
        lines.append("")
    
    lines.append(f"**Total: {len(monitored_urls)}/{MAX_URLS}**")
    
    await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove URL command"""
    if not monitored_urls:
        await update.message.reply_text(
            "❌ **No URLs to Remove**",
            parse_mode='Markdown'
        )
        return
    
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/remove <number>`\n"
            "Use `/list` to see numbers",
            parse_mode='Markdown'
        )
        return
    
    try:
        idx = int(context.args[0]) - 1
        urls = list(monitored_urls.keys())
        
        if idx < 0 or idx >= len(urls):
            await update.message.reply_text(
                f"❌ **Invalid Number**\n"
                f"Use 1-{len(urls)}",
                parse_mode='Markdown'
            )
            return
        
        url = urls[idx]
        del monitored_urls[url]
        
        with cache_lock:
            if url in content_cache:
                del content_cache[url]
        
        save_bot_state()
        
        await update.message.reply_text(
            f"✅ **URL Removed**\n{url}\n"
            f"📋 Remaining: {len(monitored_urls)}/{MAX_URLS}",
            parse_mode='Markdown'
        )
        
    except ValueError:
        await update.message.reply_text(
            "❌ **Invalid Number**",
            parse_mode='Markdown'
        )

async def debug_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug URL command"""
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/debug <number>`",
            parse_mode='Markdown'
        )
        return
    
    try:
        idx = int(context.args[0]) - 1
        urls = list(monitored_urls.keys())
        
        if idx < 0 or idx >= len(urls):
            await update.message.reply_text(
                f"❌ **Invalid Number**",
                parse_mode='Markdown'
            )
            return
        
        url = urls[idx]
        url_data = monitored_urls[url]
        
        msg = await update.message.reply_text(
            f"🔍 **Debugging URL...**\n{url}",
            parse_mode='Markdown'
        )
        
        loop = asyncio.get_event_loop()
        hash_result, response_time, error, content_sample = await loop.run_in_executor(
            None,
            get_content_hash_optimized,
            url,
            False,
            True  # Debug mode
        )
        
        if hash_result:
            change_status = "✅ NO CHANGE" if url_data.hash == hash_result else "🔄 CHANGE DETECTED"
            
            debug_text = (
                f"🔍 **DEBUG RESULTS**\n"
                f"📍 {url}\n\n"
                f"**Status:** {change_status}\n"
                f"**Current Hash:** `{hash_result[:16]}...`\n"
                f"**Stored Hash:** `{url_data.hash[:16] if url_data.hash else 'None'}...`\n"
                f"**Response Time:** {response_time:.2f}s\n\n"
                f"**Content Sample:**\n"
                f"```\n{content_sample[:500] if content_sample else 'No content'}\n```"
            )
            
            await msg.edit_text(debug_text[:4000], parse_mode='Markdown')
        else:
            await msg.edit_text(
                f"❌ **Debug Failed**\n"
                f"Error: {error}",
                parse_mode='Markdown'
            )
            
    except ValueError:
        await update.message.reply_text(
            "❌ **Invalid Number**",
            parse_mode='Markdown'
        )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command"""
    if not monitored_urls:
        await update.message.reply_text(
            "📊 **No URLs Being Monitored**",
            parse_mode='Markdown'
        )
        return
    
    total_checks = sum(d.check_count for d in monitored_urls.values())
    total_changes = sum(d.total_changes for d in monitored_urls.values())
    avg_times = [d.avg_response_time for d in monitored_urls.values()]
    overall_avg = sum(avg_times) / len(avg_times) if avg_times else 0
    
    memory_mb = get_memory_usage()
    uptime = int(time.time() - stats['start_time'])
    hours = uptime // 3600
    minutes = (uptime % 3600) // 60
    
    status_text = (
        f"📊 **STATUS REPORT**\n"
        f"**📈 MONITORING**\n"
        f"• URLs: {len(monitored_urls)}/{MAX_URLS}\n"
        f"• Total Checks: {total_checks}\n"
        f"• Total Changes: {total_changes}\n"
        f"• Avg Response: {overall_avg:.2f}s\n"
        f"• Status: {'🟢 Active' if is_monitoring else '🔴 Stopped'}\n\n"
        f"**💾 SYSTEM**\n"
        f"• Memory: {memory_mb:.1f}/{MEMORY_LIMIT_MB}MB\n"
        f"• Uptime: {hours}h {minutes}m\n"
        f"• Mode: {'Parallel' if not USE_SEQUENTIAL_MODE else 'Sequential'}\n"
    )
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear cache command"""
    with cache_lock:
        old_size = len(content_cache)
        content_cache.clear()
    
    with driver_pool_lock:
        old_pool = len(driver_pool)
        for driver in driver_pool:
            try:
                driver.quit()
            except:
                pass
        driver_pool.clear()
        driver_usage_count.clear()
    
    memory_before = get_memory_usage()
    cleanup_memory()
    memory_after = get_memory_usage()
    
    await update.message.reply_text(
        f"🧹 **CACHE CLEARED**\n"
        f"• Cache: {old_size} entries\n"
        f"• Drivers: {old_pool} closed\n"
        f"• Memory freed: {memory_before - memory_after:.1f}MB\n"
        f"• Current: {memory_after:.1f}MB",
        parse_mode='Markdown'
    )

async def memory_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memory status command"""
    memory_mb = get_memory_usage()
    memory_percent = (memory_mb / MEMORY_LIMIT_MB) * 100
    
    process = psutil.Process(os.getpid())
    cpu_percent = process.cpu_percent(interval=1)
    
    health = "🟢 Excellent" if memory_percent < 50 else "🟡 Good" if memory_percent < 70 else "🔴 Critical"
    
    await update.message.reply_text(
        f"💾 **MEMORY STATUS**\n"
        f"• RAM: {memory_mb:.1f}/{MEMORY_LIMIT_MB}MB\n"
        f"• Usage: {memory_percent:.1f}%\n"
        f"• Health: {health}\n"
        f"• CPU: {cpu_percent:.1f}%\n",
        parse_mode='Markdown'
    )

async def toggle_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle mode command"""
    global USE_SEQUENTIAL_MODE
    
    USE_SEQUENTIAL_MODE = not USE_SEQUENTIAL_MODE
    new_mode = "Sequential" if USE_SEQUENTIAL_MODE else "Parallel"
    
    save_bot_state()
    
    await update.message.reply_text(
        f"⚙️ **MODE CHANGED**\n"
        f"New Mode: **{new_mode}**\n"
        f"Workers: {1 if USE_SEQUENTIAL_MODE else MAX_PARALLEL_CHECKS}\n"
        f"{'⚠️ Restart monitoring for changes' if is_monitoring else '✅ Ready'}",
        parse_mode='Markdown'
    )

async def set_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set checking speed parameters"""
    global CHECK_INTERVAL, MAX_PARALLEL_CHECKS, REACT_WAIT_TIME
    
    if not context.args:
        await update.message.reply_text(
            "⚡ **SPEED SETTINGS**\n"
            f"• Check Interval: {CHECK_INTERVAL}s\n"
            f"• Parallel Workers: {MAX_PARALLEL_CHECKS}\n"
            f"• React Wait: {REACT_WAIT_TIME}s\n"
            f"• Mode: {'Sequential' if USE_SEQUENTIAL_MODE else 'Parallel'}\n\n"
            "**Usage:**\n"
            "`/speed fast` - Fast settings (8 workers, 10s interval)\n"
            "`/speed normal` - Normal settings (5 workers, 30s interval)\n"
            "`/speed slow` - Slow/Stable (3 workers, 60s interval)\n"
            "`/speed custom <interval> <workers>` - Custom settings",
            parse_mode='Markdown'
        )
        return
    
    preset = context.args[0].lower()
    
    if preset == "fast":
        CHECK_INTERVAL = 10
        MAX_PARALLEL_CHECKS = 8
        REACT_WAIT_TIME = 3
        settings = "Fast (10s interval, 8 workers, 3s React wait)"
    elif preset == "normal":
        CHECK_INTERVAL = 30
        MAX_PARALLEL_CHECKS = 5
        REACT_WAIT_TIME = 4
        settings = "Normal (30s interval, 5 workers, 4s React wait)"
    elif preset == "slow":
        CHECK_INTERVAL = 60
        MAX_PARALLEL_CHECKS = 3
        REACT_WAIT_TIME = 6
        settings = "Slow/Stable (60s interval, 3 workers, 6s React wait)"
    elif preset == "custom" and len(context.args) >= 3:
        try:
            CHECK_INTERVAL = int(context.args[1])
            MAX_PARALLEL_CHECKS = int(context.args[2])
            settings = f"Custom ({CHECK_INTERVAL}s interval, {MAX_PARALLEL_CHECKS} workers)"
        except ValueError:
            await update.message.reply_text("❌ Invalid custom values. Use numbers only.")
            return
    else:
        await update.message.reply_text("❌ Invalid preset. Use: fast, normal, slow, or custom")
        return
    
    save_bot_state()
    
    await update.message.reply_text(
        f"⚡ **SPEED UPDATED**\n"
        f"Settings: **{settings}**\n\n"
        f"**New Values:**\n"
        f"• Check Interval: {CHECK_INTERVAL}s\n"
        f"• Parallel Workers: {MAX_PARALLEL_CHECKS}\n"
        f"• React Wait: {REACT_WAIT_TIME}s\n\n"
        f"{'⚠️ Restart monitoring for changes to take effect' if is_monitoring else '✅ Ready to use new settings'}",
        parse_mode='Markdown'
    )

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run monitoring command"""
    global is_monitoring
    
    if is_monitoring:
        await update.message.reply_text(
            "⚠️ **Already Monitoring**",
            parse_mode='Markdown'
        )
        return
    
    if not monitored_urls:
        await update.message.reply_text(
            "❌ **No URLs to Monitor**\n"
            "Add URLs with `/add`",
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
            f"• URLs: {len(monitored_urls)}\n"
            f"• Mode: {'Sequential' if USE_SEQUENTIAL_MODE else 'Parallel'}\n"
            f"• Interval: {CHECK_INTERVAL}s",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(
            f"❌ **Failed to Start**\n{str(e)[:100]}",
            parse_mode='Markdown'
        )

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop monitoring command"""
    global is_monitoring
    
    if not is_monitoring:
        await update.message.reply_text(
            "⚠️ **Not Monitoring**",
            parse_mode='Markdown'
        )
        return
    
    is_monitoring = False
    
    # Cancel tasks
    if 'tasks' in context.chat_data:
        for task in context.chat_data['tasks'].values():
            try:
                task.cancel()
            except:
                pass
        del context.chat_data['tasks']
    
    # Clear driver pool
    with driver_pool_lock:
        for driver in driver_pool:
            try:
                driver.quit()
            except:
                pass
        driver_pool.clear()
        driver_usage_count.clear()
    
    save_bot_state()
    
    await update.message.reply_text(
        f"🛑 **MONITORING STOPPED**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ State saved\n"
        f"✅ Resources cleaned",
        parse_mode='Markdown'
    )

# ============================================================================
# MAIN FUNCTIONS
# ============================================================================

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
                f"🔄 **AUTO-RESTART**\n"
                f"Restored {len(monitored_urls)} URLs\n"
                f"Memory: {get_memory_usage():.1f}MB",
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
        print(f"🚀 ZEALY BOT v2.0 FIXED")
        print(f"📅 {datetime.now()}")
        print(f"💾 Memory limit: {MEMORY_LIMIT_MB}MB")
        
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
            CommandHandler("debug", debug_url),
            CommandHandler("clear", clear_cache),
            CommandHandler("memory", memory_status),
            CommandHandler("mode", toggle_mode),
            CommandHandler("speed", set_speed)
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