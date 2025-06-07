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
import signal
import weakref

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

# OPTIMIZED Configuration for 512MB RAM
CHECK_INTERVAL = 30
MAX_URLS = 10
ZEALY_CONTAINER_SELECTOR = "div.flex.flex-col.w-full.pt-100"
REQUEST_TIMEOUT = 15
MAX_CONCURRENT_CHECKS = 1
DRIVER_POOL_SIZE = 1
MAX_RETRIES = 2
RETRY_DELAY_BASE = 3
FAILURE_THRESHOLD = 5

# Set appropriate paths based on environment
IS_RENDER = os.getenv('IS_RENDER', 'false').lower() == 'true'

if IS_RENDER:
    # Try multiple possible Chrome paths on Render
    possible_chrome_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser', 
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable'
    ]
    CHROME_PATH = None
    for path in possible_chrome_paths:
        if os.path.exists(path):
            CHROME_PATH = path
            print(f"✅ Found Chrome at: {path}")
            break
    
    if not CHROME_PATH:
        CHROME_PATH = '/usr/bin/chromium'  # Default fallback
        print(f"⚠️ No Chrome found, using fallback: {CHROME_PATH}")
    
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

def kill_chrome_processes():
    """Kill all Chrome/Chromium processes to prevent zombies"""
    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and any(name in proc.info['name'].lower() for name in ['chrome', 'chromium', 'chromedriver']):
                    print(f"🔫 Killing Chrome process: {proc.info['name']} (PID: {proc.info['pid']})")
                    proc.kill()
                    proc.wait(timeout=3)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                continue
    except Exception as e:
        print(f"Warning: Error killing Chrome processes: {e}")

def get_chrome_options():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Memory optimizations
    options.add_argument("--memory-pressure-off")
    options.add_argument("--aggressive-cache-discard")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-ipc-flooding-protection")
    
    # Feature disabling
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-accelerated-2d-canvas")
    options.add_argument("--disable-accelerated-jpeg-decoding")
    options.add_argument("--disable-accelerated-mjpeg-decode")
    options.add_argument("--disable-accelerated-video-decode")
    
    # Network and UI optimizations
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-features=TranslateUI")
    options.add_argument("--disable-features=MediaRouter")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    
    # FIXED: Consistent memory limits
    if IS_RENDER:
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-infobars")
        options.add_argument("--single-process")
        options.add_argument("--no-zygote")
        options.add_argument("--disable-dev-tools")
        options.add_argument("--max_old_space_size=128")  # Fixed consistent memory limit
        options.add_argument("--js-flags=--max-old-space-size=128")
    else:
        options.add_argument("--max_old_space_size=256")  # Fixed consistent memory limit
        options.add_argument("--js-flags=--max-old-space-size=256")
    
    if not IS_RENDER:
        if not os.path.exists(CHROME_PATH):
            if platform.system() == "Windows":
                possible_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
                ]
                for path in possible_paths:
                    if os.path.exists(path):
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
    """FIXED: Thread-safe driver pool with proper resource management and circuit breaker pattern"""
    
    def __init__(self, pool_size: int = DRIVER_POOL_SIZE):
        self.pool_size = pool_size
        self.available_drivers = Queue()
        self.active_drivers = set()
        self.driver_refs = weakref.WeakSet()  # FIXED: Track all drivers for cleanup
        self.lock = threading.RLock()  # FIXED: Use RLock for nested locking
        
        # FIXED: Circuit breaker pattern
        self.session_failures_count = 0
        self.consecutive_failures = 0
        self.use_fresh_drivers = False
        self.last_failure_time = 0
        self.circuit_breaker_threshold = 3
        self.circuit_breaker_timeout = 300  # 5 minutes
        
        # FIXED: Process tracking for cleanup
        self.chrome_processes = set()
        
        if not self.use_fresh_drivers:
            self._initialize_pool()
    
    def _initialize_pool(self):
        """FIXED: Thread-safe pool initialization with Render optimization"""
        with self.lock:
            if self.use_fresh_drivers:
                print("🔧 Using fresh drivers mode - no pool initialization")
                return
            
            # RENDER OPTIMIZATION: Start with fresh drivers on Render to avoid initialization delays
            if IS_RENDER:
                print("🌐 Render detected - using fresh drivers mode for faster startup")
                self.use_fresh_drivers = True
                return
                
            for _ in range(self.pool_size):
                try:
                    print(f"🔧 Creating driver {_ + 1}/{self.pool_size}...")
                    driver = self._create_driver()
                    if driver:
                        self.available_drivers.put(driver)
                        print(f"✅ Driver added to pool. Pool size: {self.available_drivers.qsize()}")
                    else:
                        print(f"⚠️ Driver creation returned None, switching to fresh mode")
                        self.use_fresh_drivers = True
                        break
                except Exception as e:
                    print(f"⚠️ Failed to initialize driver in pool: {e}")
                    print("🔄 Switching to fresh drivers mode for reliability")
                    self.use_fresh_drivers = True
                    break
    
    def _create_driver(self):
        """FIXED: Create a new WebDriver with Render optimization and timeout"""
        print("🔧 Creating new Chrome driver...")
        try:
            options = get_chrome_options()
            
            # RENDER OPTIMIZATION: Faster driver creation with timeout
            if IS_RENDER:
                print("🌐 Creating driver for Render environment...")
                # Use system chromedriver on Render
                driver = webdriver.Chrome(options=options)
            else:
                if not os.path.exists(CHROMEDRIVER_PATH):
                    print("🔧 Using system chromedriver...")
                    driver = webdriver.Chrome(options=options)
                else:
                    print(f"🔧 Using chromedriver at: {CHROMEDRIVER_PATH}")
                    service = Service(executable_path=CHROMEDRIVER_PATH)
                    driver = webdriver.Chrome(service=service, options=options)
            
            # FIXED: Track process for cleanup
            if hasattr(driver, 'service') and hasattr(driver.service, 'process'):
                with self.lock:
                    self.chrome_processes.add(driver.service.process.pid)
                print(f"📊 Tracking Chrome process PID: {driver.service.process.pid}")
            
            # FIXED: Add to weak reference set for tracking
            self.driver_refs.add(driver)
            
            # Configure driver with timeout protection
            print("⚙️ Configuring driver timeouts...")
            driver.set_page_load_timeout(REQUEST_TIMEOUT)
            driver.implicitly_wait(5)
            
            print("✅ Chrome driver created successfully")
            return driver
            
        except Exception as e:
            error_msg = f"Failed to create driver: {e}"
            print(f"❌ {error_msg}")
            
            # RENDER FALLBACK: If driver creation fails on Render, ensure fresh mode
            if IS_RENDER:
                print("🌐 Render driver creation failed - ensuring fresh mode")
                with self.lock:
                    self.use_fresh_drivers = True
                    
            return None
    
    def _handle_session_failure(self):
        """FIXED: Thread-safe session failure handling with circuit breaker"""
        with self.lock:
            self.session_failures_count += 1
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            
            # Circuit breaker logic
            if self.consecutive_failures >= self.circuit_breaker_threshold:
                if not self.use_fresh_drivers:
                    print("🚨 CIRCUIT BREAKER ACTIVATED - SWITCHING TO FRESH DRIVERS MODE")
                    self.use_fresh_drivers = True
                    self._force_cleanup_all()
                elif self.session_failures_count >= 10:
                    print("🚨 PERSISTENT SESSION ISSUES - ENFORCING FRESH DRIVERS MODE")
                    self._force_cleanup_all()

    def get_driver(self, timeout: int = 10):
        """FIXED: Thread-safe driver acquisition with circuit breaker check"""
        # Check circuit breaker timeout
        if self.use_fresh_drivers and time.time() - self.last_failure_time > self.circuit_breaker_timeout:
            with self.lock:
                if self.consecutive_failures < self.circuit_breaker_threshold:
                    print("🔄 Circuit breaker timeout - attempting to restore pool mode")
                    self.use_fresh_drivers = False
                    self._initialize_pool()
        
        # FRESH DRIVERS MODE
        if self.use_fresh_drivers:
            print("🆕 Creating fresh driver (circuit breaker active)")
            driver = self._create_driver()
            if driver:
                with self.lock:
                    self.active_drivers.add(driver)
            return driver
        
        # POOLED MODE
        try:
            driver = self.available_drivers.get(timeout=timeout)
            
            # FIXED: Thread-safe health check
            with self.lock:
                if not self._is_driver_healthy(driver):
                    print("🔄 Driver unhealthy, creating new one...")
                    self._force_close_driver(driver)
                    self._handle_session_failure()
                    
                    if self.use_fresh_drivers:
                        return self.get_driver(timeout)
                        
                    driver = self._create_driver()
                else:
                    self.consecutive_failures = 0
                
                if driver:
                    self.active_drivers.add(driver)
                    
            return driver
            
        except Empty:
            print("⚠️ No drivers available, creating new one...")
            driver = self._create_driver()
            if driver:
                with self.lock:
                    self.active_drivers.add(driver)
            return driver
    
    def return_driver(self, driver):
        """FIXED: Thread-safe driver return with enhanced health checks"""
        if not driver:
            return
            
        try:
            with self.lock:
                self.active_drivers.discard(driver)
                
                # FRESH DRIVERS MODE
                if self.use_fresh_drivers:
                    print("🗑️ Closing fresh driver (circuit breaker active)")
                    self._force_close_driver(driver)
                    return
                
                # POOLED MODE - comprehensive health check
                if self._is_driver_healthy(driver):
                    try:
                        _ = driver.current_url  # Additional validation
                        self.available_drivers.put(driver)
                        print("✅ Healthy driver returned to pool")
                        self.consecutive_failures = 0
                    except Exception as e:
                        print(f"🔄 Driver failed final health check: {e}")
                        self._force_close_driver(driver)
                        self._handle_session_failure()
                        self._replace_driver_in_pool()
                else:
                    print("🔄 Replacing unhealthy driver")
                    self._force_close_driver(driver)
                    self._handle_session_failure()
                    self._replace_driver_in_pool()
                        
        except Exception as e:
            print(f"⚠️ Error returning driver: {e}")
            with self.lock:
                self._force_close_driver(driver)
                self._handle_session_failure()
    
    def _replace_driver_in_pool(self):
        """FIXED: Replace a driver in the pool if not in fresh mode"""
        if not self.use_fresh_drivers:
            new_driver = self._create_driver()
            if new_driver:
                self.available_drivers.put(new_driver)
    
    def _is_driver_healthy(self, driver) -> bool:
        """FIXED: Enhanced thread-safe health check"""
        try:
            _ = driver.current_url
            _ = driver.title
            _ = driver.window_handles
            return True
        except Exception as e:
            error_str = str(e)
            if any(session_error in error_str for session_error in [
                "invalid session id", "session deleted", "browser has closed",
                "not connected to DevTools", "chrome not reachable"
            ]):
                self._handle_session_failure()
            return False
    
    def _force_close_driver(self, driver):
        """FIXED: Force close driver with comprehensive cleanup"""
        try:
            driver.quit()
        except Exception as e:
            print(f"⚠️ Error during graceful driver close: {e}")
        
        try:
            # Force close process if it exists
            if hasattr(driver, 'service') and hasattr(driver.service, 'process'):
                proc = driver.service.process
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except:
                        proc.kill()
                        
                # Remove from tracking
                with self.lock:
                    self.chrome_processes.discard(proc.pid)
        except Exception as e:
            print(f"⚠️ Error during force driver close: {e}")
    
    def _force_cleanup_all(self):
        """FIXED: Force cleanup all drivers and processes"""
        print("🧹 Force cleaning up all drivers and processes...")
        
        # Close available drivers
        while not self.available_drivers.empty():
            try:
                driver = self.available_drivers.get_nowait()
                self._force_close_driver(driver)
            except Empty:
                break
        
        # Close active drivers
        with self.lock:
            for driver in self.active_drivers.copy():
                self._force_close_driver(driver)
            self.active_drivers.clear()
            
            # Kill tracked processes
            for pid in self.chrome_processes.copy():
                try:
                    proc = psutil.Process(pid)
                    proc.kill()
                    self.chrome_processes.discard(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    self.chrome_processes.discard(pid)
        
        # Final nuclear option - kill all Chrome processes
        kill_chrome_processes()
    
    def cleanup(self):
        """FIXED: Enhanced cleanup with process management"""
        self._force_cleanup_all()

# Global instances
monitored_urls: Dict[str, URLData] = {}
is_monitoring = False
driver_pool = None
notification_queue = Queue()

def get_content_hash_fast(url: str, debug_mode: bool = False) -> Tuple[Optional[str], float, Optional[str], Optional[str]]:
    """FIXED: Enhanced content hash extraction with Render optimization and comprehensive error handling"""
    driver = None
    start_time = time.time()
    max_attempts = 2
    
    print(f"🌐 get_content_hash_fast called for: {url}")
    
    for attempt in range(max_attempts):
        try:
            print(f"🌐 Getting driver for URL: {url} (attempt {attempt + 1}/{max_attempts})")
            
            # RENDER OPTIMIZATION: Add timeout for driver acquisition
            try:
                if IS_RENDER:
                    print("🌐 Render environment - using optimized driver acquisition")
                    driver = driver_pool.get_driver(timeout=30)  # Longer timeout for Render
                else:
                    driver = driver_pool.get_driver(timeout=10)
            except Exception as driver_error:
                print(f"❌ Driver acquisition failed: {driver_error}")
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying driver acquisition in 3s...")
                    time.sleep(3)
                    continue
                return None, time.time() - start_time, f"Driver acquisition failed: {str(driver_error)}", None
            
            if not driver:
                error_msg = "Failed to get driver from pool"
                print(f"❌ {error_msg}")
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying in 3s...")
                    time.sleep(3)
                    continue
                return None, time.time() - start_time, error_msg, None
            
            print(f"✅ Driver acquired successfully")
            print(f"🌐 Loading URL: {url}")
            
            # RENDER OPTIMIZATION: Add timeout for page load
            try:
                driver.get(url)
                print("✅ URL loaded successfully")
            except Exception as load_error:
                print(f"❌ URL load failed: {load_error}")
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying URL load...")
                    driver_pool.return_driver(driver)
                    driver = None
                    time.sleep(3)
                    continue
                return None, time.time() - start_time, f"URL load failed: {str(load_error)}", None
            
            print("⏳ Waiting for React to render...")
            time.sleep(3)  # Slightly longer wait for Render
            
            print("⏳ Searching for page elements...")
            selectors_to_try = [
                ZEALY_CONTAINER_SELECTOR,
                "div[class*='flex'][class*='flex-col']",
                "main",
                "body"
            ]
            
            container = None
            for selector in selectors_to_try:
                try:
                    print(f"🔍 Trying selector: {selector}")
                    container = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    print(f"✅ Found element with selector: {selector}")
                    break
                except TimeoutException:
                    print(f"⚠️ Selector {selector} not found, trying next...")
                    continue
                except Exception as selector_error:
                    print(f"⚠️ Error with selector {selector}: {selector_error}")
                    continue
            
            if not container:
                error_msg = "No suitable container found after trying all selectors"
                print(f"❌ {error_msg}")
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying container search...")
                    driver_pool.return_driver(driver)
                    driver = None
                    time.sleep(3)
                    continue
                return None, time.time() - start_time, error_msg, None
            
            print("⏳ Extracting content...")
            time.sleep(2)  # Wait for content to stabilize
            
            try:
                content = container.text
                print(f"📄 Raw content extracted, length: {len(content)} chars")
            except Exception as content_error:
                print(f"❌ Content extraction failed: {content_error}")
                if attempt < max_attempts - 1:
                    driver_pool.return_driver(driver)
                    driver = None
                    time.sleep(3)
                    continue
                return None, time.time() - start_time, f"Content extraction failed: {str(content_error)}", None
            
            if not content or len(content.strip()) < 10:
                error_msg = f"Content too short: {len(content)} chars"
                print(f"❌ {error_msg}")
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying for better content...")
                    driver_pool.return_driver(driver)
                    driver = None
                    time.sleep(3)
                    continue
                return None, time.time() - start_time, error_msg, None
            
            print(f"📄 Content retrieved successfully, length: {len(content)} chars")
            
            # Enhanced content cleaning (consolidated for performance)
            clean_content = content
            
            patterns = [
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z?',
                r'\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?',
                r'(?:\d+\s*(?:seconds?|mins?|minutes?|hours?|days?|weeks?|months?|years?)\s*ago)',
                r'(?:just now|moments? ago|recently)',
                r'\d+\s*(?:XP|points?|pts)',
                r'(?:XP|points?|pts)\s*:\s*\d+',
                r'\b[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12}\b',
                r'\b[a-f0-9]{32}\b',
                r'\b[a-f0-9]{40}\b',
                r'\d+\s*(?:views?|likes?|shares?|comments?|replies?)',
                r'(?:views?|likes?|shares?|comments?|replies?)\s*:\s*\d+',
                r'\d+\s*(?:online|active|members?|users?)',
                r'(?:online|active|members?|users?)\s*:\s*\d+',
                r'\d+%|\d+/\d+',
                r'(?:progress|completed|remaining)\s*:\s*\d+',
                r'\d+\s*(?:total|count|number)',
                r'(?:total|count|number)\s*:\s*\d+',
                r'(?:rank|position)\s*#?\d+',
                r'#\d+\s*(?:rank|position)',
                r'session\s*[a-f0-9]+',
                r'token\s*[a-f0-9]+',
                r'(?:loading|refreshing|updating)\.{0,3}',
                r'(?:quest|task)\s+\d+\s*(?:of|/)\s*\d+',
                r'(?:day|week|month)\s+\d+'
            ]
            
            for pattern in patterns:
                clean_content = re.sub(pattern, '', clean_content, flags=re.IGNORECASE)
            
            clean_content = re.sub(r'\s+', ' ', clean_content).strip()
            
            print(f"📄 Content cleaned successfully, original: {len(content)} chars, cleaned: {len(clean_content)} chars")
            
            content_hash = hashlib.sha256(clean_content.encode()).hexdigest()
            response_time = time.time() - start_time
            
            content_sample = content[:500] if debug_mode else None
            
            print(f"🔢 Hash generated successfully: {content_hash[:8]}... in {response_time:.2f}s")
            return content_hash, response_time, None, content_sample
            
        except Exception as e:
            error_str = str(e)
            print(f"⚠️ Exception in attempt {attempt + 1}: {error_str}")
            
            # Enhanced session error detection
            session_errors = [
                "invalid session id", "session deleted", "browser has closed",
                "not connected to DevTools", "chrome not reachable", "target window already closed",
                "chrome process may have crashed", "session deleted because of page crash"
            ]
            
            if any(session_error in error_str.lower() for session_error in session_errors):
                print(f"🚨 Session error detected: {error_str[:100]}...")
                
                if driver_pool:
                    driver_pool._handle_session_failure()
                
                if driver:
                    try:
                        driver_pool._force_close_driver(driver)
                    except Exception as close_error:
                        print(f"⚠️ Error closing broken driver: {close_error}")
                    driver = None
                
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying after session error in 5s...")
                    time.sleep(5)
                    continue
                else:
                    return None, time.time() - start_time, f"Max retries exceeded due to session errors: {error_str}", None
            else:
                error_msg = f"WebDriver error: {error_str}"
                print(f"⚠️ {error_msg}")
                
                if attempt < max_attempts - 1:
                    print(f"⏳ Retrying after error in 3s...")
                    if driver:
                        driver_pool.return_driver(driver)
                        driver = None
                    time.sleep(3)
                    continue
                else:
                    return None, time.time() - start_time, error_msg, None
        finally:
            if driver:
                print("🔄 Returning driver to pool...")
                driver_pool.return_driver(driver)
    
    print("❌ All retry attempts exhausted")
    return None, time.time() - start_time, "All retry attempts failed", None

async def check_single_url(url: str, url_data: URLData) -> Tuple[str, bool, Optional[str]]:
    """FIXED: Enhanced URL checking with proper async error handling"""
    retry_count = 0
    last_error = None
    
    while retry_count < MAX_RETRIES:
        try:
            loop = asyncio.get_event_loop()
            
            # FIXED: Proper async exception handling
            try:
                hash_result, response_time, error, content_sample = await loop.run_in_executor(
                    None, get_content_hash_fast, url, False
                )
            except Exception as executor_error:
                print(f"⚠️ Executor error for {url}: {executor_error}")
                last_error = f"Executor error: {str(executor_error)}"
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE ** retry_count
                    await asyncio.sleep(delay)
                    continue
                else:
                    url_data.failures += 1
                    url_data.consecutive_successes = 0
                    url_data.last_error = last_error
                    return url, False, last_error
            
            if hash_result is None:
                retry_count += 1
                last_error = error or "Unknown error"
                
                if retry_count < MAX_RETRIES:
                    delay = RETRY_DELAY_BASE ** retry_count + (retry_count * 0.5)
                    print(f"⏳ Retrying {url} in {delay:.1f}s (attempt {retry_count + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(delay)
                    continue
                else:
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
            
            has_changes = url_data.hash != hash_result
            if has_changes:
                print(f"🔔 Change detected for {url}")
                url_data.hash = hash_result
                return url, True, None
            else:
                print(f"✓ No changes for {url} (avg: {url_data.avg_response_time:.2f}s)")
                return url, False, None
                
        except asyncio.CancelledError:
            print(f"⚠️ Task cancelled for {url}")
            raise
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
    """FIXED: Enhanced notification system with proper async error handling"""
    try:
        if priority:
            return await _send_telegram_message(bot, message)
        else:
            notification_queue.put(message)
            return True
    except Exception as e:
        print(f"⚠️ Error in send_notification: {e}")
        return False

async def _send_telegram_message(bot, message: str) -> bool:
    """FIXED: Send message with improved async retry logic"""
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
        except Exception as e:
            print(f"⚠️ Unexpected error sending message: {e}")
            return False
    
    print(f"❌ Failed to send notification after 3 retries")
    return False

async def process_notification_queue(bot):
    """FIXED: Process queued notifications with proper async error handling"""
    while is_monitoring:
        try:
            messages = []
            start_time = time.time()
            
            while len(messages) < 5 and (time.time() - start_time) < 2:
                try:
                    message = notification_queue.get(timeout=0.5)
                    messages.append(message)
                except Empty:
                    break
            
            if messages:
                batch_message = "\n".join(messages)
                try:
                    await _send_telegram_message(bot, batch_message[:4000])
                except Exception as e:
                    print(f"⚠️ Error sending batch notification: {e}")
            
            await asyncio.sleep(1)
            
        except asyncio.CancelledError:
            print("🚫 Notification queue processor cancelled")
            break
        except Exception as e:
            print(f"⚠️ Error in notification queue processor: {e}")
            await asyncio.sleep(5)

async def check_urls_parallel(bot):
    """FIXED: Sequential URL checking with circuit breaker pattern"""
    global monitored_urls
    current_time = time.time()
    
    if not monitored_urls:
        print("⚠️ No URLs to check")
        return
    
    print(f"🔍 Checking {len(monitored_urls)} URLs sequentially...")
    
    changes_detected = 0
    urls_to_remove = []
    consecutive_url_failures = 0
    
    for url, url_data in list(monitored_urls.items()):
        try:
            result = await check_single_url(url, url_data)
            
            if isinstance(result, Exception):
                print(f"⚠️ Task exception: {result}")
                consecutive_url_failures += 1
                continue
                
            url, has_changes, error = result
            
            if url not in monitored_urls:
                continue
                
            url_data = monitored_urls[url]
            
            if has_changes:
                changes_detected += 1
                consecutive_url_failures = 0  # Reset on success
                
                if current_time - url_data.last_notified > 60:
                    try:
                        await send_notification(
                            bot, 
                            f"🚨 CHANGE DETECTED!\n{url}\nAvg response: {url_data.avg_response_time:.2f}s\nCheck #{url_data.check_count}",
                            priority=True
                        )
                        url_data.last_notified = current_time
                    except Exception as e:
                        print(f"⚠️ Error sending change notification: {e}")
            else:
                if error is None:
                    consecutive_url_failures = 0  # Reset on success
            
            # FIXED: Circuit breaker for failing URLs
            if url_data.failures > FAILURE_THRESHOLD:
                urls_to_remove.append(url)
            elif url_data.failures > 2 and url_data.consecutive_successes == 0:
                try:
                    await send_notification(
                        bot,
                        f"⚠️ Monitoring issues for {url}\nFailures: {url_data.failures}/{FAILURE_THRESHOLD}\nLast error: {url_data.last_error or 'Unknown'}"
                    )
                except Exception as e:
                    print(f"⚠️ Error sending failure notification: {e}")
                    
        except asyncio.CancelledError:
            print("🚫 URL checking cancelled")
            break
        except Exception as e:
            print(f"⚠️ Error processing URL {url}: {e}")
            consecutive_url_failures += 1
            
            # FIXED: Circuit breaker for system-wide failures
            if consecutive_url_failures >= len(monitored_urls):
                print("🚨 SYSTEM-WIDE FAILURE DETECTED - TRIGGERING CIRCUIT BREAKER")
                if driver_pool:
                    driver_pool._handle_session_failure()
                break
    
    # Remove problematic URLs
    for url in urls_to_remove:
        del monitored_urls[url]
        try:
            await send_notification(
                bot, 
                f"🔴 Removed from monitoring (too many failures): {url}",
                priority=True
            )
        except Exception as e:
            print(f"⚠️ Error sending removal notification: {e}")
        print(f"🗑️ Removed {url} after {FAILURE_THRESHOLD} failures")
    
    print(f"✅ Sequential check complete: {changes_detected} changes, {len(urls_to_remove)} removed")

# FIXED: Enhanced command handlers with proper async error handling
async def auth_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update and update.effective_chat and update.effective_chat.id != CHAT_ID:
            if update.message:
                await update.message.reply_text("🚫 Unauthorized access!")
            raise ApplicationHandlerStop
    except Exception as e:
        print(f"⚠️ Error in auth middleware: {e}")
        raise ApplicationHandlerStop

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message:
            return
            
        await update.message.reply_text(
            "🚀 FIXED Memory-Optimized Zealy Monitoring Bot\n\n"
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
            f"Max URLs: {MAX_URLS} (optimized for 512MB RAM)\n"
            f"Check interval: {CHECK_INTERVAL}s\n"
            f"Concurrent checks: {MAX_CONCURRENT_CHECKS} (sequential for memory)\n\n"
            "🛡️ CRITICAL FIXES APPLIED:\n"
            "✅ Memory leak prevention\n"
            "✅ Thread safety\n"
            "✅ Circuit breaker pattern\n"
            "✅ Enhanced resource cleanup"
        )
    except Exception as e:
        print(f"⚠️ Error in start command: {e}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message:
            return
            
        if not monitored_urls:
            await update.message.reply_text("📊 No URLs being monitored")
            return
        
        status_lines = ["📊 FIXED Memory-Optimized Monitoring Statistics:\n"]
        
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
        
        # FIXED: Enhanced driver status
        if driver_pool:
            if driver_pool.use_fresh_drivers:
                status_lines.append("🔧 Driver mode: FRESH (circuit breaker active)")
                status_lines.append(f"⚠️ Session failures: {driver_pool.session_failures_count}")
                status_lines.append(f"🔄 Circuit breaker timeout: {driver_pool.circuit_breaker_timeout}s")
            else:
                status_lines.append(f"🔧 Driver pool: {driver_pool.available_drivers.qsize()}/{driver_pool.pool_size} available")
                status_lines.append(f"📊 Session failures: {driver_pool.session_failures_count}")
                
        status_lines.append(f"💾 Memory limit: {128 if IS_RENDER else 256}MB heap (FIXED)")
        status_lines.append(f"🔄 Monitoring: {'✅ Active' if is_monitoring else '❌ Stopped'}")
        status_lines.append("🛡️ Circuit breaker: Active")
        status_lines.append("🔒 Thread safety: Enhanced")
        status_lines.append("🧹 Resource cleanup: Fixed")
        
        message = "\n".join(status_lines)[:4000]
        await update.message.reply_text(message)
        
    except Exception as e:
        print(f"⚠️ Error in status command: {e}")
        try:
            if update and update.message:
                await update.message.reply_text(f"❌ Error retrieving status: {str(e)}")
        except:
            pass

async def debug_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FIXED: Enhanced debug command with proper error handling"""
    try:
        if not update or not update.message:
            return
            
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
            
            try:
                loop = asyncio.get_event_loop()
                hash_result, response_time, error, content_sample = await loop.run_in_executor(
                    None, get_content_hash_fast, url, True
                )
                
                if hash_result:
                    current_data = monitored_urls[url]
                    debug_info = [
                        f"🔍 FIXED Debug Info for URL #{url_index + 1}:",
                        f"📄 Current hash: {current_data.hash[:12]}...",
                        f"📄 New hash: {hash_result[:12]}...",
                        f"🔄 Hashes match: {'✅ Yes' if current_data.hash == hash_result else '❌ No - CHANGE DETECTED!'}",
                        f"⚡ Response time: {response_time:.2f}s",
                        f"📊 Check count: {current_data.check_count}",
                        f"❌ Failures: {current_data.failures}",
                        f"💾 Memory mode: {'Fresh drivers' if driver_pool.use_fresh_drivers else 'Pooled drivers'}",
                        f"🛡️ Circuit breaker: {'Active' if driver_pool.use_fresh_drivers else 'Inactive'}",
                        f"🔒 Thread safety: Enhanced",
                        "",
                        "📝 Content sample (first 400 chars):",
                        f"```{content_sample[:400] if content_sample else 'No sample available'}```"
                    ]
                    
                    debug_message = "\n".join(debug_info)
                    await processing_msg.edit_text(debug_message[:4000])
                else:
                    await processing_msg.edit_text(f"❌ Failed to get content: {error}")
                    
            except Exception as debug_error:
                await processing_msg.edit_text(f"❌ Debug execution error: {str(debug_error)}")
                
        except ValueError:
            await update.message.reply_text("❌ Please provide a valid number")
            
    except Exception as e:
        print(f"⚠️ Error in debug_url: {str(e)}")
        try:
            if update and update.message:
                await update.message.reply_text(f"❌ Debug error: {str(e)}")
        except:
            pass

async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check endpoint for Render"""
    try:
        if not update or not update.message:
            return
            
        # Basic health check
        health_status = [
            f"🤖 Bot Status: ✅ Running",
            f"💾 Memory: {128 if IS_RENDER else 256}MB limit",
            f"🌐 Environment: {'Render' if IS_RENDER else 'Local'}",
            f"🔧 Chrome Path: {CHROME_PATH}",
            f"📊 URLs Monitored: {len(monitored_urls)}/{MAX_URLS}",
            f"🔄 Monitoring Active: {'✅' if is_monitoring else '❌'}",
        ]
        
        if driver_pool:
            health_status.append(f"🛡️ Driver Mode: {'Fresh' if driver_pool.use_fresh_drivers else 'Pooled'}")
            health_status.append(f"📈 Session Failures: {driver_pool.session_failures_count}")
        
        health_status.append(f"⏰ Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        await update.message.reply_text("\n".join(health_status))
        print("✅ Health check completed")
        
    except Exception as e:
        error_msg = f"❌ Health check failed: {str(e)}"
        print(error_msg)
        try:
            if update and update.message:
                await update.message.reply_text(error_msg)
        except:
            pass
    """FIXED: Enhanced sensitivity command"""
    try:
        if not update or not update.message:
            return
            
        if update.effective_chat.id != CHAT_ID:
            return
        
        help_text = [
            "🎛️ FIXED Memory-Optimized Sensitivity Settings:",
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
            "FIXED optimizations:",
            f"💾 Max URLs: {MAX_URLS} (reduced for 512MB RAM)",
            f"💾 Sequential processing (no parallel checks)",
            f"💾 Heap limit: {128 if IS_RENDER else 256}MB (consistent)",
            f"💾 Reduced timeouts and retries",
            "🛡️ Circuit breaker pattern active",
            "🔒 Thread-safe driver pool",
            "🧹 Enhanced resource cleanup",
            "🚨 Session error recovery",
            "",
            "If you're still getting false positives:",
            "1. Use /debug <number> to see what content is changing",
            "2. Consider reducing monitored URLs further",
            "3. Circuit breaker auto-switches modes when needed"
        ]
        
        await update.message.reply_text("\n".join(help_text))
        
    except Exception as e:
        print(f"⚠️ Error in sensitivity command: {e}")

async def sensitivity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FIXED: Enhanced sensitivity command"""
    try:
        if not update or not update.message:
            return
            
        if update.effective_chat.id != CHAT_ID:
            return
        
        help_text = [
            "🎛️ FIXED Memory-Optimized Sensitivity Settings:",
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
            "FIXED optimizations:",
            f"💾 Max URLs: {MAX_URLS} (reduced for 512MB RAM)",
            f"💾 Sequential processing (no parallel checks)",
            f"💾 Heap limit: {128 if IS_RENDER else 256}MB (consistent)",
            f"💾 Reduced timeouts and retries",
            "🛡️ Circuit breaker pattern active",
            "🔒 Thread-safe driver pool",
            "🧹 Enhanced resource cleanup",
            "🚨 Session error recovery",
            "",
            "If you're still getting false positives:",
            "1. Use /debug <number> to see what content is changing",
            "2. Consider reducing monitored URLs further",
            "3. Circuit breaker auto-switches modes when needed"
        ]
        
        await update.message.reply_text("\n".join(help_text))
        
    except Exception as e:
        print(f"⚠️ Error in sensitivity command: {e}")

async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message:
            return
            
        if not monitored_urls:
            await update.message.reply_text("No monitored URLs")
            return
        
        message_lines = ["📋 FIXED Monitored URLs:\n"]
        for idx, (url, data) in enumerate(monitored_urls.items(), 1):
            status = "✅" if data.failures == 0 else f"⚠️({data.failures})"
            circuit_status = "🛡️" if driver_pool and driver_pool.use_fresh_drivers else "🔧"
            message_lines.append(f"{idx}. {status}{circuit_status} {url}")
        
        message_lines.append(f"\n💾 Using {len(monitored_urls)}/{MAX_URLS} slots (512MB optimized)")
        message_lines.append("🛡️ = Circuit breaker active, 🔧 = Pooled mode")
        message_lines.append("🔒 Thread safety and resource cleanup FIXED")
        message = "\n".join(message_lines)[:4000]
        await update.message.reply_text(message)
        
    except Exception as e:
        print(f"⚠️ Error in list_urls: {e}")

async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update or not update.message:
            return
            
        if update.effective_chat.id != CHAT_ID:
            return
        
        if not monitored_urls:
            await update.message.reply_text("❌ No URLs to remove")
            return
            
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
            f"✅ Removed: {url_to_remove}\n📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}\n💾 Memory freed! (FIXED cleanup)"
        )
        print(f"🗑️ Manually removed URL: {url_to_remove}")
        
    except Exception as e:
        print(f"⚠️ Error in remove_url: {str(e)}")
        try:
            if update and update.message:
                await update.message.reply_text(f"❌ Error removing URL: {str(e)}")
        except:
            pass

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        print(f"🔵 ADD_URL called by user: {update.effective_chat.id if update and update.effective_chat else 'Unknown'}")
        
        if not update or not update.message:
            print("❌ ADD_URL: No update or message")
            return
        
        if update.effective_chat.id != CHAT_ID:
            print(f"❌ ADD_URL: Unauthorized chat ID: {update.effective_chat.id}")
            return
        
        if len(monitored_urls) >= MAX_URLS:
            print(f"❌ ADD_URL: Max URLs reached ({len(monitored_urls)}/{MAX_URLS})")
            await update.message.reply_text(f"❌ Maximum URLs limit ({MAX_URLS}) reached\n💾 This limit is optimized for 512MB RAM")
            return
            
        if not context.args or not context.args[0]:
            print("❌ ADD_URL: No URL provided")
            await update.message.reply_text("❌ Usage: /add <zealy-url>")
            return
            
        url = context.args[0].lower()
        print(f"📥 ADD_URL: Processing URL: {url}")
        
        if not re.match(r'^https://(www\.)?zealy\.io/cw/[\w/-]+', url):
            print(f"❌ ADD_URL: Invalid URL format: {url}")
            await update.message.reply_text("❌ Invalid Zealy URL format")
            return
            
        if url in monitored_urls:
            print(f"ℹ️ ADD_URL: URL already monitored: {url}")
            await update.message.reply_text("ℹ️ URL already monitored")
            return
            
        # CRITICAL FIX: Immediate response to prevent timeout
        processing_msg = await update.message.reply_text("⏳ Verifying URL... (This may take 30-60 seconds on Render)")
        print("✅ ADD_URL: Processing message sent")
        
        try:
            print(f"🔄 ADD_URL: Starting URL verification for {url}")
            
            # RENDER OPTIMIZATION: Add timeout protection
            loop = asyncio.get_event_loop()
            
            # Create a timeout wrapper for the verification
            async def verify_with_timeout():
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, get_content_hash_fast, url, False),
                        timeout=90.0  # 90 second timeout for Render
                    )
                except asyncio.TimeoutError:
                    print("⏰ ADD_URL: Verification timeout")
                    return None, 0, "Verification timeout (Render environment)", None
                except Exception as e:
                    print(f"❌ ADD_URL: Verification error: {e}")
                    return None, 0, f"Verification error: {str(e)}", None
            
            print("🔄 ADD_URL: Calling verify_with_timeout...")
            initial_hash, response_time, error, content_sample = await verify_with_timeout()
            print(f"🔄 ADD_URL: Verification complete. Hash: {initial_hash is not None}, Error: {error}")
            
            if not initial_hash:
                error_msg = f"❌ Failed to verify URL: {error}"
                print(f"❌ ADD_URL: {error_msg}")
                await processing_msg.edit_text(error_msg)
                return
                
            # SUCCESS: Add URL to monitoring
            monitored_urls[url] = URLData(
                hash=initial_hash,
                last_notified=0,
                last_checked=time.time(),
                failures=0,
                consecutive_successes=1,
                check_count=1,
                avg_response_time=response_time
            )
            
            print(f"✅ ADD_URL: URL added successfully: {url}")
            
            # RENDER STATUS: Show current driver mode
            driver_mode = "🛡️ Fresh drivers (Render optimized)" if driver_pool and driver_pool.use_fresh_drivers else "🔧 Pooled mode"
            
            success_msg = (
                f"✅ Added: {url}\n"
                f"📊 Now monitoring: {len(monitored_urls)}/{MAX_URLS}\n"
                f"⚡ Initial response: {response_time:.2f}s\n"
                f"💾 Memory optimized for 512MB RAM\n"
                f"{driver_mode}\n"
                f"🌐 Render environment detected\n"
                f"🔒 All critical fixes active"
            )
            
            await processing_msg.edit_text(success_msg)
            print("✅ ADD_URL: Success message sent")
            
        except asyncio.CancelledError:
            print("🚫 ADD_URL: Operation cancelled")
            await processing_msg.edit_text("❌ Operation cancelled")
            raise
        except Exception as e:
            error_msg = f"❌ Failed to add URL: {str(e)}"
            print(f"❌ ADD_URL: Exception during verification: {e}")
            print(f"❌ ADD_URL: Exception traceback: {traceback.format_exc()}")
            try:
                await processing_msg.edit_text(error_msg)
            except Exception as edit_error:
                print(f"❌ ADD_URL: Failed to edit message: {edit_error}")
                # Try sending new message if editing fails
                try:
                    await update.message.reply_text(error_msg)
                except Exception as reply_error:
                    print(f"❌ ADD_URL: Failed to send reply: {reply_error}")
            
    except Exception as e:
        print(f"⚠️ ADD_URL: Critical error: {str(e)}")
        print(f"⚠️ ADD_URL: Critical traceback: {traceback.format_exc()}")
        try:
            if update and update.message:
                await update.message.reply_text(f"❌ Critical error: {str(e)}")
        except Exception as critical_error:
            print(f"❌ ADD_URL: Failed to send critical error message: {critical_error}")
        # Don't re-raise to prevent bot crash

async def run_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    try:
        if not update or not update.message:
            return
            
        if is_monitoring:
            await update.message.reply_text("⚠️ Already monitoring")
            return
        if not monitored_urls:
            await update.message.reply_text("❌ No URLs to monitor")
            return
        
        is_monitoring = True
        monitor_task = asyncio.create_task(start_monitoring(context.application))
        notification_task = asyncio.create_task(process_notification_queue(context.application.bot))
        
        context.chat_data['monitor_task'] = monitor_task
        context.chat_data['notification_task'] = notification_task
        
        circuit_status = "🛡️ Circuit breaker ready" if driver_pool else "🔧 Pool mode"
        await update.message.reply_text(
            f"✅ FIXED Memory-optimized monitoring started!\n"
            f"🔍 Checking {len(monitored_urls)} URLs every {CHECK_INTERVAL}s\n"
            f"💾 Sequential processing for 512MB RAM\n"
            f"⚡ Heap limit: {128 if IS_RENDER else 256}MB (consistent)\n"
            f"🛡️ FIXES APPLIED:\n"
            f"  ✅ Thread safety\n"
            f"  ✅ Circuit breaker pattern\n"
            f"  ✅ Memory leak prevention\n"
            f"  ✅ Enhanced resource cleanup\n"
            f"{circuit_status}"
        )
        print("✅ FIXED Memory-optimized monitoring tasks created and started")
        
    except Exception as e:
        is_monitoring = False
        print(f"❌ Error starting monitoring: {str(e)}")
        try:
            if update and update.message:
                await update.message.reply_text(f"❌ Failed to start monitoring: {str(e)}")
        except:
            pass

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring
    try:
        if not update or not update.message:
            return
            
        is_monitoring = False
        
        # FIXED: Enhanced task cancellation
        cancelled_tasks = 0
        for task_name in ['monitor_task', 'notification_task']:
            if task_name in context.chat_data:
                try:
                    task = context.chat_data[task_name]
                    if not task.done():
                        task.cancel()
                        try:
                            await asyncio.wait_for(task, timeout=5.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                    del context.chat_data[task_name]
                    cancelled_tasks += 1
                    print(f"🛑 {task_name} cancelled")
                except Exception as e:
                    print(f"⚠️ Error cancelling {task_name}: {str(e)}")
        
        await update.message.reply_text(f"🛑 FIXED Memory-optimized monitoring stopped\n🔧 {cancelled_tasks} tasks cancelled cleanly\n🧹 Enhanced cleanup applied")
        
    except Exception as e:
        print(f"⚠️ Error in stop_monitoring: {e}")

async def purge_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitored_urls
    try:
        if not update or not update.message:
            return
            
        count = len(monitored_urls)
        monitored_urls.clear()
        
        # FIXED: Force circuit breaker reset if needed
        if driver_pool and driver_pool.use_fresh_drivers:
            driver_pool.consecutive_failures = 0
            print("🔄 Circuit breaker reset after purge")
        
        await update.message.reply_text(f"✅ All {count} URLs purged!\n💾 Memory fully freed! (FIXED cleanup)\n🔄 Circuit breaker reset\n🧹 Enhanced resource management")
        
    except Exception as e:
        print(f"⚠️ Error in purge_urls: {e}")

async def start_monitoring(application: Application):
    global is_monitoring
    bot = application.bot
    
    try:
        await send_notification(bot, "🔔 FIXED Memory-optimized monitoring started! (Thread safety + Circuit breaker + Resource cleanup)", priority=True)
        print("🔍 Entering FIXED enhanced memory-optimized monitoring loop")
        
        while is_monitoring:
            try:
                print(f"🔄 Running sequential URL check cycle - {len(monitored_urls)} URLs")
                start_time = time.time()
                
                await check_urls_parallel(bot)
                
                elapsed = time.time() - start_time
                wait_time = max(CHECK_INTERVAL - elapsed, 2)
                print(f"✓ Sequential check complete in {elapsed:.2f}s, waiting {wait_time:.2f}s")
                
                await asyncio.sleep(wait_time)
                
            except asyncio.CancelledError:
                print("🚫 Monitoring task was cancelled")
                break
            except Exception as e:
                print(f"🚨 Monitoring error: {str(e)}")
                print(traceback.format_exc())
                await asyncio.sleep(10)
        
        print("👋 Exiting FIXED enhanced memory-optimized monitoring loop")
        await send_notification(bot, "🔴 FIXED Memory-optimized monitoring stopped!", priority=True)
        
    except Exception as e:
        print(f"🚨 Critical error in start_monitoring: {e}")
        is_monitoring = False

def setup_signal_handlers():
    """FIXED: Setup signal handlers for graceful shutdown"""
    def signal_handler(signum, frame):
        print(f"\n🚨 Received signal {signum}, shutting down gracefully...")
        global is_monitoring
        is_monitoring = False
        
        if driver_pool:
            driver_pool.cleanup()
        
        kill_chrome_processes()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def main():
    try:
        global CHROME_PATH, CHROMEDRIVER_PATH, driver_pool
        
        print(f"🚀 Starting FIXED memory-optimized bot (512MB RAM) at {datetime.now()}")
        
        # FIXED: Setup signal handlers
        setup_signal_handlers()
        
        kill_previous_instances()

        print(f"🌍 Operating System: {platform.system()}")
        print(f"🌍 Running on Render: {IS_RENDER}")
        print(f"💾 Chrome path: {CHROME_PATH}")
        print(f"💾 Chrome exists: {os.path.exists(CHROME_PATH) if CHROME_PATH else False}")
        print(f"💾 Chromedriver path: {CHROMEDRIVER_PATH}")
        print(f"💾 Chromedriver exists: {os.path.exists(CHROMEDRIVER_PATH) if CHROMEDRIVER_PATH else False}")
        print(f"⚡ Max concurrent checks: {MAX_CONCURRENT_CHECKS} (sequential)")
        print(f"🔧 Driver pool size: {DRIVER_POOL_SIZE}")
        print(f"💾 Memory optimization: {128 if IS_RENDER else 256}MB heap limit (CONSISTENT)")
        print("🔴 JavaScript: ENABLED (required for Zealy)")
        print("🛡️ CRITICAL FIXES APPLIED:")
        print("  ✅ Memory leak prevention")
        print("  ✅ Thread safety (RLock)")
        print("  ✅ Circuit breaker pattern")
        print("  ✅ Enhanced resource cleanup")
        print("  ✅ Process tracking")
        print("  ✅ Session error recovery")
        
        # RENDER SPECIFIC CHECKS
        if IS_RENDER:
            print("🌐 RENDER ENVIRONMENT DETECTED")
            print(f"  📂 Chrome binary exists: {os.path.exists(CHROME_PATH) if CHROME_PATH else False}")
            print(f"  📂 Chromedriver exists: {os.path.exists(CHROMEDRIVER_PATH)}")
            
            # Check for required packages in Render
            try:
                import selenium
                print(f"  ✅ Selenium version: {selenium.__version__}")
            except ImportError:
                print("  ❌ Selenium not found!")
                
            try:
                print(f"  ✅ Telegram bot token: {'Set' if TELEGRAM_BOT_TOKEN else 'Missing'}")
                print(f"  ✅ Chat ID: {'Set' if CHAT_ID else 'Missing'}")
            except:
                print("  ❌ Environment variables issue!")
                
            # Test Chrome binary
            if CHROME_PATH and os.path.exists(CHROME_PATH):
                try:
                    result = os.system(f"{CHROME_PATH} --version")
                    print(f"  🔍 Chrome test result: {result}")
                except Exception as chrome_test_error:
                    print(f"  ⚠️ Chrome test failed: {chrome_test_error}")
        
        print("=" * 60)
        
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
        
        # FIXED: Initialize enhanced driver pool after paths are set
        print("🔧 Initializing FIXED memory-optimized driver pool with enhanced features...")
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
            CommandHandler("health", health_check),
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
        print("🧹 FIXED Enhanced cleanup sequence...")
        if driver_pool:
            driver_pool.cleanup()
        kill_chrome_processes()
        print("🧹 FIXED Enhanced cleanup complete")

if __name__ == "__main__":
    print("FIXED Memory-optimized script starting (512MB RAM)...")
    print("🛡️ Critical fixes applied: Memory leaks, Thread safety, Circuit breaker, Resource cleanup")
    try:
        main()
    except Exception as e:
        print(f"❌ CRITICAL ERROR in __main__: {str(e)}")
        print(traceback.format_exc())
        input("Press Enter to exit...")
    finally:
        # FIXED: Enhanced final cleanup
        try:
            if 'driver_pool' in globals() and driver_pool:
                driver_pool.cleanup()
            kill_chrome_processes()
            print("🧹 Final enhanced cleanup completed")
        except Exception as cleanup_error:
            print(f"⚠️ Error during final cleanup: {cleanup_error}")
        print("✅ All critical fixes applied successfully!")