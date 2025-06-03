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
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
import threading
from queue import Queue, Empty

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

# Enhanced Configuration
CHECK_INTERVAL = 15  # Reduced for faster detection
MAX_URLS = 20 
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 20  # Reduced timeout
MAX_CONCURRENT_CHECKS = 5  # Parallel processing
DRIVER_POOL_SIZE = 3  # Browser connection pool
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2  # Base delay for exponential backoff
FAILURE_THRESHOLD = 8  # Increased threshold before removal

# Set appropriate paths based on environment
IS_RENDER = os.getenv('IS_RENDER', 'false').lower() == 'true'

if IS_RENDER:
    CHROME_PATH = '/usr/bin/chromium'
    CHROMEDRIVER_PATH = '/usr/bin/chromedriver'
elif platform.system() == "Windows":
    CHROME_PATH = os.getenv('CHROME_BIN', 
                          r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    CHROMEDRIVER_PATH = os.getenv('CHROME_DRIVER', 
                                shutil.which('chromedriver') or r"C:\Program Files\chromedriver\chromedriver.exe")
else:
    CHROME_PATH = os.getenv('CHROME_BIN', '/usr/bin/chromium')
    CHROMEDRIVER_PATH = os.getenv('CHROME_DRIVER', '/usr/lib/chromium/chromedriver')

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
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")
    options.add_argument("--disable-javascript")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Performance optimizations
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--aggressive-cache-discard")
    
    if IS_RENDER:
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-zygote")
        options.add_argument("--single-process")
        options.add_argument("--memory-pressure-off")
        options.add_argument("--max_old_space_size=4096")
    
    print(f"🕵️ Using Chrome binary path: {CHROME_PATH}")
    print(f"🕵️ Using Chromedriver path: {CHROMEDRIVER_PATH}")
    
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
        """Update average response time with exponential moving average"""
        if self.avg_response_time == 0:
            self.avg_response_time = response_time
        else:
            self.avg_response_time = 0.7 * self.avg_response_time + 0.3 * response_time

class DriverPool:
    """Connection pool for WebDriver instances"""
    
    def __init__(self, pool_size: int = DRIVER_POOL_SIZE):
        self.pool_size = pool_size
        self.available_drivers = Queue()
        self.active_drivers = set()
        self.lock = threading.Lock()
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Pre-initialize driver pool for faster access"""
        for _ in range(self.pool_size):
            try:
                driver = self._create_driver()
                if driver:
                    self.available_drivers.put(driver)
                    print(f"✅ Driver added to pool. Pool size: {self.available_drivers.qsize()}")
            except Exception as e:
                print(f"⚠️ Failed to initialize driver in pool: {e}")
    
    def _create_driver(self):
        """Create a new WebDriver instance"""
        try:
            options = get_chrome_options()
            if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
                driver = webdriver.Chrome(options=options)
            else:
                service = Service(executable_path=CHROMEDRIVER_PATH)
                driver = webdriver.Chrome(service=service, options=options)
            
            # Pre-configure driver for better performance
            driver.set_page_load_timeout(REQUEST_TIMEOUT)
            driver.implicitly_wait(5)
            return driver
        except Exception as e:
            print(f"❌ Failed to create driver: {e}")
            return None
    
    def get_driver(self, timeout: int = 10):
        """Get an available driver from the pool"""
        try:
            driver = self.available_drivers.get(timeout=timeout)
            with self.lock:
                self.active_drivers.add(driver)
            return driver
        except Empty:
            # If no drivers available, create a new one
            print("⚠️ No drivers available, creating new one...")
            driver = self._create_driver()
            if driver:
                with self.lock:
                    self.active_drivers.add(driver)
            return driver
    
    def return_driver(self, driver):
        """Return a driver to the pool"""
        if not driver:
            return
            
        try:
            with self.lock:
                self.active_drivers.discard(driver)
            
            # Check if driver is still functional
            if self._is_driver_healthy(driver):
                self.available_drivers.put(driver)
            else:
                print("🔄 Replacing unhealthy driver")
                self._close_driver(driver)
                # Replace with new driver
                new_driver = self._create_driver()
                if new_driver:
                    self.available_drivers.put(new_driver)
        except Exception as e:
            print(f"⚠️ Error returning driver to pool: {e}")
            self._close_driver(driver)
    
    def _is_driver_healthy(self, driver) -> bool:
        """Check if driver is still functional"""
        try:
            driver.current_url  # Simple health check
            return True
        except Exception:
            return False
    
    def _close_driver(self, driver):
        """Safely close a driver"""
        try:
            driver.quit()
        except Exception as e:
            print(f"⚠️ Error closing driver: {e}")
    
    def cleanup(self):
        """Clean up all drivers in the pool"""
        print("🧹 Cleaning up driver pool...")
        
        # Close available drivers
        while not self.available_drivers.empty():
            try:
                driver = self.available_drivers.get_nowait()
                self._close_driver(driver)
            except Empty:
                break
        
        # Close active drivers
        with self.lock:
            for driver in self.active_drivers.copy():
                self._close_driver(driver)
            self.active_drivers.clear()

# Global instances (will be initialized after function definitions)
monitored_urls: Dict[str, URLData] = {}
is_monitoring = False
driver_pool = None
notification_queue = Queue()
SECURITY_LOG = "activity.log"

def get_content_hash_fast(url: str, debug_mode: bool = False) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
    """
    Fast content hash extraction with improved error handling
    Returns: (hash, response_time, error_message, raw_content_sample)
    """
    driver = None
    start_time = time.time()
    
    try:
        print(f"🌐 Getting driver for URL: {url}")
        driver = driver_pool.get_driver(timeout=5)
        
        if not driver:
            return None, time.time() - start_time, "Failed to get driver from pool", None
        
        print(f"🌐 Loading URL: {url}")
        driver.get(url)
        
        print("⏳ Waiting for page elements...")
        # Keep the exact same selector logic as requested
        selectors_to_try = [
            ZEALY_CONTAINER_SELECTOR,
            "div[class*='flex'][class*='flex-col']",
            "main",
            "body"
        ]
        
        container = None
        for selector in selectors_to_try:
            try:
                container = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                print(f"✅ Found element with selector: {selector}")
                break
            except TimeoutException:
                print(f"⚠️ Selector {selector} not found, trying next...")
                continue
        
        if not container:
            return None, time.time() - start_time, "No suitable container found", None
        
        # Reduced wait time for faster processing
        time.sleep(1)
        content = container.text
        
        if not content or len(content.strip()) < 10:
            return None, time.time() - start_time, f"Content too short: {len(content)} chars", None
        
        print(f"📄 Content retrieved, length: {len(content)} chars")
        
        # Enhanced content cleaning to remove dynamic elements
        clean_content = content
        
        # Remove timestamps (various formats)
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
        
        # Remove dynamic counters and statistics
        clean_content = re.sub(r'\d+\s*(?:total|count|number)', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:total|count|number)\s*:\s*\d+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove rank and position indicators (but keep quest ranks)
        clean_content = re.sub(r'(?:rank|position)\s*#?\d+', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'#\d+\s*(?:rank|position)', '', clean_content, flags=re.IGNORECASE)
        
        # Remove session-specific data
        clean_content = re.sub(r'session\s*[a-f0-9]+', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'token\s*[a-f0-9]+', '', clean_content, flags=re.IGNORECASE)
        
        # Remove loading states and dynamic text
        clean_content = re.sub(r'(?:loading|refreshing|updating)\.{0,3}', '', clean_content, flags=re.IGNORECASE)
        
        # Remove whitespace variations and normalize
        clean_content = re.sub(r'\s+', ' ', clean_content)
        clean_content = clean_content.strip()
        
        # Additional filtering for Zealy-specific dynamic content
        clean_content = re.sub(r'(?:quest|task)\s+\d+\s*(?:of|/)\s*\d+', '', clean_content, flags=re.IGNORECASE)
        clean_content = re.sub(r'(?:day|week|month)\s+\d+', '', clean_content, flags=re.IGNORECASE)
        
        print(f"📄 Content cleaned, original: {len(content)} chars, cleaned: {len(clean_content)} chars")
        content_hash = hashlib.sha256(clean_content.encode()).hexdigest()
        response_time = time.time() - start_time
        
        # Return sample for debugging if requested
        content_sample = content[:500] if debug_mode else None
        
        print(f"🔢 Hash generated: {content_hash[:8]}... in {response_time:.2f}s")
        return content_hash, response_time, None, content_sample
        
    except TimeoutException as e:
        error_msg = f"Timeout waiting for page elements: {str(e)}"
        print(f"⚠️ {error_msg}")
        return None, time.time() - start_time, error_msg, None
    except WebDriverException as e:
        error_msg = f"WebDriver error: {str(e)}"
        print(f"⚠️ {error_msg}")
        return None, time.time() - start_time, error_msg, None
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"❌ {error_msg}")
        return None, time.time() - start_time, error_msg, None
    finally:
        if driver:
            driver_pool.return_driver(driver)

async def check_single_url(url: str, url_data: URLData) -> Tuple[str, bool, Optional[str]]:
    """
    Check a single URL with smart retry logic
    Returns: (url, has_changes, error_message)
    """
    retry_count = 0
    last_error = None
    
    while retry_count < MAX_RETRIES:
        try:
            # Use thread pool for CPU-bound hash operation
            loop = asyncio.get_event_loop()
            hash_result, response_time, error, content_sample = await loop.run_in_executor(
                None, get_content_hash_fast, url, False  # Debug mode off by default
            )
            
            if hash_result is None:
                retry_count += 1
                last_error = error or "Unknown error"
                
                if retry_count < MAX_RETRIES:
                    # Exponential backoff with jitter
                    delay = RETRY_DELAY_BASE ** retry_count + (retry_count * 0.5)
                    print(f"⏳ Retrying {url} in {delay:.1f}s (attempt {retry_count + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Max retries reached
                    url_data.failures += 1
                    url_data.consecutive_successes = 0
                    url_data.last_error = last_error
                    print(f"❌ Max retries reached for {url}. Failure #{url_data.failures}")
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
            
            if retry_count < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY_BASE ** retry_count)
            else:
                url_data.failures += 1
                url_data.consecutive_successes = 0
                url_data.last_error = last_error
                return url, False, last_error
    
    return url, False, last_error

async def send_notification(bot, message: str, priority: bool = False):
    """Enhanced notification system with queue and priority"""
    if priority:
        # For urgent notifications, send immediately
        return await _send_telegram_message(bot, message)
    else:
        # Add to queue for batch processing
        notification_queue.put(message)
        return True

async def _send_telegram_message(bot, message: str) -> bool:
    """Send message with improved retry logic"""
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

async def process_notification_queue(bot):
    """Process queued notifications in batches"""
    while is_monitoring:
        try:
            messages = []
            start_time = time.time()
            
            # Collect messages for up to 2 seconds or until we have 5 messages
            while len(messages) < 5 and (time.time() - start_time) < 2:
                try:
                    message = notification_queue.get(timeout=0.5)
                    messages.append(message)
                except Empty:
                    break
            
            # Send batched messages
            if messages:
                batch_message = "\n".join(messages)
                await _send_telegram_message(bot, batch_message[:4000])
            
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"⚠️ Error in notification queue processor: {e}")
            await asyncio.sleep(5)

async def check_urls_parallel(bot):
    """Parallel URL checking with concurrency control"""
    global monitored_urls
    current_time = time.time()
    
    if not monitored_urls:
        print("⚠️ No URLs to check")
        return
    
    print(f"🔍 Checking {len(monitored_urls)} URLs in parallel...")
    
    # Create semaphore to limit concurrent checks
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    
    async def check_with_semaphore(url, url_data):
        async with semaphore:
            return await check_single_url(url, url_data)
    
    # Start all checks concurrently
    tasks = [
        check_with_semaphore(url, url_data) 
        for url, url_data in list(monitored_urls.items())
    ]
    
    # Wait for all checks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    changes_detected = 0
    urls_to_remove = []
    
    for result in results:
        if isinstance(result, Exception):
            print(f"⚠️ Task exception: {result}")
            continue
            
        url, has_changes, error = result
        
        if url not in monitored_urls:
            continue
            
        url_data = monitored_urls[url]
        
        if has_changes:
            changes_detected += 1
            # Check rate limiting for notifications
            if current_time - url_data.last_notified > 60:  # Reduced to 1 minute
                await send_notification(
                    bot, 
                    f"🚨 CHANGE DETECTED!\n{url}\nAvg response: {url_data.avg_response_time:.2f}s\nCheck #{url_data.check_count}",
                    priority=True
                )
                url_data.last_notified = current_time
        
        # Handle failures with smarter logic
        if url_data.failures > FAILURE_THRESHOLD:
            urls_to_remove.append(url)
        elif url_data.failures > 3 and url_data.consecutive_successes == 0:
            # Temporary failure notification
            await send_notification(
                bot,
                f"⚠️ Monitoring issues for {url}\nFailures: {url_data.failures}/{FAILURE_THRESHOLD}\nLast error: {url_data.last_error or 'Unknown'}"
            )
    
    # Remove problematic URLs
    for url in urls_to_remove:
        del monitored_urls[url]
        await send_notification(
            bot, 
            f"🔴 Removed from monitoring (too many failures): {url}",
            priority=True
        )
        print(f"🗑️ Removed {url} after {FAILURE_THRESHOLD} failures")
    
    print(f"✅ Parallel check complete: {changes_detected} changes, {len(urls_to_remove)} removed")

# Command handlers
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("🚫 Unauthorized access!")
        raise ApplicationHandlerStop

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Enhanced Zealy Monitoring Bot\n\n"
        "Commands:\n"
        "/add <url> - Add monitoring URL\n"
        "/remove <number> - Remove URL by number\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/purge - Remove all URLs\n"
        "/status - Show monitoring statistics\n"
        "/debug <number> - Debug URL content\n"
        "/sensitivity - View filter settings\n"
        f"Max URLs: {MAX_URLS}\n"
        f"Check interval: {CHECK_INTERVAL}s\n"
        f"Parallel checks: {MAX_CONCURRENT_CHECKS}\n\n"
        "✨ Now with enhanced filtering to reduce false positives!"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("📊 No URLs being monitored")
        return
    
    status_lines = ["📊 Monitoring Statistics:\n"]
    
    for url, data in monitored_urls.items():
        status_lines.append(
            f"🔗 {url[:50]}...\n"
            f"   ✅ Checks: {data.check_count} | Failures: {data.failures}\n"
            f"   ⚡ Avg time: {data.avg_response_time:.2f}s\n"
            f"   🕐 Last: {time.time() - data.last_checked:.0f}s ago"
        )
        
        if data.last_error:
            status_lines.append(f"   ❌ Error: {data.last_error[:30]}...")
        
        status_lines.append("")
    
    # Add driver pool status
    if driver_pool:
        status_lines.append(f"🔧 Driver pool: {driver_pool.available_drivers.qsize()}/{driver_pool.pool_size} available")
    status_lines.append(f"🔄 Monitoring: {'✅ Active' if is_monitoring else '❌ Stopped'}")
    
    message = "\n".join(status_lines)[:4000]
    await update.message.reply_text(message)

async def debug_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to see what content is being monitored for a URL"""
    if update.effective_chat.id != CHAT_ID:
        return
    
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
        processing_msg = await update.message.reply_text(f"🔍 Debugging content for: {url}")
        
        # Get content in debug mode
        loop = asyncio.get_event_loop()
        hash_result, response_time, error, content_sample = await loop.run_in_executor(
            None, get_content_hash_fast, url, True  # Debug mode ON
        )
        
        if hash_result:
            current_data = monitored_urls[url]
            debug_info = [
                f"🔍 Debug Info for URL #{url_index + 1}:",
                f"📄 Current hash: {current_data.hash[:12]}...",
                f"📄 New hash: {hash_result[:12]}...",
                f"🔄 Hashes match: {'✅ Yes' if current_data.hash == hash_result else '❌ No - CHANGE DETECTED!'}",
                f"⚡ Response time: {response_time:.2f}s",
                f"📊 Check count: {current_data.check_count}",
                f"❌ Failures: {current_data.failures}",
                "",
                "📝 Content sample (first 400 chars):",
                f"```{content_sample[:400] if content_sample else 'No sample available'}```"
            ]
            
            debug_message = "\n".join(debug_info)
            await processing_msg.edit_text(debug_message[:4000])
        else:
            await processing_msg.edit_text(f"❌ Failed to get content: {error}")
            
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number")
    except Exception as e:
        await update.message.reply_text(f"❌ Debug error: {str(e)}")

async def sensitivity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adjust monitoring sensitivity"""
    if update.effective_chat.id != CHAT_ID:
        return
    
    help_text = [
        "🎛️ Sensitivity Settings:",
        "",
        "Current filters remove:",
        "✅ Timestamps and dates",
        "✅ XP and point counters", 
        "✅ View counts and engagement",
        "✅ Online user counts",
        "✅ Progress indicators",
        "✅ Rank positions",
        "✅ Session IDs and tokens",
        "✅ Loading states",
        "",
        "If you're still getting false positives:",
        "1. Use /debug <number> to see what content is changing",
        "2. The enhanced filters should catch most background changes",
        "3. Real quest updates should still be detected"
    ]
    
    await update.message.reply_text("\n".join(help_text))

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("No monitored URLs")
        return
    
    message_lines = ["📋 Monitored URLs:\n"]
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        status = "✅" if data.failures == 0 else f"⚠️({data.failures})"
        message_lines.append(f"{idx}. {status} {url}")
    
    message = "\n".join(message_lines)[:4000]
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
        
        if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+', url):
            await update.message.reply_text("❌ Invalid Zealy URL format")
            return
            
        if url in monitored_urls:
            await update.message.reply_text("ℹ️ URL already monitored")
            return
            
        processing_msg = await update.message.reply_text("⏳ Verifying URL (fast check)...")
        
        try:
            # Use the fast hash function
            loop = asyncio.get_event_loop()
            print(f"🔄 Getting initial hash for {url}")
            initial_hash, response_time, error, content_sample = await loop.run_in_executor(
                None, get_content_hash_fast, url, False
            )
            
            if not initial_hash:
                await processing_msg.edit_text(f"❌ Failed to verify URL: {error}")
                return
                
            # Add to monitored URLs with enhanced data structure
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
                f"✅ Added: {url}\n"
                f"📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}\n"
                f"⚡ Initial response: {response_time:.2f}s"
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
        notification_task = asyncio.create_task(process_notification_queue(context.application.bot))
        
        context.chat_data['monitor_task'] = monitor_task
        context.chat_data['notification_task'] = notification_task
        
        await update.message.reply_text(
            f"✅ Enhanced monitoring started!\n"
            f"🔍 Checking {len(monitored_urls)} URLs every {CHECK_INTERVAL}s\n"
            f"⚡ Parallel processing: {MAX_CONCURRENT_CHECKS} concurrent checks"
        )
        print("✅ Enhanced monitoring tasks created and started")
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(f"❌ Failed to start monitoring: {str(e)}")
        print(f"❌ Error starting monitoring: {str(e)}")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    is_monitoring = False
    
    # Cancel tasks
    for task_name in ['monitor_task', 'notification_task']:
        if task_name in context.chat_data:
            try:
                context.chat_data[task_name].cancel()
                del context.chat_data[task_name]
                print(f"🛑 {task_name} cancelled")
            except Exception as e:
                print(f"⚠️ Error cancelling {task_name}: {str(e)}")
    
    await update.message.reply_text("🛑 Enhanced monitoring stopped")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    count = len(monitored_urls)
    monitored_urls.clear()
    await update.message.reply_text(f"✅ All {count} URLs purged!")

async def start_monitoring(application: Application):
    global is_monitoring
    bot = application.bot
    await send_notification(bot, "🔔 Enhanced monitoring started!", priority=True)
    print("🔍 Entering enhanced monitoring loop")
    
    while is_monitoring:
        try:
            print(f"🔄 Running parallel URL check cycle - {len(monitored_urls)} URLs")
            start_time = time.time()
            
            await check_urls_parallel(bot)
            
            elapsed = time.time() - start_time
            wait_time = max(CHECK_INTERVAL - elapsed, 2)
            print(f"✓ Parallel check complete in {elapsed:.2f}s, waiting {wait_time:.2f}s")
            
            await asyncio.sleep(wait_time)
            
        except asyncio.CancelledError:
            print("🚫 Monitoring task was cancelled")
            break
        except Exception as e:
            print(f"🚨 Monitoring error: {str(e)}")
            print(traceback.format_exc())
            await asyncio.sleep(10)  # Shorter error recovery time
    
    print("👋 Exiting enhanced monitoring loop")
    await send_notification(bot, "🔴 Enhanced monitoring stopped!", priority=True)

def main():
    try:
        global CHROME_PATH, CHROMEDRIVER_PATH, driver_pool
        
        print(f"🚀 Starting enhanced bot at {datetime.now()}")
        kill_previous_instances()

        print(f"🌍 Operating System: {platform.system()}")
        print(f"🌍 Running on Render: {IS_RENDER}")
        print(f"💾 Chrome path: {CHROME_PATH}")
        print(f"💾 Chromedriver path: {CHROMEDRIVER_PATH}")
        print(f"⚡ Max concurrent checks: {MAX_CONCURRENT_CHECKS}")
        print(f"🔧 Driver pool size: {DRIVER_POOL_SIZE}")
        
        if not IS_RENDER:
            print(f"📂 Chrome exists: {os.path.exists(CHROME_PATH)}")
            print(f"📂 Chromedriver exists: {os.path.exists(CHROMEDRIVER_PATH)}")
            
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
                    
            if chrome_path_to_use != CHROME_PATH or chromedriver_path_to_use != CHROMEDRIVER_PATH:
                CHROME_PATH = chrome_path_to_use
                CHROMEDRIVER_PATH = chromedriver_path_to_use
                print(f"📌 Using Chrome at: {CHROME_PATH}")
                print(f"📌 Using Chromedriver at: {CHROMEDRIVER_PATH}")
        
        # Initialize driver pool after paths are set
        print("🔧 Initializing driver pool...")
        driver_pool = DriverPool()
        
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
            CommandHandler("purge", purge_urls),
            CommandHandler("status", status),
            CommandHandler("debug", debug_url),
            CommandHandler("sensitivity", sensitivity)
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
        print("🧹 Cleaning up driver pool...")
        if driver_pool:
            driver_pool.cleanup()
        print("🧹 Cleanup complete")

if __name__ == "__main__":
    print("Enhanced script starting...")
    try:
        main()
    except Exception as e:
        print(f"❌ CRITICAL ERROR in __main__: {str(e)}")
        print(traceback.format_exc())
        input("Press Enter to exit...")
    finally:
        # Final cleanup
        try:
            if 'driver_pool' in globals() and driver_pool:
                driver_pool.cleanup()
        except:
            pass