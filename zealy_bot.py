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
````
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
        NoSuchElementException
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

# Configuration - Optimized for speed
CHECK_INTERVAL = 15  # Reduced from 45 seconds
MAX_URLS = 20
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 15  # Reduced from 30 seconds
MAX_CONCURRENT_CHECKS = 10  # Process multiple URLs simultaneously
PAGE_LOAD_TIMEOUT = 15  # Faster page load timeout

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
    """Optimized Chrome options for faster performance - Only disable images to be safe"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")  # Smaller window
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")  # Only disable images - keep JS and CSS for safety
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=TranslateUI")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-field-trial-config")
    options.add_argument("--disable-back-forward-cache")
    options.add_argument("--disable-background-networking")
    options.add_argument("--memory-pressure-off")
    options.add_argument("--max_old_space_size=2048")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    # Add special options for Render.com
    if IS_RENDER:
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--no-zygote")
        options.add_argument("--single-process")
    
    # Use environment variables for paths
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
        options.binary_location = CHROME_PATH
        
    return options

async def try_lightweight_check(url):
    """Try to get content using lightweight HTTP request first"""
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    # Use BeautifulSoup for faster parsing
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # Try to find the main content
                    container = soup.select_one(ZEALY_CONTAINER_SELECTOR)
                    if not container:
                        # Fallback selectors
                        container = soup.select_one("div[class*='flex'][class*='flex-col']") or soup.select_one("main") or soup.select_one("body")
                    
                    if container:
                        text_content = container.get_text(strip=True)
                        if len(text_content) > 10:
                            print(f"✅ Lightweight check successful for {url}")
                            return text_content
    except Exception as e:
        print(f"⚠️ Lightweight check failed for {url}: {str(e)}")
    
    return None

def get_content_hash_selenium(url):
    """Fallback selenium method with optimizations"""
    driver = None
    try:
        print(f"🌐 Using Selenium for URL: {url}")
        options = get_chrome_options()
        
        try:
            if IS_RENDER or not os.path.exists(CHROMEDRIVER_PATH):
                driver = webdriver.Chrome(options=options)
            else:
                service = Service(executable_path=CHROMEDRIVER_PATH)
                driver = webdriver.Chrome(service=service, options=options)
                
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(3)  # Reduced wait time
            
            driver.get(url)
            
            # Try multiple selectors quickly
            selectors_to_try = [
                ZEALY_CONTAINER_SELECTOR,
                "div[class*='flex'][class*='flex-col']",
                "main",
                "body"
            ]
            
            container = None
            for selector in selectors_to_try:
                try:
                    container = WebDriverWait(driver, 5).until(  # Reduced wait time
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    break
                except TimeoutException:
                    continue
            
            if not container:
                return None
            
            content = container.text
            if not content or len(content.strip()) < 10:
                return None
            
            return content
            
        except Exception as e:
            print(f"⚠️ Selenium error for {url}: {str(e)}")
            return None
            
    finally:
        try:
            if driver:
                driver.quit()
        except:
            pass

async def get_content_hash(url):
    """Optimized content hash generation with fallback strategy"""
    try:
        # First try lightweight HTTP request
        content = await try_lightweight_check(url)
        
        # If lightweight fails, use Selenium in thread pool
        if not content:
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(executor, get_content_hash_selenium, url)
        
        if not content:
            return None
        
        # Clean and hash content
        clean_content = re.sub(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d+ XP|\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b', 
            '', 
            content
        )
        content_hash = hashlib.sha256(clean_content.strip().encode()).hexdigest()
        return content_hash
        
    except Exception as e:
        print(f"❌ Content hash error for {url}: {str(e)}")
        return None

async def check_single_url(url, url_data, bot):
    """Check a single URL and return result"""
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
            
            if current_time - url_data.get('last_notified', 0) > 60:  # Reduced cooldown
                # Send notification immediately without waiting
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
    """Fast notification sending without blocking"""
    try:
        message = f"🚨 CHANGE DETECTED!\n{url}\n⚡ Response: {response_time:.2f}s"
        await bot.send_message(chat_id=CHAT_ID, text=message)
        print(f"✅ Fast notification sent for {url}")
    except Exception as e:
        print(f"❌ Failed to send fast notification: {str(e)}")

async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        await update.message.reply_text("🚫 Unauthorized access!")
        raise ApplicationHandlerStop

async def send_notification(bot, message):
    """Standard notification with retries"""
    retries = 0
    while retries < 2:  # Reduced retries for speed
        try:
            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"✅ Sent notification: {message[:50]}...")
            return True
        except (TelegramError, NetworkError, TimedOut) as e:
            print(f"📡 Network error: {str(e)} - Retry {retries+1}/2")
            retries += 1
            await asyncio.sleep(2)  # Reduced retry delay
    return False

async def check_urls_concurrent(bot):
    """Concurrent URL checking for maximum speed"""
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
            if monitored_urls[url]['failures'] > 3:  # Reduced threshold
                asyncio.create_task(send_notification(bot, f"🔴 Removed due to failures: {url}"))
                del monitored_urls[url]
                print(f"🗑️ Removed {url} after 3 failures")
    
    print(f"✅ Concurrent check complete in {total_elapsed:.2f}s")
    print(f"📊 Success: {successful_checks}, Failed: {failed_checks}, Changes: {changes_detected}")

# Command handlers (optimized for faster responses)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = format_duration(time.time() - bot_stats['start_time']) if bot_stats['start_time'] else "Not started"
    
    message = (
        "🚀 **ENHANCED ZEALY MONITOR** ⚡\n\n"
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
        f"└ **Changes Found:** {bot_stats['total_changes']}"
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
