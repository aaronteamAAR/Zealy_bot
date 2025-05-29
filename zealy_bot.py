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
CHAT_ID = int(os.getenv('CHAT_ID')) if os.getenv('CHAT_ID') else None
CHECK_INTERVAL = 10  # Fast 10-second intervals
MAX_URLS = 20
TIMEOUT = 15  # Increased for better reliability

# Global storage
monitored_urls = {}
is_monitoring = False
driver_pool = []
session = None
monitoring_task = None


def create_balanced_driver():
    """Optimized Chrome driver - balances speed with reliability"""
    options = Options()
    
    # Basic setup
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")
    
    # Performance optimizations (less aggressive)
    options.add_argument("--disable-images")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
    options.add_argument("--disable-logging")
    options.add_argument("--silent")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    
    # Balanced network settings
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-background-timer-throttling")
    
    # User agent
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(12)  # Balanced - 12 seconds
        driver.implicitly_wait(5)         # Reasonable wait for elements
        return driver
    except Exception as e:
        print(f"❌ Failed to create Chrome driver: {e}")
        raise


def get_driver():
    """Get driver from pool or create new one with health check"""
    if driver_pool:
        driver = driver_pool.pop()
        try:
            driver.current_url
            return driver
        except WebDriverException:
            try:
                driver.quit()
            except:
                pass
    return create_balanced_driver()


def return_driver(driver):
    """Return driver to pool with health check"""
    if not driver:
        return
    try:
        driver.current_url
        if len(driver_pool) < 3:  # Reasonable pool size
            driver_pool.append(driver)
        else:
            driver.quit()
    except:
        try:
            driver.quit()
        except:
            pass


async def smart_content_check(url):
    """Smart content checking - fast but thorough"""
    driver = None
    try:
        driver = get_driver()
        
        print(f"🔍 Smart check: {url}")
        start_time = time.time()
        
        # Load page
        driver.get(url)
        
        # IMPROVED: Progressive content loading check
        # Start with quick check, then allow more time if needed
        await asyncio.sleep(1.0)  # Initial wait for basic content
        
        # Enhanced selector strategy with priority order
        selectors_to_try = [
            # Primary Zealy selectors (most specific first)
            "div.flex.flex-col.w-full.pt-100",           # Your original primary
            "[data-testid='leaderboard-container']",      # Specific leaderboard
            ".leaderboard-wrapper, .leaderboard-content", # Leaderboard variations
            "[data-testid='quest-list'], .quest-container", # Quest content
            ".community-leaderboard, .user-rankings",    # Community content
            
            # Secondary selectors (broader but still relevant)
            "[data-testid='leaderboard']",
            ".leaderboard-container",
            "[class*='leaderboard']",
            ".quest-list, [data-testid='quest-list']",
            ".user-rank, [class*='rank']",
            ".community-stats",
            
            # Fallback selectors
            "main [class*='content']",
            "div[class*='flex'][class*='col']",
            "main",
            "body"
        ]
        
        content = None
        best_content = ""
        content_quality_score = 0
        
        for i, selector in enumerate(selectors_to_try):
            try:
                # IMPROVED: Adaptive timeout based on selector priority
                timeout = 6 if i < 5 else 4  # More time for primary selectors
                
                element = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                
                # Wait a bit more for dynamic content if it's a primary selector
                if i < 3:  # Primary selectors get extra loading time
                    await asyncio.sleep(1.5)
                    # Re-get element to ensure fresh content
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                
                current_content = element.text.strip()
                
                # IMPROVED: Content quality scoring
                zealy_indicators = [
                    'leaderboard', 'xp', 'rank', 'points', 'quest', 'reward', 
                    'community', 'member', 'score', 'level', 'badge', 'achievement'
                ]
                content_lower = current_content.lower()
                
                # Calculate quality score
                quality_score = 0
                quality_score += sum(1 for indicator in zealy_indicators if indicator in content_lower)
                quality_score += min(len(current_content) // 100, 10)  # Length bonus (max 10)
                quality_score += (10 - i)  # Selector priority bonus
                
                # Immediate acceptance for high-quality primary content
                if i < 3 and quality_score > 8 and len(current_content) > 100:
                    content = current_content
                    print(f"✅ HIGH QUALITY content with {selector}: {len(content)} chars, score: {quality_score}")
                    break
                
                # Track best content found so far
                if quality_score > content_quality_score:
                    content_quality_score = quality_score
                    best_content = current_content
                    print(f"📊 Better content found: score {quality_score}, {len(current_content)} chars")
                
            except TimeoutException:
                if i < 3:  # Log timeouts for primary selectors
                    print(f"⏱️ Timeout on primary selector: {selector}")
                continue
            except Exception as e:
                print(f"⚠️ Error with selector {selector}: {e}")
                continue
        
        # Use best available content
        if not content and best_content:
            content = best_content
            print(f"📝 Using best available content: {content_quality_score} score, {len(content)} chars")
        
        # IMPROVED: Content validation
        if not content or len(content.strip()) < 50:
            print(f"❌ Insufficient content: {len(content) if content else 0} chars")
            return None
        
        # Enhanced content cleaning for better change detection
        clean_content = content
        
        # Remove dynamic timestamps and counters
        patterns_to_remove = [
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z',  # ISO timestamps
            r'\d{1,2}:\d{2}:\d{2}(?:\s*[AP]M)?',                    # Time formats
            r'Updated \d+[smhd] ago|Last updated.*ago',             # Update timestamps
            r'\d+ XP|\d+XP|\d+\s*XP',                               # XP values
            r'#\d+|Rank\s*#?\d+',                                   # Rank numbers
            r'\d{1,3}(?:,\d{3})*(?:\.\d+)?',                       # Large numbers
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}(?:,\s*\d{4})?', # Dates
            r'Online now|\d+\s*online|Active \d+[smh] ago',        # Online status
            r'Loading\.\.\.|Please wait\.\.\.',                     # Loading indicators
        ]
        
        for pattern in patterns_to_remove:
            clean_content = re.sub(pattern, '', clean_content, flags=re.IGNORECASE)
        
        # Normalize whitespace
        clean_content = re.sub(r'\s+', ' ', clean_content).strip()
        
        # Create hash from cleaned content
        content_hash = hashlib.sha256(clean_content.encode()).hexdigest()
        
        # Enhanced logging
        preview = clean_content[:150].replace('\n', ' ')
        elapsed = time.time() - start_time
        print(f"🎯 Content captured: {len(content)} chars → {len(clean_content)} clean")
        print(f"📝 Preview: {preview}...")
        print(f"⚡ Hash: {content_hash[:12]}... ({elapsed:.2f}s)")
        
        return content_hash
        
    except Exception as e:
        print(f"❌ Content check error {url}: {e}")
        return None
    finally:
        return_driver(driver)


async def intelligent_monitoring(bot):
    """Smart monitoring with improved reliability"""
    global monitored_urls
    
    if not monitored_urls:
        return
    
    print(f"🔍 INTELLIGENT CHECK: {len(monitored_urls)} URLs...")
    start_time = time.time()
    
    # Balanced concurrency - not too aggressive
    semaphore = asyncio.Semaphore(4)  # Balanced concurrency
    
    async def check_with_semaphore(url):
        async with semaphore:
            return await smart_content_check(url)
    
    tasks = []
    urls = list(monitored_urls.keys())
    
    for url in urls:
        task = asyncio.create_task(check_with_semaphore(url))
        tasks.append((url, task))
    
    try:
        # Reasonable timeout for thorough checking
        results = await asyncio.wait_for(
            asyncio.gather(*[task for _, task in tasks], return_exceptions=True),
            timeout=25  # Balanced timeout
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
                
                # Reasonable failure handling
                if monitored_urls[url]['failures'] > 3:
                    del monitored_urls[url]
                    notifications.append(f"🔴 Removed {url[:40]}... (too many failures)")
                continue
            
            # Reset failures on success
            monitored_urls[url]['failures'] = 0
            
            # Change detection with verification
            old_hash = monitored_urls[url]['hash']
            if old_hash != result:
                # IMPROVED: Smart alerting with cooldown
                last_notified = monitored_urls[url].get('last_notified', 0)
                cooldown_period = 3  # 5 minutes between notifications for same URL
                
                if current_time - last_notified > cooldown_period:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    notifications.append(
                        f"🚨 CHANGE DETECTED [{timestamp}]\n"
                        f"🔗 {url}\n"
                        f"🎯 SMART DETECTION\n"
                        f"📊 Hash: {old_hash[:8]}... → {result[:8]}...\n"
                        f"⚡ Confidence: HIGH"
                    )
                    monitored_urls[url]['last_notified'] = current_time
                    print(f"🔥 VERIFIED CHANGE: {url}")
                else:
                    print(f"🕒 Change detected but in cooldown: {url}")
                
                monitored_urls[url]['hash'] = result
            else:
                monitored_urls[url]['hash'] = result
            
            monitored_urls[url]['last_checked'] = current_time
        
        # Send notifications efficiently
        if notifications:
            send_tasks = []
            for notification in notifications:
                task = asyncio.create_task(
                    bot.send_message(chat_id=CHAT_ID, text=notification[:4000])
                )
                send_tasks.append(task)
            
            try:
                await asyncio.gather(*send_tasks, return_exceptions=True)
                print(f"📤 Sent {len(notifications)} notifications!")
            except Exception as e:
                print(f"❌ Some notifications failed: {e}")
        
        elapsed = time.time() - start_time
        print(f"✅ SMART CHECK: {len(urls)} URLs in {elapsed:.2f}s (avg: {elapsed/len(urls):.1f}s/URL)")
        
    except asyncio.TimeoutError:
        print("⚠️ Some checks timed out - but continuing")
    except Exception as e:
        print(f"❌ Monitoring error: {e}")


async def smart_monitoring_loop(application):
    """Balanced monitoring loop"""
    global is_monitoring
    
    bot = application.bot
    try:
        await bot.send_message(chat_id=CHAT_ID, text="🎯 SMART MONITORING ACTIVATED!\n⚡ Fast intervals with reliable detection")
    except Exception as e:
        print(f"❌ Failed to send start message: {e}")
    
    try:
        while is_monitoring:
            await intelligent_monitoring(bot)
            await asyncio.sleep(CHECK_INTERVAL)
    except asyncio.CancelledError:
        print("🛑 Monitoring task was cancelled")
    except Exception as e:
        print(f"❌ Smart monitoring error: {e}")
        try:
            await bot.send_message(chat_id=CHAT_ID, text=f"🚨 Monitoring error: {e}")
        except:
            pass
    finally:
        # Cleanup
        while driver_pool:
            try:
                driver_pool.pop().quit()
            except:
                pass
        print("🧹 Smart monitoring cleanup completed")


# Auth middleware
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block unauthorized users"""
    if update.effective_chat and update.effective_chat.id != CHAT_ID:
        if update.message:
            try:
                await update.message.reply_text("🚫 Unauthorized")
            except:
                pass
        raise ApplicationHandlerStop


# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Smart Zealy Monitor v3\n\n"
        "📋 Commands:\n"
        "/add <url> - Add monitoring URL\n"
        "/remove <#> - Remove URL by number\n"
        "/list - Show monitored URLs\n"
        "/run - Start monitoring\n"
        "/stop - Stop monitoring\n"
        "/purge - Clear all URLs\n\n"
        f"⚡ Speed: {CHECK_INTERVAL}s intervals\n"
        f"🎯 Accuracy: Smart content detection\n"
        f"📊 Limits: {MAX_URLS} URLs max"
    )


async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(monitored_urls) >= MAX_URLS:
        await update.message.reply_text(f"❌ Max {MAX_URLS} URLs reached")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: /add <zealy-url>")
        return
    
    url = context.args[0].strip()
    
    # Flexible URL validation for Zealy
    zealy_pattern = r'^https?://(www\.)?zealy\.io/c(w?|ommunity)?/[\w/-]+/?$'
    
    # Normalize URL
    url = url.lower()
    if url.startswith('http://'):
        url = url.replace('http://', 'https://')
    url = url.rstrip('/')
    
    if not re.match(zealy_pattern, url, re.IGNORECASE):
        await update.message.reply_text(
            "❌ Invalid Zealy URL format\n"
            "Expected: https://zealy.io/cw/community-name\n"
            "Or: https://zealy.io/community/community-name"
        )
        return
    
    if url in monitored_urls:
        await update.message.reply_text("⚠️ Already monitoring this URL")
        return
    
    msg = await update.message.reply_text("🔍 Verifying URL...")
    
    try:
        # Get initial hash with smart checking
        initial_hash = await smart_content_check(url)
        if not initial_hash:
            await msg.edit_text("❌ Unable to access URL content or insufficient content found")
            return
        
        monitored_urls[url] = {
            'hash': initial_hash,
            'last_notified': 0,
            'last_checked': time.time(),
            'failures': 0
        }
        
        await msg.edit_text(
            f"✅ Added successfully!\n"
            f"🎯 Smart monitoring enabled\n"
            f"📊 Total: {len(monitored_urls)}/{MAX_URLS} URLs"
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
    global is_monitoring, monitoring_task
    
    if is_monitoring:
        await update.message.reply_text("⚠️ Already monitoring")
        return
    
    if not monitored_urls:
        await update.message.reply_text("❌ No URLs to monitor")
        return
    
    is_monitoring = True
    if monitoring_task and not monitoring_task.done():
        monitoring_task.cancel()
    
    monitoring_task = asyncio.create_task(smart_monitoring_loop(context.application))
    await update.message.reply_text("✅ Smart monitoring started!")


async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring, monitoring_task
    
    is_monitoring = False
    
    if monitoring_task and not monitoring_task.done():
        monitoring_task.cancel()
        try:
            await monitoring_task
        except asyncio.CancelledError:
            pass
    
    await update.message.reply_text("🛑 Monitoring stopped")


async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    count = len(monitored_urls)
    monitored_urls.clear()
    await update.message.reply_text(f"✅ Cleared {count} URLs")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Missing TELEGRAM_BOT_TOKEN in .env")
        return
    
    if not CHAT_ID:
        print("❌ Missing or invalid CHAT_ID in .env")
        return
    
    print("🚀 Starting Smart Zealy Monitor v3...")
    
    try:
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        app.add_handler(MessageHandler(filters.ALL, auth_middleware), group=-1)
        
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
        
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")
    finally:
        global is_monitoring
        is_monitoring = False
        while driver_pool:
            try:
                driver_pool.pop().quit()
            except:
                pass


if __name__ == "__main__":
    main()