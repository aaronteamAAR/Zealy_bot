import hashlib
import asyncio
import aiohttp
import re
import shutil
import time
import os
import traceback
import sys
from datetime import datetime
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

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
    from telegram.error import TelegramError, NetworkError, TimedOut
    from telegram.request import HTTPXRequest
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
        InvalidSessionIdException,
        SessionNotCreatedException
    )
    from selenium.webdriver.chrome.service import Service
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"ERROR: Missing required package: {str(e)}")
    print("Please install required packages using:")
    print("pip install python-telegram-bot selenium python-dotenv psutil chromedriver-autoinstaller aiohttp beautifulsoup4")
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

# Configuration - Optimized for stability
CHECK_INTERVAL = 20  # Slightly increased for stability
MAX_URLS = 15  # Reduced to prevent resource exhaustion
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 15  # Increased for better reliability
MAX_CONCURRENT_CHECKS = 5  # Reduced for stability
PAGE_LOAD_TIMEOUT = 12  # Increased timeout
MAX_SELENIUM_RETRIES = 2  # Retry failed selenium attempts
DRIVER_REUSE_LIMIT = 10  # Recreate driver after N uses to prevent memory leaks

# Global bot statistics
bot_stats = {
    'start_time': None,
    'total_checks': 0,
    'total_changes': 0,
    'selenium_errors': 0,
    'http_success': 0,
    'selenium_success': 0
}

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

# Thread pool for concurrent operations
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CHECKS)

# Driver management
driver_usage_count = 0
current_driver = None

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

def cleanup_chrome_processes():
    """Kill any orphaned Chrome processes"""
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if 'chrome' in proc.info['name'].lower() or 'chromium' in proc.info['name'].lower():
                    proc.kill()
                    print(f"🧹 Cleaned up Chrome process: {proc.info['pid']}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        print(f"Warning: Error cleaning Chrome processes: {e}")

def get_chrome_options():
    """Optimized Chrome options for stability - ENHANCED"""
    options = Options()
    
    # Core stability options
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Enhanced stability options to prevent disconnections
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-hang-monitor")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--disable-prompt-on-repost")
    options.add_argument("--disable-component-extensions-with-background-pages")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-extensions-file-access-check")
    options.add_argument("--disable-extensions-http-throttling")
    
    # Memory and resource management
    options.add_argument("--memory-pressure-off")
    options.add_argument("--max-old-space-size=512")
    options.add_argument("--max-heap-size=512")
    
    # Network and connection stability
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    
    # Logging and debugging (helps identify issues)
    options.add_argument("--enable-logging")
    options.add_argument("--log-level=0")
    options.add_argument("--v=1")
    
    # Set binary location
    if not IS_RENDER:
        if os.path.exists(CHROME_PATH):
            options.binary_location = CHROME_PATH
    else:
        options.binary_location = CHROME_PATH
        # Render-specific options
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-zygote")
        options.add_argument("--single-process")
        
    return options

async def try_lightweight_check(url):
    """Enhanced lightweight HTTP request with better error handling"""
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        connector = aiohttp.TCPConnector(
            limit=10,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0'
            }
            
            async with session.get(url, headers=headers, allow_redirects=True) as response:
                if response.status == 200:
                    content = await response.text()
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # Try multiple selectors
                    selectors = [
                        ZEALY_CONTAINER_SELECTOR,
                        "div[class*='flex'][class*='flex-col']",
                        "div[class*='questboard']",
                        "main",
                        "div[id*='content']",
                        "body"
                    ]
                    
                    container = None
                    for selector in selectors:
                        container = soup.select_one(selector)
                        if container:
                            break
                    
                    if container:
                        text_content = container.get_text(separator=' ', strip=True)
                        if len(text_content) > 20:  # Require minimum content
                            print(f"✅ HTTP success for {url} ({len(text_content)} chars)")
                            bot_stats['http_success'] += 1
                            return text_content
                        
                elif response.status in [403, 429]:
                    print(f"🚫 HTTP {response.status} for {url} - may need Selenium")
                    return None
                else:
                    print(f"⚠️ HTTP {response.status} for {url}")
                    
    except asyncio.TimeoutError:
        print(f"⏰ HTTP timeout for {url}")
    except Exception as e:
        print(f"⚠️ HTTP error for {url}: {str(e)}")
    
    return None

def create_driver_with_retry():
    """Create Chrome driver with retry mechanism - ENHANCED"""
    global current_driver, driver_usage_count
    
    for attempt in range(3):
        try:
            print(f"🌐 Creating Chrome driver (attempt {attempt + 1}/3)")
            
            # Clean up any existing driver more thoroughly
            if current_driver:
                try:
                    current_driver.quit()
                    time.sleep(1)  # Wait for cleanup
                except:
                    pass
                current_driver = None
            
            # Clean up Chrome processes if needed
            if attempt > 0:
                cleanup_chrome_processes()
                time.sleep(3)  # Longer wait after cleanup
            
            options = get_chrome_options()
            
            # Add additional stability options
            options.add_argument("--disable-logging")
            options.add_argument("--disable-gpu-logging")
            options.add_argument("--silent")
            options.add_argument("--log-level=3")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-features=VizDisplayCompositor")
            
            if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
                driver = webdriver.Chrome(options=options)
            else:
                service = Service(executable_path=CHROMEDRIVER_PATH)
                driver = webdriver.Chrome(service=service, options=options)
            
            # Test the driver more thoroughly
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(3)
            
            # Multiple tests to ensure driver is stable
            test_html = "data:text/html,<html><body><div>Test</div></body></html>"
            driver.get(test_html)
            
            # Test various driver functions
            _ = driver.title
            _ = driver.current_url
            _ = driver.window_handles
            ready_state = driver.execute_script("return document.readyState")
            
            if "Test" in driver.page_source and ready_state == "complete":
                print("✅ Driver test successful")
                current_driver = driver
                driver_usage_count = 0
                return driver
            else:
                print("❌ Driver test failed")
                driver.quit()
                
        except (SessionNotCreatedException, WebDriverException) as e:
            print(f"❌ Driver creation failed (attempt {attempt + 1}): {str(e)}")
            if attempt == 2:
                raise
            time.sleep(5)  # Longer wait between attempts
        except Exception as e:
            print(f"❌ Unexpected driver error (attempt {attempt + 1}): {str(e)}")
            if attempt == 2:
                raise
            time.sleep(5)
    
    raise Exception("Failed to create Chrome driver after 3 attempts")

def get_content_hash_selenium(url):
    """Enhanced Selenium method with better session management"""
    global current_driver, driver_usage_count
    
    for retry in range(MAX_SELENIUM_RETRIES):
        driver = None
        try:
            print(f"🌐 Selenium check for {url} (retry {retry + 1}/{MAX_SELENIUM_RETRIES})")
            
            # Check if we need a new driver
            need_new_driver = (
                current_driver is None or 
                driver_usage_count >= DRIVER_REUSE_LIMIT
            )
            
            if need_new_driver:
                driver = create_driver_with_retry()
            else:
                driver = current_driver
                
                # Test if current driver is still alive - ENHANCED CHECK
                try:
                    # Multiple checks to ensure session is truly alive
                    _ = driver.current_url
                    _ = driver.title  # Additional check
                    _ = driver.window_handles  # Another session check
                    
                    # Quick test navigation to ensure renderer is responsive
                    driver.execute_script("return document.readyState")
                    
                except (InvalidSessionIdException, WebDriverException, Exception) as e:
                    print(f"🔄 Current driver session dead ({type(e).__name__}), creating new one")
                    # Clean up the dead driver
                    try:
                        current_driver.quit()
                    except:
                        pass
                    current_driver = None
                    driver = create_driver_with_retry()
            
            # Set timeouts for this session
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(3)
            
            # Navigate to URL with additional error handling
            try:
                driver.get(url)
            except Exception as nav_error:
                print(f"⚠️ Navigation error: {nav_error}")
                # If navigation fails, the session might be corrupted
                if "session deleted" in str(nav_error) or "disconnected" in str(nav_error):
                    raise InvalidSessionIdException("Session corrupted during navigation")
                raise
            
            # Wait for content with multiple selectors
            selectors_to_try = [
                ZEALY_CONTAINER_SELECTOR,
                "div[class*='flex'][class*='flex-col']",
                "div[class*='questboard']",
                "main",
                "div[id*='content']",
                "body"
            ]
            
            container = None
            for selector in selectors_to_try:
                try:
                    # Check if session is still alive before each wait
                    driver.execute_script("return document.readyState")
                    
                    container = WebDriverWait(driver, 8).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    print(f"✅ Found content with selector: {selector}")
                    break
                except TimeoutException:
                    continue
                except (InvalidSessionIdException, WebDriverException) as e:
                    print(f"🔄 Session lost during wait: {e}")
                    raise InvalidSessionIdException("Session lost during element wait")
            
            if not container:
                # Fallback to body content
                try:
                    # Final session check
                    driver.execute_script("return document.readyState")
                    container = driver.find_element(By.TAG_NAME, "body")
                except NoSuchElementException:
                    print(f"❌ No content found for {url}")
                    continue
            
            content = container.text
            if not content or len(content.strip()) < 20:
                print(f"⚠️ Content too short for {url}: {len(content)} chars")
                continue
            
            elapsed = time.time() - start_time
            print(f"✅ Selenium success for {url} in {elapsed:.2f}s ({len(content)} chars)")
            
            # Update usage counter
            driver_usage_count += 1
            bot_stats['selenium_success'] += 1
            
            return content
            
        except InvalidSessionIdException as e:
            print(f"🔄 Invalid session for {url}: {str(e)}")
            bot_stats['selenium_errors'] += 1
            
            # Force cleanup of current driver
            if current_driver:
                try:
                    current_driver.quit()
                except:
                    pass
                current_driver = None
                driver_usage_count = 0
            
            # Clean up any orphaned Chrome processes
            cleanup_chrome_processes()
            
            if retry == MAX_SELENIUM_RETRIES - 1:
                return None
            time.sleep(3)  # Longer wait after session errors
            
        except (TimeoutException, WebDriverException) as e:
            print(f"⚠️ Selenium error for {url} (retry {retry + 1}): {str(e)}")
            bot_stats['selenium_errors'] += 1
            
            # Check if this is a session-related error
            error_str = str(e).lower()
            if any(term in error_str for term in ['session deleted', 'disconnected', 'renderer']):
                print(f"🔄 Session-related error detected, forcing driver recreation")
                current_driver = None
                cleanup_chrome_processes()
                time.sleep(3)
            
            if retry == MAX_SELENIUM_RETRIES - 1:
                return None
            time.sleep(3)
            
        except Exception as e:
            print(f"❌ Unexpected Selenium error for {url}: {str(e)}")
            bot_stats['selenium_errors'] += 1
            
            # Check if this is a session-related error
            error_str = str(e).lower()
            if any(term in error_str for term in ['session deleted', 'disconnected', 'renderer']):
                print(f"🔄 Unexpected session error, forcing cleanup")
                current_driver = None
                cleanup_chrome_processes()
            
            if retry == MAX_SELENIUM_RETRIES - 1:
                return None
            time.sleep(2)
    
    return None

async def get_content_hash(url):
    """Enhanced content hash generation with better fallback strategy"""
    try:
        bot_stats['total_checks'] += 1
        
        # First try lightweight HTTP request
        content = await try_lightweight_check(url)
        
        # If lightweight fails, use Selenium in thread pool
        if not content:
            print(f"🔄 Falling back to Selenium for {url}")
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(executor, get_content_hash_selenium, url)
        
        if not content:
            print(f"❌ Failed to get any content for {url}")
            return None
        
        # Clean and hash content
        clean_content = re.sub(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b|Updated \d+ \w+ ago', 
            '', 
            content
        )
        content_hash = hashlib.sha256(clean_content.strip().encode()).hexdigest()
        return content_hash
        
    except Exception as e:
        print(f"❌ Content hash error for {url}: {str(e)}")
        print(traceback.format_exc())
        return None

async def check_single_url(url, url_data, bot):
    """Enhanced single URL checking with better error handling"""
    print(f"🔍 Checking URL: {url}")
    start_time = time.time()
    
    try:
        current_hash = await get_content_hash(url)
        elapsed = time.time() - start_time
        
        if not current_hash:
            print(f"⚠️ Failed to get hash for {url} in {elapsed:.2f}s")
            return {
                'url': url,
                'success': False,
                'elapsed': elapsed
            }
        
        # Check if content changed
        if url_data['hash'] != current_hash:
            print(f"🔔 Change detected for {url} in {elapsed:.2f}s")
            current_time = time.time()
            bot_stats['total_changes'] += 1
            
            # Cooldown check
            if current_time - url_data.get('last_notified', 0) > 90:  # 90 second cooldown
                # Send notification
                asyncio.create_task(send_notification_fast(bot, url, elapsed))
                
                return {
                    'url': url,
                    'success': True,
                    'changed': True,
                    'new_hash': current_hash,
                    'elapsed': elapsed,
                    'notified': True
                }
        
        print(f"✓ No changes for {url} in {elapsed:.2f}s")
        return {
            'url': url,
            'success': True,
            'changed': False,
            'new_hash': current_hash,
            'elapsed': elapsed
        }
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"⚠️ Error processing {url}: {str(e)} in {elapsed:.2f}s")
        return {
            'url': url,
            'success': False,
            'error': str(e),
            'elapsed': elapsed
        }

async def send_notification_fast(bot, url, response_time):
    """Fast notification sending"""
    try:
        message = f"🚨 **CHANGE DETECTED!**\n{url}\n⚡ Response: {response_time:.2f}s"
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
        print(f"✅ Notification sent for {url}")
    except Exception as e:
        print(f"❌ Failed to send notification: {str(e)}")

async def send_notification(bot, message):
    """Standard notification with retries"""
    retries = 0
    while retries < 3:
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
            print(f"✅ Sent notification")
            return True
        except (TelegramError, NetworkError, TimedOut) as e:
            print(f"📡 Network error: {str(e)} - Retry {retries+1}/3")
            retries += 1
            await asyncio.sleep(3)
    return False

def format_duration(seconds):
    """Format duration in human readable format"""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"

async def check_urls_concurrent(bot):
    """Enhanced concurrent URL checking with better error recovery"""
    global monitored_urls
    current_time = time.time()
    
    if not monitored_urls:
        print("⚠️ No URLs to check")
        return
    
    print(f"🚀 Starting concurrent check of {len(monitored_urls)} URLs")
    start_time = time.time()
    
    # Create tasks for concurrent execution
    tasks = []
    url_items = list(monitored_urls.items())
    
    for url, url_data in url_items:
        task = asyncio.create_task(check_single_url(url, url_data, bot))
        tasks.append(task)
    
    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    total_elapsed = time.time() - start_time
    successful_checks = 0
    failed_checks = 0
    changes_detected = 0
    
    for result in results:
        if isinstance(result, Exception):
            print(f"❌ Task exception: {str(result)}")
            failed_checks += 1
            continue
            
        url = result['url']
        if url not in monitored_urls:  # URL might have been removed
            continue
            
        if result['success']:
            successful_checks += 1
            monitored_urls[url]['failures'] = 0  # Reset failure count
            monitored_urls[url]['last_checked'] = current_time
            
            if result.get('changed'):
                changes_detected += 1
                monitored_urls[url]['hash'] = result['new_hash']
                if result.get('notified'):
                    monitored_urls[url]['last_notified'] = current_time
            else:
                monitored_urls[url]['hash'] = result['new_hash']
        else:
            failed_checks += 1
            monitored_urls[url]['failures'] += 1
            
            # Remove URLs with too many failures
            if monitored_urls[url]['failures'] > 5:
                asyncio.create_task(send_notification(bot, f"🔴 **Removed due to failures:** {url}"))
                del monitored_urls[url]
                print(f"🗑️ Removed {url} after 5 failures")
    
    print(f"✅ Check complete in {total_elapsed:.2f}s")
    print(f"📊 Success: {successful_checks}, Failed: {failed_checks}, Changes: {changes_detected}")
    
    # Log statistics
    error_rate = (bot_stats['selenium_errors'] / max(bot_stats['total_checks'], 1)) * 100
    if error_rate > 30:
        print(f"⚠️ High error rate: {error_rate:.1f}% - consider reducing check frequency")

async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("🚫 Unauthorized access!")
        raise ApplicationHandlerStop

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = format_duration(time.time() - bot_stats['start_time']) if bot_stats['start_time'] else "Not started"
    error_rate = (bot_stats['selenium_errors'] / max(bot_stats['total_checks'], 1)) * 100
    
    message = (
        "🚀 **ENHANCED ZEALY MONITOR v2.0** ⚡\n\n"
        "**🎯 Commands:**\n"
        "`/add <url>` - Add Zealy URL to monitor\n"
        "`/remove <num>` - Remove URL by number\n"
        "`/list` - Show all monitored URLs\n"
        "`/run` - Start monitoring process\n"
        "`/stop` - Stop monitoring process\n"
        "`/purge` - Clear all URLs\n"
        "`/stats` - Show detailed statistics\n\n"
        f"**⚙️ Configuration:**\n"
        f"└ **Max URLs:** {MAX_URLS}\n"
        f"└ **Check Interval:** {CHECK_INTERVAL}s\n"
        f"└ **Concurrent Checks:** {MAX_CONCURRENT_CHECKS}\n"
        f"└ **Current Uptime:** {uptime}\n\n"
        f"**📊 Current Status:**\n"
        f"└ **URLs Monitored:** {len(monitored_urls)}\n"
        f"└ **Monitoring Active:** {'✅ Yes' if is_monitoring else '❌ No'}\n"
        f"└ **Total Checks:** {bot_stats['total_checks']}\n"
        f"└ **Changes Found:** {bot_stats['total_changes']}\n"
        f"└ **HTTP Success:** {bot_stats['http_success']}\n"
        f"└ **Selenium Success:** {bot_stats['selenium_success']}\n"
        f"└ **Error Rate:** {error_rate:.1f}%"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not monitored_urls:
        await update.message.reply_text("📭 No monitored URLs")
        return
    
    message_parts = ["📋 **Monitored URLs:**"]
    for idx, (url, data) in enumerate(monitored_urls.items(), 1):
        last_check = data.get('last_checked', 0)
        if last_check:
            time_ago = int(time.time() - last_check)
            time_str = f" (checked {time_ago}s ago)"
        else:
            time_str = " (not checked yet)"
        message_parts.append(f"`{idx}.` {url}{time_str}")
    
    message = "\n".join(message_parts)[:4000]
    await update.message.reply_text(message, parse_mode='Markdown')

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to remove")
        return
        
    try:
        if not context.args or not context.args[0]:
            await update.message.reply_text("❌ Usage: `/remove <number>`\nUse `/list` to see numbers", parse_mode='Markdown')
            return
            
        try:
            url_index = int(context.args[0]) - 1
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")
            return
            
        url_list = list(monitored_urls.keys())
        
        if url_index < 0 or url_index >= len(url_list):
            await update.message.reply_text(f"❌ Invalid number. Use 1-{len(url_list)}")
            return
            
        url_to_remove = url_list[url_index]
        del monitored_urls[url_to_remove]
        
        await update.message.reply_text(
            f"✅ **Removed:** {url_to_remove}\n📊 **Now monitoring:** {len(monitored_urls)}/{MAX_URLS}",
            parse_mode='Markdown'
        )
        print(f"🗑️ Manually removed URL: {url_to_remove}")
        
    except Exception as e:
        print(f"⚠️ Error in remove_url: {str(e)}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(f"❌ Maximum URLs limit ({MAX_URLS}) reached")
        return
        
    try:
        if not context.args or not context.args[0]:
            await update.message.reply_text("❌ Usage: `/add <zealy-url>`", parse_mode='Markdown')
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
        processing_msg = await update.message.reply_text("⚡ **Verifying URL...**", parse_mode='Markdown')
        
        try:
            print(f"🔄 Getting initial hash for {url}")
            start_time = time.time()
            initial_hash = await get_content_hash(url)
            elapsed = time.time() - start_time
            
            if not initial_hash:
                await processing_msg.edit_text("❌ Failed to verify URL. Check console for details.")
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
                f"✅ **Added:** {url}\n⚡ **Verified in:** {elapsed:.2f}s\n📊 **Monitoring:** {len(monitored_urls)}/{MAX_URLS}",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            print(f"❌ Error while getting initial hash: {str(e)}")
            await processing_msg.edit_text(f"❌ Failed to add URL: {str(e)}")
            
    except Exception as e:
        print(f"⚠️ Error in add_url: {str(e)}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

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
        monitor_task = asyncio.create_task(start_monitoring_fast(context.application))
        context.chat_data['monitor_task'] = monitor_task
        await update.message.reply_text(f"✅ **Fast monitoring started!**\n⚡ **Check interval:** {CHECK_INTERVAL}s", parse_mode='Markdown')
        print("✅ Fast monitoring task created and started")
    except Exception as e:
        is_monitoring = False
        await update.message.reply_text(f"❌ Failed to start: {str(e)}")
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
    
    await update.message.reply_text("🛑 **Monitoring stopped**", parse_mode='Markdown')

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    count = len(monitored_urls)
    monitored_urls.clear()
    await update.message.reply_text(f"✅ **Purged {count} URLs!**", parse_mode='Markdown')

async def start_monitoring_fast(application: Application):
    """Optimized monitoring loop with concurrent checks"""
    global is_monitoring
    bot = application.bot
    await send_notification(bot, f"🚀 **Fast monitoring started!**\n⚡ Check every {CHECK_INTERVAL}s")
    print("🚀 Entering fast monitoring loop")
    
    while is_monitoring:
        try:
            if monitored_urls:
                print(f"🔄 Starting concurrent check of {len(monitored_urls)} URLs")
                cycle_start = time.time()
                
                await check_urls_concurrent(bot)
                
                cycle_time = time.time() - cycle_start
                wait_time = max(CHECK_INTERVAL - cycle_time, 1)
                
                print(f"✓ Cycle complete in {cycle_time:.2f}s, waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
            else:
                print("📭 No URLs to monitor, waiting...")
                await asyncio.sleep(CHECK_INTERVAL)
                
        except asyncio.CancelledError:
            print("🚫 Fast monitoring cancelled")
            break
        except Exception as e:
            print(f"🚨 Monitoring error: {str(e)}")
            print(traceback.format_exc())
            await asyncio.sleep(10)  # Shorter error recovery time
    
    print("👋 Exiting fast monitoring loop")
    await send_notification(bot, "🔴 **Fast monitoring stopped!**")

def main():
    try:
        global CHROME_PATH, CHROMEDRIVER_PATH
        
        print(f"🚀 Starting FAST Zealy Bot at {datetime.now()}")
        kill_previous_instances()

        # Debug environment info
        print(f"🌍 Operating System: {platform.system()}")
        print(f"🌍 Running on Render: {IS_RENDER}")
        print(f"⚡ Max concurrent checks: {MAX_CONCURRENT_CHECKS}")
        print(f"⚡ Check interval: {CHECK_INTERVAL}s")
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
