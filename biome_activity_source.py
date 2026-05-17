import json, requests, time, os, threading, re, webbrowser, random, keyboard, webbrowser, pyautogui, pytesseract
import sys, atexit, fcntl, subprocess, signal, shutil, asyncio
from pathlib import Path
import traceback
import pyscreenrec
import pywinctl as gw

from tkinter import ttk
from tkinter import messagebox, filedialog
from PIL import Image, ImageTk
from datetime import datetime, timedelta

# Safer defaults for embedded web GUI rendering on Linux.
# You can override this if you need to.
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
os.environ.setdefault("QTWEBENGINE_DISABLE_GPU", "1")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("GDK_BACKEND", "x11")

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

APP_VERSION = "noteab-macro-linux"
APP_DIR = Path(__file__).resolve().parent
os.chdir(APP_DIR)
LOCK_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "noteab-macro-linux.lock"

def acquire_single_instance_lock():
    """Prevent two macro instances from fighting over mouse/keyboard control."""
    lock_handle = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_handle.write(str(os.getpid()))
        lock_handle.flush()
        atexit.register(lambda: lock_handle.close())
        return lock_handle
    except BlockingIOError:
        try:
            messagebox.showwarning(
                "Macro already running",
                "Another macro instance is already running. Close it before opening another copy."
            )
        except Exception:
            print("Another macro instance is already running.")
        sys.exit(1)

def split_webhook_urls(value):
    if not value:
        return []
    pieces = re.split(r"[\n,; ]+", str(value).strip())
    return [p for p in pieces if p.startswith("https://") or p.startswith("http://")]

def discord_mention(raw):
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if raw in ("@everyone", "@here"):
        return raw
    if raw.startswith("<@") or raw.startswith("<@&"):
        return raw
    if raw.startswith("&") and raw[1:].isdigit():
        return f"<@&{raw[1:]}>"
    if raw.isdigit():
        return f"<@{raw}>"
    return raw


class _HeadlessVar:
    """Small tkinter Variable replacement used by the web UI mode."""
    def __init__(self, value=None):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value

class _HeadlessRoot:
    def __init__(self):
        self._title = "noteab-macro-linux"
        self.style = _HeadlessVar("darkly")
        self.style.name = "darkly"
    def title(self, value=None):
        if value is not None:
            self._title = value
        return self._title
    def after(self, ms, callback=None, *args):
        if callback:
            threading.Timer(ms / 1000.0, callback, args=args).start()
    def destroy(self):
        pass


class SnippingWidget:
    def __init__(self, root, config_key=None, callback=None):
        self.root = root
        self.config_key = config_key
        self.callback = callback
        self.snipping_window = None
        self.begin_x = None
        self.begin_y = None
        self.end_x = None
        self.end_y = None

    def start(self):
        self.snipping_window = ttk.Toplevel(self.root)
        self.snipping_window.attributes('-fullscreen', True)
        self.snipping_window.attributes('-alpha', 0.3)
        self.snipping_window.configure(bg="lightblue")
        
        self.snipping_window.bind("<Button-1>", self.on_mouse_press)
        self.snipping_window.bind("<B1-Motion>", self.on_mouse_drag)
        self.snipping_window.bind("<ButtonRelease-1>", self.on_mouse_release)

        self.canvas = ttk.Canvas(self.snipping_window, bg="lightblue", highlightthickness=0)
        self.canvas.pack(fill=ttk.BOTH, expand=True)

    def on_mouse_press(self, event):
        self.begin_x = event.x
        self.begin_y = event.y
        self.canvas.delete("selection_rect")

    def on_mouse_drag(self, event):
        self.end_x, self.end_y = event.x, event.y
        self.canvas.delete("selection_rect")
        self.canvas.create_rectangle(self.begin_x, self.begin_y, self.end_x, self.end_y,
                                      outline="white", width=2, tag="selection_rect")

    def on_mouse_release(self, event):
        self.end_x = event.x
        self.end_y = event.y

        x1, y1 = min(self.begin_x, self.end_x), min(self.begin_y, self.end_y)
        x2, y2 = max(self.begin_x, self.end_x), max(self.begin_y, self.end_y)

        self.capture_region(x1, y1, x2, y2)
        self.snipping_window.destroy()

    def capture_region(self, x1, y1, x2, y2):
        if self.config_key:
            region = [x1, y1, x2 - x1, y2 - y1]
            print(f"Region for '{self.config_key}' set to {region}")
            
            if self.callback:
                self.callback(region)
                
class BiomePresence():
    def __init__(self):
        self.instance_lock = acquire_single_instance_lock()
        self.logs_dir = self.find_sober_log_dir()

        self.config = self.load_config()
        self.auras_data = self.load_auras_json()
        self.biome_data = self.load_biome_data()
        self.sync_config_with_biomes()
        
        self.current_biome = None
        self.last_sent = {biome: datetime.min for biome in self.biome_data}
        
        self.biome_counts = self.config.get("biome_counts", {})
        for biome in self.biome_data.keys():
            if biome not in self.biome_counts:
                self.biome_counts[biome] = 0
                
        self.start_time = None
        self.saved_session = self.parse_session_time(self.config.get("session_time", "0:00:00"))
        
        self.last_position = 0
        self.detection_running = False
        self.detection_thread = None
        self.lock = threading.Lock()
        self.logs = self.load_logs()
        
        #item use
        self.last_br_time = datetime.min
        self.last_sc_time = datetime.min
        self.last_mt_time = datetime.min
        self.last_anti_afk_time = datetime.min
        self.anti_afk_stop_event = threading.Event()
        self.anti_afk_thread = None
        self._recording_state = None
        self.remote_bot_thread = None
        self.remote_bot_obj = None
        self.remote_bot_loop = None
        self.remote_bot_status = "stopped"
        self.remote_bot_error = ""
        self.remote_worker_running = False
        try:
            import queue as _queue
            self.remote_command_queue = _queue.Queue()
        except Exception:
            self.remote_command_queue = None
        self._remote_queue_thread = None
        self._remote_running = False
        self._egg_collecting = False
        
        # Buff variables
        self.buff_vars = {}
        self.buff_amount_vars = {}
    
        # GUI state. In webview mode these are lightweight shims; in fallback mode
        # tkinter/ttkbootstrap replaces them with real widgets/variables
        self.root = _HeadlessRoot()
        self.variables = {}
        self.init_web_gui_state()
        
        # aura detection
        self.last_aura_found = None

        # start gui
        self.init_gui()


    def init_web_gui_state(self):
        """Initialize variable attributes used by the macro loops without requiring Tk widgets."""
        self.variables = {biome: _HeadlessVar(self.config.get("biome_notifier", {}).get(biome, "None")) for biome in self.biome_data}
        self.biome_ping_vars = {biome: _HeadlessVar(self.config.get("biome_ping_ids", {}).get(biome, "")) for biome in self.biome_data}
        self.webhook_url_entry = _HeadlessVar(self.config.get("webhook_url", ""))
        self.webhook_user_id_entry = _HeadlessVar(self.config.get("webhook_user_id", ""))
        self.private_server_link_entry = _HeadlessVar(self.config.get("private_server_link", ""))
        self.br_var = _HeadlessVar(self.config.get("biome_randomizer", False))
        self.br_duration_var = _HeadlessVar(self.config.get("br_duration", "30"))
        self.sc_var = _HeadlessVar(self.config.get("strange_controller", False))
        self.sc_duration_var = _HeadlessVar(self.config.get("sc_duration", "15"))
        self.mt_var = _HeadlessVar(self.config.get("merchant_teleporter", False))
        self.mt_duration_var = _HeadlessVar(self.config.get("mt_duration", "1"))
        self.auto_record_var = _HeadlessVar(self.config.get("auto_record", False))
        self.record_duration_var = _HeadlessVar(self.config.get("record_duration", "60"))
        self.record_fps_var = _HeadlessVar(self.config.get("record_fps", "25"))
        self.auto_pop_glitched_var = _HeadlessVar(self.config.get("auto_pop_glitched", False))
        self.click_delay_var = _HeadlessVar(self.config.get("inventory_click_delay", "0"))
        self.ping_mari_var = _HeadlessVar(self.config.get("ping_mari", False))
        self.mari_user_id_var = _HeadlessVar(self.config.get("mari_user_id", ""))
        self.ping_jester_var = _HeadlessVar(self.config.get("ping_jester", False))
        self.jester_user_id_var = _HeadlessVar(self.config.get("jester_user_id", ""))
        self.enable_aura_detection_var = _HeadlessVar(self.config.get("enable_aura_detection", False))
        self.send_minimum_var = _HeadlessVar(self.config.get("send_minimum", "1000000"))
        self.ping_minimum_var = _HeadlessVar(self.config.get("ping_minimum", "10000000"))
        self.aura_user_id_var = _HeadlessVar(self.config.get("aura_user_id", ""))
        self.macro_idle_mode_var = _HeadlessVar(self.config.get("enable_idle_mode", False))
        self.anti_afk_var = _HeadlessVar(self.config.get("anti_afk", False))
        self.anti_afk_interval_var = _HeadlessVar(self.config.get("anti_afk_interval", "60"))
        self.auto_reconnect_var = _HeadlessVar(self.config.get("auto_reconnect", False))
        self.auto_roblox_fullscreen_var = _HeadlessVar(self.config.get("auto_roblox_fullscreen", False))
        self.enable_obby_var = _HeadlessVar(self.config.get("enable_obby_path", False))
        self.enable_potion_crafting_var = _HeadlessVar(self.config.get("enable_potion_crafting", False))
        self.potion_recording_name_var = _HeadlessVar(self.config.get("potion_recording_name", "Potion"))
        self.collect_egg_var = _HeadlessVar(self.config.get("collect_easter_egg", False))
        self.egg_collect_interval_var = _HeadlessVar(self.config.get("egg_collect_interval_min", "30"))
        self.remote_access_var = _HeadlessVar(self.config.get("remote_access", False))
        self.remote_bot_token_var = _HeadlessVar(self.config.get("remote_bot_token", ""))
        self.remote_allowed_user_id_var = _HeadlessVar(self.config.get("remote_allowed_user_id", ""))
        self.buff_vars = {}
        self.buff_amount_vars = {}
        for buff, value in self.config.get("auto_buff_glitched", {}).items():
            try:
                enabled, amount = value
            except Exception:
                enabled, amount = bool(value), 1
            self.buff_vars[buff] = _HeadlessVar(enabled)
            self.buff_amount_vars[buff] = _HeadlessVar(str(amount))

    def apply_config_dict(self, new_config):
        """Save config from the React/webview GUI and sync runtime variables."""
        if not isinstance(new_config, dict):
            return False
        self.config.update(new_config)
        self.sync_config_with_biomes()
        self.init_web_gui_state()
        with open("config.json", "w") as file:
            json.dump(self.config, file, indent=4)
        return True

    def sync_config_with_biomes(self):
        """automatically add newly-known biomes to config maps."""
        self.config.setdefault("biome_notifier", {})
        self.config.setdefault("biome_ping_ids", {})
        self.config.setdefault("biome_counts", {})
        for biome in self.biome_data:
            self.config["biome_notifier"].setdefault(biome, "None")
            self.config["biome_ping_ids"].setdefault(biome, "")
            self.config["biome_counts"].setdefault(biome, 0)

    def get_webhook_urls(self):
        urls = split_webhook_urls(self.config.get("webhook_url", ""))
        extra = self.config.get("webhook_urls", [])
        if isinstance(extra, str):
            urls.extend(split_webhook_urls(extra))
        elif isinstance(extra, list):
            for item in extra:
                urls.extend(split_webhook_urls(item))
        # removing duplicates
        seen = set()
        unique = []
        for url in urls:
            if url not in seen:
                unique.append(url)
                seen.add(url)
        return unique

    def post_json_to_webhooks(self, payload):
        urls = self.get_webhook_urls()
        if not urls:
            print("Webhook URL is missing/not included in config.json")
            return False
        ok = False
        for url in urls:
            try:
                response = requests.post(url, json=payload, timeout=20)
                response.raise_for_status()
                ok = True
            except requests.exceptions.RequestException as e:
                print(f"Failed to send webhook to {url}: {e}")
        return ok

    def append_log(self, message):
        stamped = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        self.logs.append(stamped)
        if hasattr(self, "logs_text"):
            self.display_logs()
            self.logs_text.see(ttk.END)
        self.save_logs()
        self.update_status_tab()
       
    def load_logs(self):
        if os.path.exists('macro_logs.txt'):
            with open('macro_logs.txt', 'r') as file:
                lines = file.read().splitlines()
                return lines
        return []
    
    def load_biome_data(self):
        biomes_paths = [
            "biomes_data.json",
            "source_code/biomes_data.json",
            os.path.join(os.path.dirname(__file__), "biomes_data.json"),
            os.path.join(os.path.dirname(__file__), "source_code/biomes_data.json")
        ]
        
        default_biome_data = {
            "WINDY": {
                "color": "0x9ae5ff",
                "duration": 120,
                "thumbnail_url": "https://i.postimg.cc/6qPH4wy6/image.png"
            },
            "RAINY": {
                "color": "0x027cbd",
                "duration": 120,
                "thumbnail_url": "https://static.wikia.nocookie.net/sol-rng/images/e/ec/Rainy.png"
            },
            "SNOWY": {
                "color": "0xDceff9",
                "duration": 120,
                "thumbnail_url": "https://static.wikia.nocookie.net/sol-rng/images/d/d7/Snowy_img.png"
            },
            "SAND STORM": {
                "color": "0x8F7057",
                "duration": 600,
                "thumbnail_url": "https://i.postimg.cc/3JyL25Kz/image.png"
            },
            "HELL": {
                "color": "0xff4719",
                "duration": 660,
                "thumbnail_url": "https://i.postimg.cc/hGC5xNyY/image.png"
            },
            "STARFALL": {
                "color": "0x011ab7",
                "duration": 600,
                "thumbnail_url": "https://i.postimg.cc/1t0dY4J8/image.png"
            },
            "CORRUPTION": {
                "color": "0x6d32a8",
                "duration": 660,
                "thumbnail_url": "https://i.postimg.cc/ncZQ84Dh/image.png"
            },
            "GRAVEYARD": {
                "color": "0x707070",
                "duration": 230,
                "thumbnail_url": "https://i.postimg.cc/nrVLLcx2/image.png"
            },
            "PUMPKIN MOON": {
                "color": "0xff8a1c",
                "duration": 230,
                "thumbnail_url": "https://i.postimg.cc/6TJtvWJF/image.png"
            },
            "NULL": {
                "color": "0x838383",
                "duration": 90,
                "thumbnail_url": "https://static.wikia.nocookie.net/sol-rng/images/f/fc/NULLLL.png"
            },
            "GLITCHED": {
                "color": "0xbfff00",
                "duration": 164,
                "thumbnail_url": "https://i.postimg.cc/W3Lhtn5g/image.png"
            }
        }
        
        try:
            for path in biomes_paths:
                if os.path.exists(path):
                    with open(path, "r") as file:
                        biome_data = json.load(file)
                        return biome_data
        except Exception as e:
            print(f"Error loading biomes_data.json: {e}")
            self.error_logging(e, f"Error loading biomes_data.json")
        
        with open("biomes_data.json", "w") as file:
            json.dump(default_biome_data, file, indent=4)
            print("Default biomes_data.json created.")
        
        return default_biome_data

    def error_logging(self, exception, custom_message=None, max_log_size=3 * 1024 * 1024):
        log_file = "error_logs.txt"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_type = type(exception).__name__
        error_message = str(exception)
        stack_trace = traceback.format_exc()

        if not os.path.exists(log_file):
            with open(log_file, "w") as log:
                log.write("Error Log File Created\n")
                log.write("-" * 40 + "\n")

        if os.path.exists(log_file) and os.path.getsize(log_file) > max_log_size:
            with open(log_file, "r") as log:
                lines = log.readlines()
            with open(log_file, "w") as log:
                log.writelines(lines[-1000:])

        with open(log_file, "a") as log:
            log.write(f"\n[{timestamp}] ERROR LOG\n")
            log.write(f"Error Type: {error_type}\n")
            log.write(f"Error Message: {error_message}\n")
            if custom_message:
                log.write(f"Custom Message: {custom_message}\n")
            log.write(f"Traceback:\n{stack_trace}\n")
            log.write("-" * 40 + "\n")

        print(f"Error logged to {log_file}.")
    
    def save_logs(self):
        with open('macro_logs.txt', 'w') as file:
            for log in self.logs:
                file.write(log + "\n")
                
    def save_config(self):
        try:
            with open("config.json", "r") as file:
                config = json.load(file)
        except FileNotFoundError:
            config = {}

        auto_buff_glitched = config.get("auto_buff_glitched", self.config.get("auto_buff_glitched", {}))
        session_time = self.get_total_session_time()

        config.update({
            "webhook_url": self.webhook_url_entry.get(),
            "webhook_user_id": self.webhook_user_id_entry.get(),
            "private_server_link": self.private_server_link_entry.get(),
            "biome_notifier": {biome: self.variables[biome].get() for biome in self.biome_data},
            "biome_ping_ids": {biome: self.biome_ping_vars[biome].get() for biome in self.biome_data} if hasattr(self, "biome_ping_vars") else self.config.get("biome_ping_ids", {}),
            "biome_counts": self.biome_counts,
            "session_time": session_time,
            "biome_randomizer": self.br_var.get(),
            "br_duration": self.br_duration_var.get(),
            "strange_controller": self.sc_var.get(),
            "sc_duration": self.sc_duration_var.get(),
            "auto_record": self.auto_record_var.get(),
            "record_duration": self.record_duration_var.get(),
            "record_fps": self.record_fps_var.get(),
            "auto_pop_glitched": self.auto_pop_glitched_var.get(),
            "auto_pop_biomes": self.config.get("auto_pop_biomes", ["GLITCHED", "CYBERSPACE", "DREAMSPACE"]),
            "auto_buff_glitched": {
                buff: (self.buff_vars[buff].get(), int(self.buff_amount_vars[buff].get()))
                for buff in self.buff_vars
            },
            "selected_theme": getattr(getattr(getattr(self.root, "style", None), "theme", None), "name", self.config.get("selected_theme", "darkly")),
            "dont_ask_for_update": self.config.get("dont_ask_for_update", False),
            "merchant_teleporter": self.mt_var.get(),
            "mt_duration": self.mt_duration_var.get(),
            "Mari_Items": self.config.get("Mari_Items", {}),
            "Jester_Items": self.config.get("Jester_Items", {}),
            "ping_mari": self.ping_mari_var.get(),
            "mari_user_id": self.mari_user_id_var.get(),
            "ping_jester": self.ping_jester_var.get(),
            "jester_user_id": self.jester_user_id_var.get(),
            "merchant_open_button": self.config.get("merchant_open_button", [579, 906]),
            "merchant_dialogue_box": self.config.get("merchant_dialogue_box", [1114, 796]),
            "purchase_amount_button": self.config.get("purchase_amount_button", [700, 584]),
            "purchase_button": self.config.get("purchase_button", [739, 635]),
            "first_item_slot_pos": self.config.get("first_item_slot_pos", [571, 704]),
            "merchant_name_ocr_pos": self.config.get("merchant_name_ocr_pos", [746, 680, 103, 32]),
            "item_name_ocr_pos": self.config.get("item_name_ocr_pos", [728, 731, 218, 24]),
            "enable_aura_detection": self.enable_aura_detection_var.get(),
            "send_minimum": self.send_minimum_var.get(),
            "ping_minimum": self.ping_minimum_var.get(),
            "aura_user_id": self.aura_user_id_var.get(),
            
            "inventory_menu": self.config.get("inventory_menu", [36, 535]),
            "items_tab": self.config.get("items_tab", [1272, 329]),
            "search_bar": self.config.get("search_bar", [855, 358]),
            "first_item_slot": self.config.get("first_item_slot", [839, 434]),
            "amount_box": self.config.get("amount_box", [594, 570]),
            "use_button": self.config.get("use_button", [710, 573]),
            "inventory_click_delay": self.click_delay_var.get(),
            "enable_idle_mode": getattr(self, "macro_idle_mode_var", ttk.BooleanVar(value=self.config.get("enable_idle_mode", False))).get(),
            "anti_afk": getattr(self, "anti_afk_var", ttk.BooleanVar(value=self.config.get("anti_afk", False))).get(),
            "anti_afk_interval": getattr(self, "anti_afk_interval_var", ttk.StringVar(value=self.config.get("anti_afk_interval", "60"))).get(),
            "auto_reconnect": getattr(self, "auto_reconnect_var", ttk.BooleanVar(value=self.config.get("auto_reconnect", False))).get(),
            "auto_roblox_fullscreen": getattr(self, "auto_roblox_fullscreen_var", ttk.BooleanVar(value=self.config.get("auto_roblox_fullscreen", False))).get(),
            "enable_obby_path": getattr(self, "enable_obby_var", ttk.BooleanVar(value=self.config.get("enable_obby_path", False))).get(),
            "enable_potion_crafting": getattr(self, "enable_potion_crafting_var", ttk.BooleanVar(value=self.config.get("enable_potion_crafting", False))).get(),
            "potion_recording_name": getattr(self, "potion_recording_name_var", ttk.StringVar(value=self.config.get("potion_recording_name", "Potion"))).get(),
            "collect_easter_egg": getattr(self, "collect_egg_var", ttk.BooleanVar(value=self.config.get("collect_easter_egg", False))).get(),
            "egg_collect_interval_min": getattr(self, "egg_collect_interval_var", ttk.StringVar(value=self.config.get("egg_collect_interval_min", "30"))).get(),
            "remote_access": getattr(self, "remote_access_var", ttk.BooleanVar(value=self.config.get("remote_access", False))).get(),
            "remote_bot_token": getattr(self, "remote_bot_token_var", ttk.StringVar(value=self.config.get("remote_bot_token", ""))).get(),
            "remote_allowed_user_id": getattr(self, "remote_allowed_user_id_var", ttk.StringVar(value=self.config.get("remote_allowed_user_id", ""))).get()
        })

        if not config["auto_buff_glitched"]:
            config["auto_buff_glitched"] = auto_buff_glitched

        with open("config.json", "w") as file:
            json.dump(config, file, indent=4)

        self.config = config

    def load_config(self):
        config_paths = [
            "config.json",
            "source_code/config.json",
            os.path.join(os.path.dirname(__file__), "config.json"),
            os.path.join(os.path.dirname(__file__), "source_code/config.json")
        ]
        
        for path in config_paths:
            if os.path.exists(path):
                with open(path, "r") as file:
                    config = json.load(file)
                    return config
        return {"biome_counts": {}, "session_time": "0:00:00", "biome_notifier": {}, "biome_ping_ids": {}}
    
    
            
    def init_gui(self):
        if os.environ.get("NOTEAB_TK_GUI", "").lower() not in ("1", "true", "yes") and self.init_main_web_gui():
            return

        selected_theme = self.config.get("selected_theme", "solar")
        abslt_path = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(abslt_path, "NoteabBiomeTracker.ico")
        
        self.root = ttk.Window(themename=selected_theme)
        self.root.title("Noteab/Coteab Macro Linux Port (Idle)")
        self.root.geometry("760x520")
        
        try:
            self.root.iconbitmap(icon_path)
        except Exception as e:
            pass
            
        self.variables = {biome: ttk.StringVar(master=self.root, value=self.config.get("biome_notifier", {}).get(biome, "None"))
                        for biome in self.biome_data}
        self.biome_ping_vars = {biome: ttk.StringVar(master=self.root, value=self.config.get("biome_ping_ids", {}).get(biome, ""))
                        for biome in self.biome_data}

        
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True, padx=10, pady=10)

        webhook_frame = ttk.Frame(notebook)
        misc_frame = ttk.Frame(notebook)
        aura_webhook_frame = ttk.Frame(notebook)
        merchant_frame = ttk.Frame(notebook)
        credits_frame = ttk.Frame(notebook)
        stats_frame = ttk.Frame(notebook)
        status_frame = ttk.Frame(notebook)

        notebook.add(webhook_frame, text='Webhook')
        notebook.add(misc_frame, text='Misc')
        notebook.add(merchant_frame, text='Merchant')
        notebook.add(aura_webhook_frame, text='Auras')
        notebook.add(stats_frame, text='Stats')
        notebook.add(status_frame, text='Status')
        notebook.add(credits_frame, text='Credits')

        self.create_webhook_tab(webhook_frame)
        self.create_misc_tab(misc_frame)
        self.create_auras_tab(aura_webhook_frame)
        self.create_merchant_tab(merchant_frame)
        self.create_stats_tab(stats_frame)
        self.create_status_tab(status_frame)
        self.create_credit_tab(credits_frame)
        

        button_frame = ttk.Frame(self.root)
        button_frame.pack(pady=10)
        start_button = ttk.Button(button_frame, text="Start (F1)", command=self.start_detection)
        stop_button = ttk.Button(button_frame, text="Stop (F2)", command=self.stop_detection)
        start_button.pack(side='left', padx=5)
        stop_button.pack(side='left', padx=5)

        # Theme
        theme_label = ttk.Label(button_frame, text="Macro Theme:")
        theme_label.pack(side='left', padx=15)
        theme_combobox = ttk.Combobox(button_frame, values=ttk.Style().theme_names(), state="readonly")
        theme_combobox.set(selected_theme)
        theme_combobox.pack(side='left', padx=5)
        theme_combobox.bind("<<ComboboxSelected>>", lambda event: self.update_theme(theme_combobox.get()))


        try:
            keyboard.add_hotkey('F1', self.start_detection)
            keyboard.add_hotkey('F2', self.stop_detection)
        except Exception as e:
            self.append_log(f"[Hotkeys] Global F1/F2 hotkeys disabled on this Linux session: {e}")
            print(f"Global F1/F2 hotkeys disabled: {e}")
        
        self.check_for_updates()
        if self.config.get("remote_access", False):
            self.start_remote_bot()
        self.root.mainloop()


    def _webview_backend_status(self):
        """Return (ok, backend_name, message) for pywebview on Linux.

        pywebview can use either GTK/WebKit (PyGObject/gi) or Qt (qtpy + PyQt/PySide).
        Importing webview directly without a usable backend prints noisy errors like
        "No module named gi" and "cannot import qtpy". this is the fallback.
        """
        force = os.environ.get("NOTEAB_WEB_GUI", "").lower() in ("1", "true", "yes")
        try:
            import webview  # noqa: F401
        except Exception as e:
            return False, None, f"pywebview is not installed: {e}"

        gtk_error = None
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk  # noqa: F401
            # WebKit2 is the normal pywebview GTK browser widget.  Some distros
            # expose 4.1, others 4.0.  Accept either.
            try:
                gi.require_version("WebKit2", "4.1")
                from gi.repository import WebKit2  # noqa: F401
            except Exception:
                gi.require_version("WebKit2", "4.0")
                from gi.repository import WebKit2  # noqa: F401
            return True, "gtk", "GTK/WebKit backend available"
        except Exception as e:
            gtk_error = e

        qt_error = None
        try:
            import qtpy  # noqa: F401
            try:
                from qtpy import QtWebEngineWidgets  # noqa: F401
            except Exception:
                from qtpy import QtWebKitWidgets  # noqa: F401
            return True, "qt", "Qt backend available"
        except Exception as e:
            qt_error = e

        msg = (
            "No usable pywebview backend found. Falling back to the Tk GUI.\n"
            f"  GTK check failed: {gtk_error}\n"
            f"  Qt check failed: {qt_error}\n"
            "To use the webview GUI, run pip install -r requirements-webview.txtn"
        )
        if force:
            msg = "NOTEAB_WEB_GUI was requested, but " + msg[0].lower() + msg[1:]
        return False, None, msg

    def init_main_web_gui(self):
        """Launch the main Coteab/Noteab React GUI through pywebview on Linux."""
        index_path = APP_DIR / "assets" / "index.html"
        if not index_path.exists():
            return False

        ok, backend, message = self._webview_backend_status()
        if not ok:
            print(message)
            return False

        try:
            import webview
        except Exception as e:
            print(f"pywebview is not installed or could not start ({e}); falling back to Tk GUI.")
            return False

        api = MainMacroWebApi(self)
        self.webview_api = api
        try:
            window = webview.create_window(
                "noteab-macro-linux",
                index_path.as_uri(),
                js_api=api,
                width=int(self.config.get("window_width", 1180) or 1180),
                height=int(self.config.get("window_height", 760) or 760),
                min_size=(900, 600),
            )
            self.webview_window = window
            try:
                keyboard.add_hotkey('F1', self.start_detection)
                keyboard.add_hotkey('F2', self.stop_detection)
            except Exception as e:
                print(f"Global hotkeys unavailable: {e}")
            if self.config.get("remote_access", False):
                self.start_remote_bot()
            print(f"Starting imported main GUI with pywebview {backend} backend.")
            webview.start(gui=backend, debug=bool(self.config.get("webview_debug", False)))
            return True
        except Exception as e:
            print(f"Failed to launch main web GUI ({e}); falling back to Tk GUI.")
            return False

    def update_theme(self, theme_name):
        self.root.style.theme_use(theme_name)
        self.config["selected_theme"] = theme_name
        self.save_config()

    def check_for_updates(self):
        current_version = "v1.5.4-patch2.3"
        dont_ask_again = self.config.get("dont_ask_for_update", False)
        
        if dont_ask_again:
            return
        
        try:
            response = requests.get("https://api.github.com/repos/noteab/Noteab-Macro/releases/latest")
            response.raise_for_status()
            latest_release = response.json()
            latest_version = latest_release['tag_name']
            
            if latest_version != current_version:
                message = f"New update of this macro {latest_version} is available. Do you want to download the newest version?"
                if messagebox.askyesno("Update Available!!", message):
                    download_url = latest_release['assets'][0]['browser_download_url']
                    self.download_update(download_url)
                else:
                    if messagebox.askyesno("Don't Ask Again", "Would you like to stop receiving update notifications?"):
                        self.config["dont_ask_for_update"] = True
                        self.save_config()
                            
        except requests.RequestException as e:
            print(f"Failed to fetch the latest version from GitHub: {e}")
            
    def download_update(self, download_url):
        try:
            zip_filename = os.path.basename(download_url)
            save_path = filedialog.asksaveasfilename(defaultextension=".zip", initialfile=zip_filename, title="Save As")
            
            if not save_path: return
            
            response = requests.get(download_url)
            response.raise_for_status()
        
            with open(save_path, 'wb') as file:
                file.write(response.content)
            
            messagebox.showinfo("Download Complete", f"The latest version has been downloaded as {save_path}. Make sure to turn off antivirus and extract it manually.")
        except requests.RequestException as e:
            print(f"Failed to download the update: {e}")
    
    def open_biome_settings(self):
        settings_window = ttk.Toplevel(self.root)
        settings_window.title("Biome Settings")

        biomes = list(self.biome_data.keys())
        window_height = max(475, len(biomes) * 43)
        settings_window.geometry(f"400x{window_height}")

        options = ["None", "Message", "Ping"]

        ttk.Label(settings_window, text="Notify").grid(row=0, column=1, pady=2)
        ttk.Label(settings_window, text="Ping ID / Role ID").grid(row=0, column=2, pady=2)

        for i, biome in enumerate(biomes, start=1):
            ttk.Label(settings_window, text=f"{biome}:").grid(row=i, column=0, sticky="e")

            if biome not in self.variables:
                self.variables[biome] = ttk.StringVar(value="None")
            if biome not in self.biome_ping_vars:
                self.biome_ping_vars[biome] = ttk.StringVar(value="")
            dropdown = ttk.Combobox(settings_window, textvariable=self.variables[biome], values=options, state="readonly", width=10)
            dropdown.grid(row=i, column=1, pady=5)
            ping_entry = ttk.Entry(settings_window, textvariable=self.biome_ping_vars[biome], width=22)
            ping_entry.grid(row=i, column=2, padx=5, pady=5)

        ttk.Button(settings_window, text="Save", command=self.save_config).grid(row=len(biomes) + 1, column=1, pady=10)
        
    def open_buff_selections_window(self):
        buff_window = ttk.Toplevel(self.root)
        buff_window.title("Buff Selections")
        buff_window.geometry("400x300")

        buffs = {
            "Oblivion Potion": 1,
            "Heavenly Potion II": 1,
            "Fortune Potion III": 1,
            "Fortune Potion II": 1,
            "Fortune Potion I": 1,
            "Haste Potion III": 1,
            "Haste Potion II": 1,
            "Haste Potion I": 1,
            "Warp Potion": 1,
            "Strange Potion I": 1,
            "Strange Potion II": 1,
            "Stella's Candle": 1,
            "Speed Potion": 1,
            "Lucky Potion": 1,
        }

        if "auto_buff_glitched" not in self.config:
            self.config["auto_buff_glitched"] = {}

        num_columns = 2
        for i, (buff, default_amount) in enumerate(buffs.items()):
            buff_config = self.config["auto_buff_glitched"].get(buff, (False, default_amount))
            buff_enabled, buff_amount = buff_config

            self.buff_vars[buff] = ttk.BooleanVar(value=buff_enabled)
            self.buff_amount_vars[buff] = ttk.StringVar(value=str(buff_amount))

            row = i // num_columns
            col = (i % num_columns) * 2

            ttk.Checkbutton(
                buff_window, 
                text=buff, 
                variable=self.buff_vars[buff],
                command=self.save_config
            ).grid(row=row, column=col, sticky="w", padx=10, pady=5)

            entry = ttk.Entry(
                buff_window, 
                textvariable=self.buff_amount_vars[buff], 
                width=5
            )
            entry.grid(row=row, column=col + 1, padx=10, pady=5)
            entry.bind("<FocusOut>", lambda event: self.save_config())

    def create_webhook_tab(self, frame):
        ttk.Label(frame, text="Webhook URL(s):").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        self.webhook_url_entry = ttk.Entry(frame, width=50, show="*")
        self.webhook_url_entry.grid(row=0, column=1, columnspan=2, pady=5)
        self.webhook_url_entry.insert(0, self.config.get("webhook_url", ""))
        self.webhook_url_entry.bind("<FocusOut>", lambda event: self.save_config())
        
        ttk.Label(frame, text="Webhook User ID:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.webhook_user_id_entry = ttk.Entry(frame, width=50)
        self.webhook_user_id_entry.grid(row=1, column=1, columnspan=2, pady=5)
        self.webhook_user_id_entry.insert(0, self.config.get("webhook_user_id", ""))
        self.webhook_user_id_entry.bind("<FocusOut>", lambda event: self.save_config())

        ttk.Label(frame, text="Private Server Link:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.private_server_link_entry = ttk.Entry(frame, width=50)
        self.private_server_link_entry.grid(row=2, column=1, columnspan=2, pady=5)
        self.private_server_link_entry.insert(0, self.config.get("private_server_link", ""))
        self.private_server_link_entry.bind("<FocusOut>", lambda event: self.validate_and_save_ps_link())

        ttk.Button(frame, text="Configure Biomes", command=self.open_biome_settings).grid(row=3, column=1, pady=10)
    
    def create_misc_tab(self, frame):
        hp2_frame = ttk.Frame(frame)
        hp2_frame.pack(pady=10)

        # Auto Pop
        self.auto_pop_glitched_var = ttk.BooleanVar(value=self.config.get("auto_pop_glitched", False))
        auto_pop_glitched_check = ttk.Checkbutton(
            hp2_frame, 
            text="Auto Pop (rare/event biomes)", 
            variable=self.auto_pop_glitched_var,
            command=self.save_config
        )
        auto_pop_glitched_check.grid(row=0, column=0, padx=5, sticky="w")

        # Buff Selections
        buff_selections_button = ttk.Button(
            hp2_frame, 
            text="Buff Selections", 
            command=self.open_buff_selections_window
        )
        buff_selections_button.grid(row=0, column=1, padx=5)

        # Auto Record
        self.auto_record_var = ttk.BooleanVar(value=self.config.get("auto_record", False))
        auto_record_check = ttk.Checkbutton(
            hp2_frame, 
            text="Auto Record (glitched biome)", 
            variable=self.auto_record_var,
            command=self.save_config
        )
        auto_record_check.grid(row=1, column=0, padx=5, sticky="w")

        ttk.Label(hp2_frame, text="Record Duration (seconds):").grid(row=1, column=1, padx=5)
        self.record_duration_var = ttk.StringVar(value=self.config.get("record_duration", "60"))
        record_duration_entry = ttk.Entry(hp2_frame, textvariable=self.record_duration_var, width=10)
        record_duration_entry.grid(row=1, column=2, padx=5)
        record_duration_entry.bind("<FocusOut>", lambda event: self.save_config())

        ttk.Label(hp2_frame, text="FPS: (20-25 recommended)").grid(row=2, column=1, padx=5)
        self.record_fps_var = ttk.StringVar(value=self.config.get("record_fps", "25"))
        record_fps_entry = ttk.Entry(hp2_frame, textvariable=self.record_fps_var, width=10)
        record_fps_entry.grid(row=2, column=2, padx=5)
        record_fps_entry.bind("<FocusOut>", lambda event: self.save_config())

        # Biome Randomizer
        self.br_var = ttk.BooleanVar(value=self.config.get("biome_randomizer", False))
        br_check = ttk.Checkbutton(
            hp2_frame, 
            text="Biome Randomizer (BR)", 
            variable=self.br_var,
            command=self.save_config
        )
        br_check.grid(row=3, column=0, padx=5, sticky="w")

        ttk.Label(hp2_frame, text="Usage Duration (minutes):").grid(row=3, column=1, padx=5)
        self.br_duration_var = ttk.StringVar(value=self.config.get("br_duration", "30"))
        br_duration_entry = ttk.Entry(hp2_frame, textvariable=self.br_duration_var, width=10)
        br_duration_entry.grid(row=3, column=2, padx=5)
        br_duration_entry.bind("<FocusOut>", lambda event: self.save_config())

        # Strange Controller
        self.sc_var = ttk.BooleanVar(value=self.config.get("strange_controller", False))
        sc_check = ttk.Checkbutton(
            hp2_frame, 
            text="Strange Controller (SC)", 
            variable=self.sc_var,
            command=self.save_config
        )
        sc_check.grid(row=4, column=0, padx=5, sticky="w")

        ttk.Label(hp2_frame, text="Usage Duration (minutes):").grid(row=4, column=1, padx=5)
        self.sc_duration_var = ttk.StringVar(value=self.config.get("sc_duration", "15"))
        sc_duration_entry = ttk.Entry(hp2_frame, textvariable=self.sc_duration_var, width=10)
        sc_duration_entry.grid(row=4, column=2, padx=5)
        sc_duration_entry.bind("<FocusOut>", lambda event: self.save_config())

        self.mt_var = ttk.BooleanVar(value=self.config.get("merchant_teleporter", False))
        mt_check = ttk.Checkbutton(
            hp2_frame, 
            text="Merchant Teleporter (Auto Merchant)", 
            variable=self.mt_var,
            command=self.save_config
        )
        mt_check.grid(row=5, column=0, padx=5, sticky="w")

        ttk.Label(hp2_frame, text="Usage Duration (minutes):").grid(row=5, column=1, padx=5)
        self.mt_duration_var = ttk.StringVar(value=self.config.get("mt_duration", "1"))
        mt_duration_entry = ttk.Entry(hp2_frame, textvariable=self.mt_duration_var, width=10)
        mt_duration_entry.grid(row=5, column=2, padx=5)
        mt_duration_entry.bind("<FocusOut>", lambda event: self.save_config())
        
        
        # Inventory Mouse Click Delay
        ttk.Label(hp2_frame, text="Inventory Mouse Click Delay (milisecond):").grid(
            row=8, column=0, padx=5, pady=5, sticky="e"
        )
        self.click_delay_var = ttk.StringVar(value=self.config.get("inventory_click_delay", "0"))
        click_delay_entry = ttk.Entry(hp2_frame, textvariable=self.click_delay_var, width=10)
        click_delay_entry.grid(row=8, column=1, padx=5, pady=5, sticky="w")
        click_delay_entry.bind("<FocusOut>", lambda event: self.save_config())

        assign_inventory_button = ttk.Button(
            hp2_frame, 
            text="Assign Inventory Click", 
            command=self.open_assign_inventory_window
        )
        assign_inventory_button.grid(row=8, column=2, padx=5, pady=5, sticky="w")

        #  extras. these are the ones ported to linux.
        extras = ttk.LabelFrame(frame, text="Extras")
        extras.pack(fill="x", padx=8, pady=8)

        self.macro_idle_mode_var = ttk.BooleanVar(value=self.config.get("enable_idle_mode", False))
        ttk.Checkbutton(extras, text="Idle Mode (pause automated item/movement actions)", variable=self.macro_idle_mode_var, command=self.save_config).grid(row=0, column=0, columnspan=3, sticky="w", padx=5, pady=3)

        self.anti_afk_var = ttk.BooleanVar(value=self.config.get("anti_afk", False))
        ttk.Checkbutton(extras, text="Anti-AFK", variable=self.anti_afk_var, command=self.save_config).grid(row=1, column=0, sticky="w", padx=5, pady=3)
        ttk.Label(extras, text="Interval sec:").grid(row=1, column=1, sticky="e", padx=5)
        self.anti_afk_interval_var = ttk.StringVar(value=self.config.get("anti_afk_interval", "60"))
        ttk.Entry(extras, textvariable=self.anti_afk_interval_var, width=8).grid(row=1, column=2, sticky="w", padx=5)

        self.auto_reconnect_var = ttk.BooleanVar(value=self.config.get("auto_reconnect", False))
        ttk.Checkbutton(extras, text="Auto Reconnect helper (opens private-server link when requested)", variable=self.auto_reconnect_var, command=self.save_config).grid(row=2, column=0, columnspan=3, sticky="w", padx=5, pady=3)

        self.auto_roblox_fullscreen_var = ttk.BooleanVar(value=self.config.get("auto_roblox_fullscreen", False))
        ttk.Checkbutton(extras, text="Try fullscreen after focusing Roblox/Sober", variable=self.auto_roblox_fullscreen_var, command=self.save_config).grid(row=3, column=0, columnspan=3, sticky="w", padx=5, pady=3)

        self.enable_obby_var = ttk.BooleanVar(value=self.config.get("enable_obby_path", False))
        ttk.Checkbutton(extras, text="Enable Obby replay", variable=self.enable_obby_var, command=self.save_config).grid(row=4, column=0, sticky="w", padx=5, pady=3)
        ttk.Button(extras, text="Record Obby", command=lambda: self.start_recording_path()).grid(row=4, column=1, padx=5, pady=3)
        ttk.Button(extras, text="Stop+Save Obby", command=lambda: messagebox.showinfo("Recorder", self.stop_recording_path("obby", save_dir="paths"))).grid(row=4, column=2, padx=5, pady=3)
        ttk.Button(extras, text="Replay Obby", command=lambda: threading.Thread(target=self.replay_path_recording, args=("obby", "paths"), daemon=True).start()).grid(row=4, column=3, padx=5, pady=3)

        self.enable_potion_crafting_var = ttk.BooleanVar(value=self.config.get("enable_potion_crafting", False))
        ttk.Checkbutton(extras, text="Enable Potion replay", variable=self.enable_potion_crafting_var, command=self.save_config).grid(row=5, column=0, sticky="w", padx=5, pady=3)
        self.potion_recording_name_var = ttk.StringVar(value=self.config.get("potion_recording_name", "Potion"))
        ttk.Entry(extras, textvariable=self.potion_recording_name_var, width=16).grid(row=5, column=1, padx=5, pady=3)
        ttk.Button(extras, text="Record Potion", command=lambda: self.start_recording_path()).grid(row=5, column=2, padx=5, pady=3)
        ttk.Button(extras, text="Stop+Save Potion", command=lambda: messagebox.showinfo("Recorder", self.stop_recording_path(self.potion_recording_name_var.get() or "Potion", save_dir="crafting_files_do_not_open"))).grid(row=5, column=3, padx=5, pady=3)
        ttk.Button(extras, text="Replay Potion", command=lambda: threading.Thread(target=self.replay_path_recording, args=(self.potion_recording_name_var.get() or "Potion", "crafting_files_do_not_open"), daemon=True).start()).grid(row=5, column=4, padx=5, pady=3)

        ttk.Button(extras, text="Reconnect Now", command=lambda: threading.Thread(target=self.reconnect_private_server, daemon=True).start()).grid(row=6, column=0, padx=5, pady=5, sticky="w")
        ttk.Button(extras, text="Close Roblox/Sober", command=lambda: threading.Thread(target=self.terminate_roblox_processes, daemon=True).start()).grid(row=6, column=1, padx=5, pady=5, sticky="w")

        self.collect_egg_var = ttk.BooleanVar(value=self.config.get("collect_easter_egg", False))
        ttk.Checkbutton(extras, text="Enable Easter egg route replay", variable=self.collect_egg_var, command=self.save_config).grid(row=7, column=0, sticky="w", padx=5, pady=3)
        ttk.Label(extras, text="Egg interval min:").grid(row=7, column=1, sticky="e", padx=5)
        self.egg_collect_interval_var = ttk.StringVar(value=self.config.get("egg_collect_interval_min", "30"))
        ttk.Entry(extras, textvariable=self.egg_collect_interval_var, width=8).grid(row=7, column=2, sticky="w", padx=5)
        ttk.Button(extras, text="Run Egg Routes Once", command=lambda: threading.Thread(target=self.run_egg_collect_once, daemon=True).start()).grid(row=7, column=3, padx=5, pady=3)

        remote = ttk.LabelFrame(frame, text="Remote Bot (does not work currently)")
        remote.pack(fill="x", padx=8, pady=8)
        self.remote_access_var = ttk.BooleanVar(value=self.config.get("remote_access", False))
        ttk.Checkbutton(remote, text="Enable Discord slash-command remote", variable=self.remote_access_var, command=self._remote_access_toggle).grid(row=0, column=0, sticky="w", padx=5, pady=3)
        ttk.Label(remote, text="Bot token:").grid(row=1, column=0, sticky="e", padx=5)
        self.remote_bot_token_var = ttk.StringVar(value=self.config.get("remote_bot_token", ""))
        ttk.Entry(remote, textvariable=self.remote_bot_token_var, width=44, show="*").grid(row=1, column=1, columnspan=3, sticky="w", padx=5)
        ttk.Label(remote, text="Allowed user ID:").grid(row=2, column=0, sticky="e", padx=5)
        self.remote_allowed_user_id_var = ttk.StringVar(value=self.config.get("remote_allowed_user_id", ""))
        ttk.Entry(remote, textvariable=self.remote_allowed_user_id_var, width=24).grid(row=2, column=1, sticky="w", padx=5)
        self.remote_status_label = ttk.Label(remote, text="Bot: stopped")
        self.remote_status_label.grid(row=2, column=2, sticky="w", padx=5)
        ttk.Button(remote, text="Start Bot", command=self.start_remote_bot).grid(row=3, column=0, padx=5, pady=3)
        ttk.Button(remote, text="Stop Bot", command=self.stop_remote_bot).grid(row=3, column=1, padx=5, pady=3)
        ttk.Button(remote, text="Screenshot Now", command=lambda: threading.Thread(target=self.remote_take_and_send_screenshot, daemon=True).start()).grid(row=3, column=2, padx=5, pady=3)
        
    def create_auras_tab(self, frame):
        self.enable_aura_detection_var = ttk.BooleanVar(value=self.config.get("enable_aura_detection", False))
        enable_aura_detection_check = ttk.Checkbutton(
            frame, 
            text="Enable Aura Detection", 
            variable=self.enable_aura_detection_var,
            command=self.save_config
        )
        enable_aura_detection_check.pack(anchor="w", padx=5, pady=5)

        aura_frame = ttk.LabelFrame(frame, text="Aura Detection")
        aura_frame.pack(fill='x', padx=5, pady=5)
        
        # Discord UserID (Aura Ping)
        ttk.Label(aura_frame, text="Discord UserID (Aura Ping):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.aura_user_id_var = ttk.StringVar(value=self.config.get("aura_user_id", ""))
        aura_id_entry = ttk.Entry(aura_frame, textvariable=self.aura_user_id_var, width=25)
        aura_id_entry.grid(row=1, column=1, padx=5, pady=5)
        aura_id_entry.bind("<FocusOut>", lambda event: self.save_config())
        
        # Send Minimum
        ttk.Label(aura_frame, text="Aura Send Minimum:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.send_minimum_var = ttk.StringVar(value=self.config.get("send_minimum", "10000"))
        send_minimum_entry = ttk.Entry(aura_frame, textvariable=self.send_minimum_var, width=25)
        send_minimum_entry.grid(row=2, column=1, padx=5, pady=5)
        send_minimum_entry.bind("<FocusOut>", lambda event: self.save_config())

        # Ping Minimum
        ttk.Label(aura_frame, text="Aura Ping Minimum:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.ping_minimum_var = ttk.StringVar(value=self.config.get("ping_minimum", "100000"))
        ping_minimum_entry = ttk.Entry(aura_frame, textvariable=self.ping_minimum_var, width=25)
        ping_minimum_entry.grid(row=3, column=1, padx=5, pady=5)
        ping_minimum_entry.bind("<FocusOut>", lambda event: self.save_config())

    def create_merchant_tab(self, frame):
        mari_frame = ttk.LabelFrame(frame, text="Mari")
        mari_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        mari_button = ttk.Button(mari_frame, text="Mari Item Settings", command=self.open_mari_settings)
        mari_button.pack(padx=3, pady=3)
        
        jester_frame = ttk.LabelFrame(frame, text="Jester")
        jester_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        jester_button = ttk.Button(jester_frame, text="Jester Item Settings", command=self.open_jester_settings)
        jester_button.pack(padx=3, pady=3)

        calibration_button = ttk.Button(frame, text="Merchant Calibrations", command=self.open_merchant_calibration_window)
        calibration_button.grid(row=1, column=0, padx=5, pady=3, sticky="w")

        # Ping Mari
        self.ping_mari_var = ttk.BooleanVar(value=self.config.get("ping_mari", False))
        ping_mari_check = ttk.Checkbutton(
            frame, text="Ping if Mari found? (Custom Ping UserID/RoleID: &roleid)",
            variable=self.ping_mari_var, command=self.save_config)
        ping_mari_check.grid(row=2, column=0, padx=5, pady=3, sticky="w")

        self.mari_user_id_var = ttk.StringVar(value=self.config.get("mari_user_id", ""))
        mari_user_id_entry = ttk.Entry(frame, textvariable=self.mari_user_id_var, width=15)
        mari_user_id_entry.grid(row=2, column=1, padx=0, pady=3, sticky="w")
        mari_user_id_entry.bind("<FocusOut>", lambda event: self.save_config())

        mari_label = ttk.Label(frame, text="")
        mari_label.grid(row=2, column=2, padx=5, pady=3, sticky="w")

        # Ping Jester
        self.ping_jester_var = ttk.BooleanVar(value=self.config.get("ping_jester", False))
        ping_jester_check = ttk.Checkbutton(
            frame, text="Ping if Jester found? (Custom Ping UserID/RoleID: &roleid)",
            variable=self.ping_jester_var, command=self.save_config)
        ping_jester_check.grid(row=3, column=0, padx=5, pady=3, sticky="w")

        self.jester_user_id_var = ttk.StringVar(value=self.config.get("jester_user_id", ""))
        jester_user_id_entry = ttk.Entry(frame, textvariable=self.jester_user_id_var, width=15)
        jester_user_id_entry.grid(row=3, column=1, padx=0, pady=3, sticky="w")
        jester_user_id_entry.bind("<FocusOut>", lambda event: self.save_config())

        jester_label = ttk.Label(frame, text="")
        jester_label.grid(row=3, column=2, padx=5, pady=3, sticky="w")

        # Required Package Frame
        package_frame = ttk.LabelFrame(frame, text="Required Package For Auto Merchant")
        package_frame.grid(row=4, column=0, padx=5, pady=5, sticky="nsew")

        # Tesseract OCR Status
        ocr_status = self.check_tesseract_ocr()
        ocr_status_text = "Tesseract OCR Installed: Yes" if ocr_status else "Tesseract OCR Installed: No, click here to get OCR module"
        ocr_status_label = ttk.Label(package_frame, text=ocr_status_text, foreground="light blue", cursor="hand2")
        ocr_status_label.pack(anchor="w", padx=5, pady=3)
        if not ocr_status:
            ocr_status_label.bind("<Button-1>", lambda e: self.download_tesseract())

        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, weight=1)

    def check_tesseract_ocr(self):
        """Linux-compatible Tesseract detection."""
        candidate = shutil.which("tesseract")
        possible_paths = [
            candidate,
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/bin/tesseract",
            "/snap/bin/tesseract",
        ]
        for path in possible_paths:
            if path and os.path.exists(path) and os.access(path, os.X_OK):
                pytesseract.pytesseract.tesseract_cmd = path
                return True
        return False
    
    def download_tesseract(self):
        """Show Linux install instructions"""
        messagebox.showinfo(
            "Install Tesseract OCR",
            "Install Tesseract with your distro package manager, then restart the macro.\n\n"
            "Debian/Ubuntu/Linux Mint:\n"
            "  sudo apt install tesseract-ocr\n\n"
            "Fedora:\n"
            "  sudo dnf install tesseract\n\n"
            "Arch/Manjaro:\n"
            "  sudo pacman -S tesseract\n\n"
            "NixOS:\n"
            "  nix-shell -p tesseract"
        )

    def open_merchant_calibration_window(self):
        calibration_window = ttk.Toplevel(self.root)
        calibration_window.title("Merchant Calibration")
        calibration_window.geometry("650x345")

        positions = [
            ("Merchant Open Button", "merchant_open_button"),
            ("Merchant Dialogue Box", "merchant_dialogue_box"),
            ("Purchase Amount Button", "purchase_amount_button"),
            ("Purchase Button", "purchase_button"),
            ("First Item Slot Position", "first_item_slot_pos"),
            ("Merchant Name OCR Position", "merchant_name_ocr_pos"),
            ("Item Name OCR Position", "item_name_ocr_pos")
        ]

        self.coord_vars = {}

        for i, (label_text, config_key) in enumerate(positions):
            if "ocr" in config_key:
                label = ttk.Label(calibration_window, text=f"{label_text} (X, Y, W, H):")
                label.grid(row=i, column=0, padx=5, pady=5, sticky="w")

                x_var = ttk.IntVar(value=self.config.get(config_key, [0, 0, 0, 0])[0])
                y_var = ttk.IntVar(value=self.config.get(config_key, [0, 0, 0, 0])[1])
                w_var = ttk.IntVar(value=self.config.get(config_key, [0, 0, 0, 0])[2])
                h_var = ttk.IntVar(value=self.config.get(config_key, [0, 0, 0, 0])[3])
                self.coord_vars[config_key] = (x_var, y_var, w_var, h_var)

                ttk.Entry(calibration_window, textvariable=x_var, width=6).grid(row=i, column=1, padx=5, pady=5)
                ttk.Entry(calibration_window, textvariable=y_var, width=6).grid(row=i, column=2, padx=5, pady=5)
                ttk.Entry(calibration_window, textvariable=w_var, width=6).grid(row=i, column=3, padx=5, pady=5)
                ttk.Entry(calibration_window, textvariable=h_var, width=6).grid(row=i, column=4, padx=5, pady=5)

                select_button = ttk.Button(
                    calibration_window, text="Select Region",
                    command=lambda key=config_key: self.merchant_snipping(key)
                )
            else:
                label = ttk.Label(calibration_window, text=f"{label_text} (X, Y):")
                label.grid(row=i, column=0, padx=5, pady=5, sticky="w")

                x_var = ttk.IntVar(value=self.config.get(config_key, [0, 0])[0])
                y_var = ttk.IntVar(value=self.config.get(config_key, [0, 0])[1])
                self.coord_vars[config_key] = (x_var, y_var)

                ttk.Entry(calibration_window, textvariable=x_var, width=6).grid(row=i, column=1, padx=5, pady=5)
                ttk.Entry(calibration_window, textvariable=y_var, width=6).grid(row=i, column=2, padx=5, pady=5)

                select_button = ttk.Button(
                    calibration_window, text="Select Pos",
                    command=lambda key=config_key: self.start_capture_thread(key, self.coord_vars)
                )

            select_button.grid(row=i, column=5, padx=5, pady=5)

        save_button = ttk.Button(calibration_window, text="Save Calibration", command=lambda: self.save_merchant_coordinates(calibration_window))
        save_button.grid(row=len(positions), column=0, columnspan=6, pady=10)
        
    def merchant_snipping(self, config_key):
        def on_region_selected(region):
            x, y, w, h = region
            x_var, y_var, w_var, h_var = self.coord_vars[config_key]
            x_var.set(x)
            y_var.set(y)
            w_var.set(w)
            h_var.set(h)

        snipping_tool = SnippingWidget(self.root, config_key=config_key, callback=on_region_selected)
        snipping_tool.start()
    
    def save_merchant_coordinates(self, calibration_window):
        for config_key, vars in self.coord_vars.items():
            if len(vars) == 4:
                self.config[config_key] = [var.get() for var in vars]
            else:
                self.config[config_key] = [vars[0].get(), vars[1].get()]
        self.save_config()
        calibration_window.destroy()
    
    def open_mari_settings(self):
        mari_window = ttk.Toplevel(self.root)
        mari_window.title("Mari Items")

        items = [
            "Void Coin", "Lucky Penny", "Mixed Potion", "Lucky Potion",
            "Lucky Potion L", "Lucky Potion XL", "Speed Potion",
            "Speed Potion L", "Speed Potion XL", "Gear A", "Gear B"
        ]

        item_frame = ttk.Frame(mari_window)
        item_frame.pack(padx=10, pady=10, fill='x')
        ttk.Label(item_frame, text="Item Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ttk.Label(item_frame, text="Amount").grid(row=0, column=1, padx=5, pady=5, sticky="w")
        ttk.Label(item_frame, text="Rebuy").grid(row=0, column=2, padx=5, pady=5, sticky="w")

        self.mari_items_vars = {}
        self.mari_items_amounts = {}
        self.mari_items_rebuy = {}
        saved_mari_items = self.config.get("Mari_Items", {})

        for i, item in enumerate(items, start=1):
            saved_data = saved_mari_items.get(item, [False, 1, False])
            var = ttk.BooleanVar(value=saved_data[0])
            self.mari_items_vars[item] = var
            ttk.Checkbutton(item_frame, text=item, variable=var).grid(row=i, column=0, sticky="w", padx=5, pady=2)

            amount_var = ttk.StringVar(value=str(saved_data[1]))
            self.mari_items_amounts[item] = amount_var
            ttk.Entry(item_frame, textvariable=amount_var, width=5).grid(row=i, column=1, padx=5, pady=2)

            rebuy_var = ttk.BooleanVar(value=saved_data[2] if len(saved_data) > 2 else False)
            self.mari_items_rebuy[item] = rebuy_var
            ttk.Checkbutton(item_frame, variable=rebuy_var).grid(row=i, column=2, sticky="w", padx=5, pady=2)

        save_button = ttk.Button(mari_window, text="Save Selections", command=self.save_mari_selections)
        save_button.pack(pady=10)


    def save_mari_selections(self):
        mari_items = {
            item: [var.get(), int(self.mari_items_amounts[item].get()), self.mari_items_rebuy[item].get()]
            for item, var in self.mari_items_vars.items()
        }
        self.config["Mari_Items"] = mari_items
        self.save_config()
    
    def open_jester_settings(self):
        jester_window = ttk.Toplevel(self.root)
        jester_window.title("Jester Items")

        items = [
            "Oblivion Potion", "Heavenly Potion", "Rune of Everything",
            "Rune of Nothing", "Rune Of Corruption", "Rune Of Hell", "Rune of Galaxy",
            "Rune of Rainstorm", "Rune of Frost", "Rune of Wind", "Strange Potion", "Lucky Potion",
            "Stella's Candle", "Merchant Tracker", "Random Potion Sack"
        ]

        item_frame = ttk.Frame(jester_window)
        item_frame.pack(padx=10, pady=10, fill='x')
        ttk.Label(item_frame, text="Item Name").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ttk.Label(item_frame, text="Amount").grid(row=0, column=1, padx=5, pady=5, sticky="w")
        ttk.Label(item_frame, text="Rebuy").grid(row=0, column=2, padx=5, pady=5, sticky="w")

        self.jester_items_vars = {}
        self.jester_items_amounts = {}
        self.jester_items_rebuy = {}
        saved_jester_items = self.config.get("Jester_Items", {})

        for i, item in enumerate(items, start=1):
            saved_data = saved_jester_items.get(item, [False, 1, False])
            var = ttk.BooleanVar(value=saved_jester_items.get(item, [False, 1, False])[0])
            self.jester_items_vars[item] = var
            ttk.Checkbutton(item_frame, text=item, variable=var).grid(row=i, column=0, sticky="w", padx=5, pady=2)
        
            amount_var = ttk.StringVar(value=str(saved_jester_items.get(item, [False, 1, False])[1]))
            self.jester_items_amounts[item] = amount_var
            ttk.Entry(item_frame, textvariable=amount_var, width=5).grid(row=i, column=1, padx=5, pady=2)

            rebuy_var = ttk.BooleanVar(value=saved_data[2] if len(saved_data) > 2 else False)
            self.jester_items_rebuy[item] = rebuy_var
            ttk.Checkbutton(item_frame, variable=rebuy_var).grid(row=i, column=2, sticky="w", padx=5, pady=2)

        save_button = ttk.Button(jester_window, text="Save Selections", command=self.save_jester_selections)
        save_button.pack(pady=10)

    def save_jester_selections(self):
        jester_items = {
            item: [var.get(), int(self.jester_items_amounts[item].get()), self.jester_items_rebuy[item].get()]
            for item, var in self.jester_items_vars.items()
        }
        self.config["Jester_Items"] = jester_items
        self.save_config()
    
    def create_stats_tab(self, frame):
        self.stats_labels = {}
        biomes = list(self.biome_data.keys())
    
        columns = 4
        for i, biome in enumerate(biomes):
            color = f"#{int(self.biome_data[biome]['color'], 16):06x}"
            label = ttk.Label(frame, text=f"{biome}: {self.biome_counts[biome]}", foreground=color)
            
            row = i // columns
            col = i % columns
            
            label.grid(row=row, column=col, sticky="w", padx=2, pady=1)
            self.stats_labels[biome] = label

        # Total Biomes Found
        total_biomes = sum(self.biome_counts.values())
        self.total_biomes_label = ttk.Label(frame, text=f"Total Biomes Found: {total_biomes}", foreground="light green")
        self.total_biomes_label.grid(row=row + 1, column=0, columnspan=columns, sticky="w", padx=5, pady=5)

        # Running Session
        session_time = self.get_total_session_time()
        self.session_label = ttk.Label(frame, text=f"Running Session: {session_time}")
        self.session_label.grid(row=row + 2, column=0, columnspan=columns, sticky="w", padx=5, pady=10)

        # Biome Logs
        logs_frame = ttk.Frame(frame, borderwidth=2, relief="solid")
        logs_frame.grid(row=0, column=4, rowspan=5, sticky="nsew", padx=10, pady=2)
        logs_label = ttk.Label(logs_frame, text="Biome Logs")
        logs_label.pack(anchor="w", padx=5, pady=2)

        search_entry = ttk.Entry(logs_frame)
        search_entry.pack(anchor="w", padx=5, pady=1)
        search_entry.bind("<KeyRelease>", lambda event: self.filter_logs(search_entry.get()))

        self.logs_text = ttk.Text(logs_frame, height=8, width=25, wrap="word")
        self.logs_text.pack(expand=True, fill="both", padx=5, pady=5)
        self.logs_text.config(state="disabled")
        self.glitch_effect()
        
    def glitch_effect(self):
        glitch_texts = [
            "GLITCHED", "GlItChEd", "gLiTcHeD", "GL1TCHED", "g#lt#c%", 
            "g!olitc3", "g$&*ct", "G1iTcHeD", "gL1tCh3d", "gL!tCh3d",
            "G1!tCh3D", "gL1tCh3D", "gL!tCh3D", "G1!tCh3d", "gL1tCh3d"
        ]
        
        glitch_colors = [
            "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", 
            "#00FFFF", "#a6c9a3", "#ff69b4", "#8a2be2", "#7fff00",
            "#d2691e", "#ff7f50", "#6495ed", "#dc143c", "#00ced1"
        ]

        def update_glitch():
            glitchy_ahh_text = random.choice(glitch_texts)
            color = random.choice(glitch_colors)
            self.stats_labels["GLITCHED"].config(text=f"{glitchy_ahh_text}: {self.biome_counts['GLITCHED']}", foreground=color)
            self.root.after(25, update_glitch)

        update_glitch()
        
    def create_status_tab(self, frame):
        self.status_labels = {}
        rows = [
            ("Macro", "macro"),
            ("Biome Randomizer", "biome_randomizer"),
            ("Strange Controller", "strange_controller"),
            ("Merchant Teleporter", "merchant_teleporter"),
            ("Aura Detection", "aura_detection"),
            ("Auto Record", "auto_record"),
            ("Auto Pop", "auto_pop"),
            ("Idle Mode", "idle_mode"),
            ("Anti-AFK", "anti_afk"),
            ("Auto Reconnect", "auto_reconnect"),
            ("Obby Replay", "obby_replay"),
            ("Potion Replay", "potion_replay"),
            ("Current Biome", "current_biome"),
            ("Webhooks", "webhooks"),
            ("Last Log", "last_log"),
        ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=0, sticky="e", padx=8, pady=4)
            value = ttk.Label(frame, text="-")
            value.grid(row=row, column=1, sticky="w", padx=8, pady=4)
            self.status_labels[key] = value
        self.update_status_tab()

    def update_status_tab(self):
        if not hasattr(self, "status_labels"):
            return
        values = {
            "macro": "Running" if self.detection_running else "Idle/Stopped",
            "biome_randomizer": "Enabled" if getattr(self, "br_var", None) and self.br_var.get() else "Off",
            "strange_controller": "Enabled" if getattr(self, "sc_var", None) and self.sc_var.get() else "Off",
            "merchant_teleporter": "Enabled" if getattr(self, "mt_var", None) and self.mt_var.get() else "Off",
            "aura_detection": "Enabled" if getattr(self, "enable_aura_detection_var", None) and self.enable_aura_detection_var.get() else "Off",
            "auto_record": "Enabled" if getattr(self, "auto_record_var", None) and self.auto_record_var.get() else "Off",
            "auto_pop": "Enabled" if getattr(self, "auto_pop_glitched_var", None) and self.auto_pop_glitched_var.get() else "Off",
            "idle_mode": "Enabled" if getattr(self, "macro_idle_mode_var", None) and self.macro_idle_mode_var.get() else "Off",
            "anti_afk": "Enabled" if getattr(self, "anti_afk_var", None) and self.anti_afk_var.get() else "Off",
            "auto_reconnect": "Enabled" if getattr(self, "auto_reconnect_var", None) and self.auto_reconnect_var.get() else "Off",
            "obby_replay": "Enabled" if getattr(self, "enable_obby_var", None) and self.enable_obby_var.get() else "Off",
            "potion_replay": "Enabled" if getattr(self, "enable_potion_crafting_var", None) and self.enable_potion_crafting_var.get() else "Off",
            "current_biome": self.current_biome or "None",
            "webhooks": str(len(self.get_webhook_urls())),
            "last_log": self.logs[-1] if self.logs else "None",
        }
        for key, value in values.items():
            if key in self.status_labels:
                self.status_labels[key].config(text=value)

    def create_credit_tab(self, credits_frame):
        current_dir = os.getcwd()
        images_dir = os.path.join(current_dir, "images")
        credit_paths = [
            os.path.join(images_dir, "tea.png"),
            os.path.join(images_dir, "maxstellar.png")
        ]

        def load_image(filename, size):
            for path in credit_paths:
                if os.path.basename(path) == filename and os.path.exists(path):
                    try:
                        img = Image.open(path)
                        img = img.resize(size, Image.LANCZOS)
                        return ImageTk.PhotoImage(img)
                    except Exception as e:
                        print(f"Failed to load image: {path}, Error: {e}")
                        return None
            return None

        credits_frame_content = ttk.Frame(credits_frame)
        credits_frame_content.pack(pady=20)

        noteab_image = load_image("tea.png", (85, 85))
        maxstellar_image = load_image("maxstellar.png", (85, 85))

        noteab_frame = ttk.Frame(credits_frame_content, borderwidth=2, relief="solid")
        noteab_frame.grid(row=0, column=0, padx=10, pady=2)

        maxstellar_frame = ttk.Frame(credits_frame_content, borderwidth=2, relief="solid")
        maxstellar_frame.grid(row=0, column=1, padx=10, pady=2)

        if noteab_image:
            ttk.Label(noteab_frame, image=noteab_image).pack(pady=5)
        ttk.Label(noteab_frame, text="Main Developer: Noteab").pack()

        discord_label = ttk.Label(noteab_frame, text="Join my Community Server!!", foreground="#03cafc", cursor="hand2")
        discord_label.pack()
        discord_label.bind("<Button-1>", lambda e: webbrowser.open_new("https://discord.gg/radiant-team"))

        github_label = ttk.Label(noteab_frame, text="GitHub: Sol-Biome-Tracker", foreground="#03cafc", cursor="hand2")
        github_label.pack()
        github_label.bind("<Button-1>", lambda e: webbrowser.open_new("https://github.com/noteab/Sol-Biome-Tracker"))

        if maxstellar_image:
            ttk.Label(maxstellar_frame, image=maxstellar_image).pack(pady=5)
        ttk.Label(maxstellar_frame, text="Inspired Biome Macro Creator: Maxstellar").pack()
        maxstellar_yt = ttk.Label(maxstellar_frame, text="Their YT channel", foreground="#03cafc", cursor="hand2")
        maxstellar_yt.pack()
        maxstellar_yt.bind("<Button-1>", lambda e: webbrowser.open_new("https://www.youtube.com/@maxstellar_"))

        self.noteab_image = noteab_image
        self.maxstellar_image = maxstellar_image
    
    def update_stats(self):
        total_biomes = sum(self.biome_counts.values())

        for biome, label in self.stats_labels.items():
            label.config(text=f"{biome}: {self.biome_counts[biome]}")

        self.total_biomes_label.config(text=f"Total Biomes Found: {total_biomes}", foreground="light green")
        self.session_label.config(text=f"Running Session: {self.get_total_session_time()}")
        self.save_config()
        self.update_status_tab()
        

    def get_total_session_time(self):
        try:
            if self.start_time:
                elapsed_time = datetime.now() - self.start_time
                total_seconds = int(elapsed_time.total_seconds()) + self.saved_session
            else:
                total_seconds = self.saved_session

            # hours, minutes, and seconds
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            # Format time string
            return f"{hours:02}:{minutes:02}:{seconds:02}"

        except Exception as e:
            self.error_logging(e, "Error in get_total_session_time function.")
            return "00:00:00"

    def parse_session_time(self, session_time_str):
        try:
            parts = session_time_str.split(":")
            if len(parts) == 3:  # Format: hours:minutes:seconds
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            else:
                raise ValueError("Invalid session time format")

        except Exception as e:
            self.error_logging(e, "Error parsing session time.")
            return 0  # Return default value in case of error, well yeah
    
    def update_session_time(self):
        try:
            if self.start_time:
                elapsed_time = datetime.now() - self.start_time
                total_seconds = int(elapsed_time.total_seconds()) + self.saved_session

                # hours, minutes, and seconds
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)

                # Format string
                session_time_str = f"{hours:02}:{minutes:02}:{seconds:02}"
                self.session_label.config(text=f"Running Session: {session_time_str}")

        except Exception as e:
            self.error_logging(e, "Error in update_session_time function.")
    
    def display_logs(self, logs=None):
        self.logs_text.config(state="normal")
        self.logs_text.delete(1.0, ttk.END)
        if logs is None:
            logs = self.logs
        for log in logs:
            self.logs_text.insert(ttk.END, log + "\n")
        self.logs_text.config(state="disabled")

    def filter_logs(self, keyword):
        filtered_logs = [log for log in self.logs if keyword.lower() in log.lower()]
        self.display_logs(filtered_logs)
        
    ## INVENTORY SNIPPING ##
    
    def open_assign_inventory_window(self):
        assign_window = ttk.Toplevel(self.root)
        assign_window.title("Inventory Coordinates")
        assign_window.geometry("400x300")

        positions = [
            ("Inventory Menu", "inventory_menu"),
            ("Items Tab", "items_tab"),
            ("Search Bar", "search_bar"),
            ("First Item Slot", "first_item_slot"),
            ("Amount Box", "amount_box"),
            ("Use Button", "use_button")
        ]

        coord_vars = {}

        for i, (label_text, config_key) in enumerate(positions):
            label = ttk.Label(assign_window, text=f"{label_text} (X, Y):")
            label.grid(row=i, column=0, padx=5, pady=5, sticky="w")

            x_var = ttk.IntVar(value=self.config.get(config_key, [0, 0])[0])
            y_var = ttk.IntVar(value=self.config.get(config_key, [0, 0])[1])
            coord_vars[config_key] = (x_var, y_var)

            x_entry = ttk.Entry(assign_window, textvariable=x_var, width=6)
            x_entry.grid(row=i, column=1, padx=5, pady=5)

            y_entry = ttk.Entry(assign_window, textvariable=y_var, width=6)
            y_entry.grid(row=i, column=2, padx=5, pady=5)

            select_button = ttk.Button(
                assign_window, text="Assign Click",
                command=lambda key=config_key: self.start_capture_thread(key, coord_vars)
            )
            select_button.grid(row=i, column=3, padx=5, pady=5)

        save_button = ttk.Button(assign_window, text="Save", command=lambda: self.save_inventory_coordinates(assign_window, coord_vars))
        save_button.grid(row=len(positions), column=0, columnspan=4, pady=10)
        
    def save_inventory_coordinates(self, window, coord_vars):
        for key, (x_var, y_var) in coord_vars.items():
            self.config[key] = [x_var.get(), y_var.get()]
        self.save_config()
        
    def start_capture_thread(self, config_key, coord_vars):
        snipping_tool = SnippingWidget(self.root, config_key=config_key, callback=lambda region: self.update_coordinates(config_key, region, coord_vars))
        snipping_tool.start()

    def update_coordinates(self, config_key, region, coord_vars):
        x, y, _, _ = region
        x_var, y_var = coord_vars[config_key]
        x_var.set(x)
        y_var.set(y)
        
    def save_inventory_coordinates(self, window, coord_vars):
        try:
            with open("config.json", "r") as config_file:
                config = json.load(config_file)

            for key, (x_var, y_var) in coord_vars.items():
                config[key] = [x_var.get(), y_var.get()]

            with open("config.json", "w") as config_file:
                json.dump(config, config_file, indent=4)
        except Exception as e:
            print(f"Failed to save inventory coordinates to config.json: {e}")
        finally:
            window.destroy()
            
    ## INVENTORY SNIPPING ^^ ##
    def validate_and_save_ps_link(self):
        private_server_link = self.private_server_link_entry.get()
        if not self.validate_private_server_link(private_server_link):
            messagebox.showwarning(
                "Invalid PS Link!",
                "The link you provided is not a valid Roblox link. It could be either a share link or a private server code link. "
                "Please ensure the link is correct and try again.\n\n"
                "Valid links should look like:\n"
                "- Share link: https://www.roblox.com/share?code=1234567899abcdefxyz&type=Server\n"
                "- Private server link: https://www.roblox.com/games/15532962292/Sols-RNG-Eon1-1?privateServerLinkCode=..."
            )
            return

        self.save_config()

    def validate_private_server_link(self, link):
        # Pattern to match share links and private server links
        share_pattern = r"https://www\.roblox\.com/share\?code=\w+&type=Server"
        private_server_pattern = r"https://www\.roblox\.com/games/\d+/Sols-RNG-Eon1-1\?privateServerLinkCode=\w+"

        return re.match(share_pattern, link) or re.match(private_server_pattern, link)

    def start_detection(self):
        if not self.detection_running:
            self.detection_running = True
            self.start_time = datetime.now()
            self.detection_thread = threading.Thread(target=self.biome_loop_check, daemon=True)
            self.detection_thread.start()
            self.aura_detection_thread = threading.Thread(target=self.aura_loop_check, daemon=True)
            self.aura_detection_thread.start()
            self.root.title("Noteab/Coteab Macro Linux Port (Running)")
            self.start_anti_afk_worker()
            self.send_webhook_status("Macro started!", color=0x64ff5e)
            print("Biome detection started.")
            self.update_status_tab()

    def stop_detection(self):
        if self.detection_running:
            self.detection_running = False

            elapsed_time = int((datetime.now() - self.start_time).total_seconds())
            self.saved_session += elapsed_time

            self.start_time = None
            self.root.title("Noteab/Coteab Macro Linux Port (Stopped)")
            self.stop_anti_afk_worker()
            self.send_webhook_status("Macro stopped!", color=0xff0000)
            self.save_config()
            print("Biome detection stopped.")
            self.update_status_tab()
    
    def get_latest_log_file(self):
        return os.path.join(self.logs_dir, "latest.log")

    def read_log_file(self, log_file_path):
        if not os.path.exists(log_file_path):
            print(f"Log file not found: {log_file_path}")
            return []

        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as file:
            file.seek(self.last_position)
            lines = file.readlines()
            self.last_position = file.tell()
            return lines
        
    def read_full_log_file(self, log_file_path):
        if not os.path.exists(log_file_path):
            print(f"Log file not found: {log_file_path}")
            return []

        with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as file:
            return file.readlines()
        
    def load_auras_json(self):
        auras_paths = [
            "auras.json",
            "source_code/auras.json",
            os.path.join(os.path.dirname(__file__), "auras.json"),
            os.path.join(os.path.dirname(__file__), "source_code/auras.json")
        ]
        
        try:
            for path in auras_paths:
                if os.path.exists(path):
                    with open(path, "r") as file:
                        aura_data = json.load(file)
                        return aura_data
        except Exception as e:
            print(f"Error loading auras.json: {e}")
            self.error_logging(e, f"Error loading auras.json")
            return {}
        
    def check_aura_in_logs(self, log_file_path):
        try:
            if not hasattr(self, 'last_aura_found'):
                self.last_aura_found = None

            log_lines = self.read_full_log_file(log_file_path)

            for line in reversed(log_lines):
                try:
                    match = re.search(r'"state":"Equipped \\"(.*?)\\"', line)
                    if match:
                        aura = match.group(1)

                        if aura in self.auras_data:
                            aura_info = self.auras_data[aura]
                            rarity = aura_info["rarity"]
                            exclusive_biome, multiplier = aura_info["exclusive_biome"]

                            # Check if the current biome is GLITCHED
                            if self.current_biome == "GLITCHED":
                                rarity /= multiplier
                                #print(f"Adjusted rarity for {aura} in GLITCHED: {rarity}")
                                biome_message = "[From GLITCHED!]"

                            # Check if the current biome is the aura's exclusive biome
                            elif self.current_biome == exclusive_biome:
                                rarity /= multiplier
                                biome_message = f"[From {exclusive_biome}!]"

                            else:
                                biome_message = ""

                            # Format the rarity with commas
                            formatted_rarity = f"{int(rarity):,}"

                            if aura != self.last_aura_found:
                                self.send_aura_webhook(aura, formatted_rarity, biome_message)
                                self.last_aura_found = aura
                        return

                except Exception as e:
                    self.error_logging(e, "Error processing specific aura in check_aura_in_logs.")

        except Exception as e:
            self.error_logging(e, "Error in main check_aura_in_logs function")
            
    def check_biome_in_logs(self):
        try:
            log_file_path = self.get_latest_log_file()
            log_lines = self.read_log_file(log_file_path)
            retrieve_robloxid_lines = self.read_full_log_file(log_file_path)

            for line in reversed(log_lines):
                if '"largeImage":{"hoverText":"NORMAL"' in line:
                    return
                
                # Check other biomes logs line
                for biome in self.biome_data:
                    if biome in line:
                        self.handle_biome_detection(biome)
                        return

        except Exception as e:
            self.error_logging(e, "Error in check_biome_in_logs function :skull:")
                
    
    def handle_biome_detection(self, biome):
        try:
            biome_info = self.biome_data[biome]
            now = datetime.now()
            duration = int(biome_info.get('duration', 164))
            cooldown = timedelta(seconds=duration)
            
            if now - self.last_sent[biome] >= cooldown or self.last_sent[biome] == datetime.min:
                print(f"Detected Biome: {biome}, Color: {biome_info.get('color')}, Duration: {duration}")
                log_message = f"Detected Biome: {biome}"
                self.append_log(log_message)
                self.current_biome = biome
                self.last_sent[biome] = now

                # Update counter of that biome
                self.biome_counts[biome] += 1
                self.update_stats()

                message_type = self.config["biome_notifier"].get(biome, "None")
                self.send_webhook(biome, message_type)
                
                if biome in self.config.get("auto_pop_biomes", ["GLITCHED", "CYBERSPACE", "DREAMSPACE"]):
                    with self.lock:
                        record_duration = int(self.record_duration_var.get())
                        record_fps = int(self.record_fps_var.get())
                        record_thread = threading.Thread(target=self.record_screen, args=(record_duration, record_fps), daemon=True)
                        record_thread.start()
                        
                        if self.config.get("auto_pop_glitched", False):
                            self.auto_pop_buffs()

            for other_biome in self.biome_data:
                if other_biome != biome:
                    self.last_sent[other_biome] = datetime.min

        except Exception as e:
            self.error_logging(e, f"Error in handle_biome_detection for biome: {biome}. Hell naw go fix your ass macro noteab! - Wise greenie word")
                
            
    def biome_loop_check(self):
        last_log_file = None

        while self.detection_running:
            try:
                current_log_file = self.get_latest_log_file()
                if current_log_file != last_log_file:
                    self.last_position = 0
                    last_log_file = current_log_file
                
                self.check_biome_in_logs()
                self.update_session_time()

                # Check br/sc cooldown and execute it
                with self.lock:
                    self.auto_biome_change()
                    
                time.sleep(1)

            except Exception as e:
                self.error_logging(e, "Error in biome_loop_check function.")

    def aura_loop_check(self):
        last_log_file = None

        while self.detection_running:
            try:
                if self.enable_aura_detection_var.get():
                    current_log_file = self.get_latest_log_file()
                    if current_log_file != last_log_file:
                        self.last_position = 0
                        last_log_file = current_log_file

                    self.check_aura_in_logs(current_log_file)
                    time.sleep(1.2)

            except Exception as e:
                self.error_logging(e, "Error in aura_loop_check function.")

            
    def auto_biome_change(self):
        if self.config.get("enable_idle_mode", False):
            return
        try:
            mt_cooldown = timedelta(minutes=int(self.mt_duration_var.get()) if self.mt_duration_var.get() else 1) 
        except ValueError:
            mt_cooldown = timedelta(minutes=1)

        if self.mt_var.get() and datetime.now() - self.last_mt_time >= mt_cooldown:
            self.use_merchant_teleporter()
            self.last_mt_time = datetime.now()
            
        try:
            sc_cooldown = timedelta(minutes=int(self.sc_duration_var.get()) if self.sc_duration_var.get() else 15)
        except ValueError:
            sc_cooldown = timedelta(minutes=15)

        if self.sc_var.get() and datetime.now() - self.last_sc_time >= sc_cooldown:
            self.use_br_sc('strange controller')
            self.last_sc_time = datetime.now()
            
        try:
            br_cooldown = timedelta(minutes=int(self.br_duration_var.get()) if self.br_duration_var.get() else 30)
        except ValueError:
            br_cooldown = timedelta(minutes=30)

        if self.br_var.get() and datetime.now() - self.last_br_time >= br_cooldown:
            self.use_br_sc('biome randomizer')
            self.last_br_time = datetime.now()
    
    def Global_MouseClick(self, x, y, click=1):
        time.sleep(0.335)
        pyautogui.moveTo(x, y)
        pyautogui.click()
        
    def use_br_sc(self, item_name):
        try:
            if not self.detection_running: return
            time.sleep(1.3)

            inventory_click_delay = int(self.config.get("inventory_click_delay", "0")) / 1000.0
            inventory_menu = self.config.get("inventory_menu", [36, 535])
            items_tab = self.config.get("items_tab", [1272, 329])
            search_bar = self.config.get("search_bar", [855, 358])
            first_item_slot = self.config.get("first_item_slot", [839, 434])
            amount_box = self.config.get("amount_box", [594, 570])
            use_button = self.config.get("use_button", [710, 573])

            for _ in range(3):
                if not self.detection_running: return
                self.activate_roblox_window()
                time.sleep(0.15)

            print(f"Using {item_name.capitalize()}")

            # Inventory menu
            self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
            time.sleep(0.2 + inventory_click_delay)

            # Items tab
            self.Global_MouseClick(items_tab[0], items_tab[1])
            time.sleep(0.2 + inventory_click_delay) 

            # Search bar
            self.Global_MouseClick(search_bar[0], search_bar[1], click=2)
            time.sleep(0.2 + inventory_click_delay) 
            
            pyautogui.write(item_name)
            time.sleep(0.3 + inventory_click_delay)
            self.Global_MouseClick(first_item_slot[0], first_item_slot[1])
            time.sleep(0.3 + inventory_click_delay) 

            # Amount
            self.Global_MouseClick(amount_box[0], amount_box[1])
            time.sleep(0.16 + inventory_click_delay) 
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.13 + inventory_click_delay)
            pyautogui.press('backspace')
            time.sleep(0.13 + inventory_click_delay)
            pyautogui.press('1')
            time.sleep(0.13 + inventory_click_delay)

            # use button
            self.Global_MouseClick(use_button[0], use_button[1])
            time.sleep(0.22 + inventory_click_delay) 

            # inventory menu
            self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
            time.sleep(0.22 + inventory_click_delay) 

        except Exception as e:
            self.error_logging(e, "Error in use_br_sc function.")
        
    def use_merchant_teleporter(self):
        try:
            if not self.detection_running: return
            time.sleep(1.3)

            inventory_click_delay = int(self.config.get("inventory_click_delay", "0")) / 1000.0
            inventory_menu = self.config.get("inventory_menu", [36, 535])
            items_tab = self.config.get("items_tab", [1272, 329])
            search_bar = self.config.get("search_bar", [855, 358])
            first_item_slot = self.config.get("first_item_slot", [839, 434])
            amount_box = self.config.get("amount_box", [594, 570])
            use_button = self.config.get("use_button", [710, 573])

            for _ in range(3):
                if not self.detection_running: return
                self.activate_roblox_window()
                time.sleep(0.3)

            #  inventory menu
            self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
            time.sleep(0.24 + inventory_click_delay)

            # items tab
            self.Global_MouseClick(items_tab[0], items_tab[1])
            time.sleep(0.24 + inventory_click_delay)

            # search bar
            self.Global_MouseClick(search_bar[0], search_bar[1], click=2)
            time.sleep(0.27 + inventory_click_delay)

            # teleport item
            pyautogui.write("teleport")
            time.sleep(0.2 + inventory_click_delay)
            self.Global_MouseClick(first_item_slot[0], first_item_slot[1])
            time.sleep(0.25 + inventory_click_delay)

            # amount box
            self.Global_MouseClick(amount_box[0], amount_box[1])
            time.sleep(0.17 + inventory_click_delay)

            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.15 + inventory_click_delay)
            pyautogui.press('backspace')
            time.sleep(0.15 + inventory_click_delay)
            pyautogui.press('1')
            time.sleep(0.14 + inventory_click_delay)

            # use
            self.Global_MouseClick(use_button[0], use_button[1])
            time.sleep(0.23 + inventory_click_delay)

            # inv
            self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
            time.sleep(0.23 + inventory_click_delay)
            self.Merchant_Handler()

            time.sleep(0.33 + inventory_click_delay)
            self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
            time.sleep(0.33 + inventory_click_delay)
            self.Global_MouseClick(inventory_menu[0], inventory_menu[1])

        except Exception as e:
            self.error_logging(e, "Error in use_merchant_teleporter function.")
        
    def Merchant_Handler(self):
        try:
            merchant_name_ocr_pos = self.config["merchant_name_ocr_pos"]
            merchant_open_button = self.config["merchant_open_button"]
            first_item_slot_pos = self.config["first_item_slot_pos"]
            item_name_ocr_pos = self.config["item_name_ocr_pos"]
            merchant_dialogue_box = self.config["merchant_dialogue_box"]
            
            merchant_name = ""
            ocrMisdetect_Key = {
                "heovenly potion": "heavenly potion",
                "heovenly potion!": "heavenly potion",
                "heavenly potion": "heavenly potion",
                "heavenly potion!": "heavenly potion",
                "rune of goloxy": "rune of galaxy",
                "rune of roinstorm": "rune of rainstorm",
                "stronge potion": "strange potion",
                "stello's condle": "stella's candle",
                "merchont trocker": "merchant tracker",
                "rondom potion sock": "random potion sack",
                "geor a": "gear a",
                "geor b": "gear b"
            }
            
            if not hasattr(self, 'last_merchant_interaction'):
                self.last_merchant_interaction = 0
                
            merchant_cooldown_time = 190
            current_time = time.time()
            
            if current_time - self.last_merchant_interaction < merchant_cooldown_time: return
            
            for _ in range(4):
                pyautogui.press("e")
                time.sleep(0.3)
                
            pyautogui.moveTo(merchant_dialogue_box[0], merchant_dialogue_box[1])
            pyautogui.mouseDown()
            time.sleep(3)
            pyautogui.mouseUp()
                
            for _ in range(5):
                if not self.detection_running: return
                
                x, y, w, h = merchant_name_ocr_pos
                screenshot = pyautogui.screenshot(region=(x, y, w, h))
                merchant_name_text = pytesseract.image_to_string(screenshot, config='--psm 6').strip()
                if any(name in merchant_name_text for name in ["Mari", "Mori", "Marl", "Mar1", "MarI", "Mar!", "Maori"]):
                    merchant_name = "Mari"
                    print("[Merchant Detection]: Mari name found!")
                    break
                elif "Jester" in merchant_name_text:
                    merchant_name = "Jester"
                    print("[Merchant Detection]: Jester name found!")
                    break

                time.sleep(0.35)

            if merchant_name:
                print(f"Opening merchant interface for {merchant_name}")

                x, y = merchant_open_button
                pyautogui.moveTo(x, y, 0.3)
                pyautogui.click()
                time.sleep(0.73)

                screenshot_dir = os.path.join(os.getcwd(), "images")
                os.makedirs(screenshot_dir, exist_ok=True)

                item_screenshot = pyautogui.screenshot()
                screenshot_path = os.path.join(screenshot_dir, "merchant_screenshot.png")
                item_screenshot.save(screenshot_path)

                self.send_merchant_webhook(merchant_name, screenshot_path)

                auto_buy_items = self.config.get(f"{merchant_name}_Items", {})
                if not auto_buy_items:
                    return


                purchased_items = {}
                
                for slot_index in range(5):
                    if not self.detection_running:
                        return

                    x, y = first_item_slot_pos
                    slot_x = x + (slot_index * 178)
                    pyautogui.moveTo(slot_x, y, 0.5)
                    pyautogui.click()
                    time.sleep(0.35)

                    # OCR - item name
                    x, y, w, h = item_name_ocr_pos
                    screenshot = pyautogui.screenshot(region=(x, y, w, h))
                    item_text = pytesseract.image_to_string(screenshot, config='--psm 6').strip().lower()

                    self.append_log(f"[Merchant Detection - {merchant_name}] Detected item text: {item_text}")

                    corrected_item_name = item_text.split('|')[0].strip()
                    for misdetect, correct in ocrMisdetect_Key.items():
                        if misdetect in corrected_item_name:
                            corrected_item_name = correct
                            print(f"Corrected OCR misdetection: '{item_text}' -> '{correct}'")
                            break

                    print(f"Detected item text: {item_text} | Corrected: {corrected_item_name}")

                    for item_name, (enabled, quantity, rebuy) in auto_buy_items.items():
                        if enabled and corrected_item_name == item_name.lower():
                            purchased_count = purchased_items.get(item_name, 0)

                            if rebuy or purchased_count == 0:
                                self.append_log(f"[Merchant Detection - {merchant_name}] - Item {item_name} found. Proceeding to buy {quantity}")

                                purchase_amount_button = self.config["purchase_amount_button"]
                                purchase_button = self.config["purchase_button"]

                                pyautogui.moveTo(*purchase_amount_button)
                                pyautogui.click()
                                pyautogui.write(str(quantity))
                                time.sleep(0.25)

                                pyautogui.moveTo(*purchase_button)
                                pyautogui.click()
                                time.sleep(0.55)
                                pyautogui.moveTo(merchant_dialogue_box[0], merchant_dialogue_box[1])
                                pyautogui.mouseDown()
                                time.sleep(3)
                                pyautogui.mouseUp()

                                purchased_items[item_name] = purchased_count + 1
                                break

                # Update last merchant autobuy
                self.last_merchant_interaction = current_time
            else:
                print("No merchant detected.")
                
        except Exception as e:
            self.error_logging(e, "Error in Merchant_Handler function \n (If it say valueError: not enough values to unpack (expect 3 got 2) then open both mari and jester setting and click save selection again!)")
            
            
    def record_screen(self, duration=10, fps=10):
        if not self.config.get("auto_record", False):
            return
    
        recorder = pyscreenrec.ScreenRecorder()
        filename = f"glitched_biome_{int(time.time())}.mp4"
        recorder.start_recording(filename, fps)
        
        start_time = time.time()
        while time.time() - start_time < duration:
            if not self.detection_running:
                recorder.stop_recording()
                return
            time.sleep(0.5)

        recorder.stop_recording()
        print(f"Screen recording saved as {filename}")
    
        
    def send_webhook(self, biome, message_type):
        if message_type == "None": return

        biome_info = self.biome_data[biome]
        biome_color = int(biome_info["color"], 16)
        thumbnail_url = biome_info["thumbnail_url"]
        timestamp = time.strftime("[%H:%M:%S]") 
        
        content = ""
        if message_type == "Ping":
            user_id = self.config.get("biome_ping_ids", {}).get(biome) or self.config.get("webhook_user_id")
            content = discord_mention(user_id)

        private_server_link = self.config.get("private_server_link", "No link provided")

        payload = {
            "content": content,
            "embeds": [
                {
                    "title": f"{timestamp} Biome Started - {biome}",
                    "color": biome_color,
                    "thumbnail": {
                        "url": thumbnail_url
                    },
                    "footer": {
                        "text": "Noteab/Coteab Linux Port"
                    },
                    "fields": [
                        {
                            "name": "Private Server Link",
                            "value": private_server_link,
                            "inline": False
                        }
                    ]
                }
            ]
        }

        try:
            if self.post_json_to_webhooks(payload):
                print(f"Sent {message_type} for {biome}")
            
        except requests.exceptions.RequestException as e:
            print(f"Failed to send webhook: {e}")
    

    
    def send_merchant_webhook(self, merchant_name, screenshot_path):
        webhook_urls = self.get_webhook_urls()
        if not webhook_urls:
            print("Webhook URL is missing/not included in the config.json")
            return

        merchant_thumbnails = {
            "Mari": "https://static.wikia.nocookie.net/sol-rng/images/d/df/Mari_cropped.png/revision/latest?cb=20241015111527",
            "Jester": "https://static.wikia.nocookie.net/sol-rng/images/d/db/Headshot_of_Jester.png/revision/latest?cb=20240630142936"
        }


        if merchant_name == "Mari":
            ping_id = self.config.get("mari_user_id", "")
        elif merchant_name == "Jester":
            ping_id = self.config.get("jester_user_id", "")
        else:
            ping_id = ""

        content = f"<@{ping_id}>" if ping_id else ""
        ps_link = self.config.get("private_server_link", "")

        embeds = [{
            "title": f"{merchant_name} Detected!",
            "description": f"{merchant_name} has been detected on your screen.\n**Item screenshot**\n \nMerchant PS Link: {ps_link}",
            "color": 11753 if merchant_name == "Mari" else 8595632,
            "image": {"url": f"attachment://{os.path.basename(screenshot_path)}"},
            "thumbnail": {"url": merchant_thumbnails.get(merchant_name, "")}
        }]

        for webhook_url in webhook_urls:
            try:
                with open(screenshot_path, "rb") as image_file:
                    files = {"file": (os.path.basename(screenshot_path), image_file, "image/png")}
                    response = requests.post(
                        webhook_url,
                        data={
                            "payload_json": json.dumps({
                                "content": content,
                                "embeds": embeds
                            })
                        },
                        files=files,
                        timeout=30
                    )
                    response.raise_for_status()
                    print(f"Webhook sent successfully for {merchant_name}: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to send merchant webhook to {webhook_url}: {e}")
            
    def send_aura_webhook(self, aura_name, rarity, biome_message):
        send_minimum = int(self.config.get("send_minimum", "10000"))
        ping_minimum = int(self.config.get("ping_minimum", "100000"))

        color = 0xffffff # ok i have to define it
        
        # aura webhook color based from rarity
        rarity_value = int(rarity.replace(',', ''))
        if rarity_value >= send_minimum:
            if 99000 <= rarity_value < 1000000:
                color = 0x3dd3e0  # 99k - 999k
            elif 1000000 <= rarity_value < 10000000:
                color = 0xff73ec  # 1m - 9m
            elif 10000000 <= rarity_value < 99000000:
                color = 0x2d30f7  # 10m - 99m
            elif 99000000 <= rarity_value < 1000000000:
                color = 0xed2f59  # 99m - 999m
            else:
                color = 0xff9447

        description = f"## {time.strftime('[%H:%M:%S]')} \n ## > Aura found/equipped: {aura_name} in 1/{rarity} {biome_message}"

        payload = {
            "embeds": [
                {
                    "title": "⭐ Aura Detection ⭐",
                    "description": description,
                    "color": color,
                    "footer": {
                        "text": "Noteab/Coteab Linux Port",
                    }
                }
            ]
        }


        if rarity_value >= ping_minimum:
            aura_user_id = self.config.get("aura_user_id", "")
            if aura_user_id:
                payload["content"] = f"<@{aura_user_id}> "
            else:
                payload["content"] = description
            
        try:
            if self.post_json_to_webhooks(payload):
                print(f"Aura webhook sent for {aura_name}")
        except requests.exceptions.RequestException as e:
            print(f"Failed to send aura webhook: {e}")

    def find_sober_log_dir(self):
        """Find Sober/Vinegar Roblox logs without hard-coding a Linux username."""
        env_dir = os.environ.get("SOBER_LOG_DIR")
        candidates = []
        if env_dir:
            candidates.append(Path(env_dir).expanduser())
        home = Path.home()
        xdg_data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")).expanduser()
        candidates.extend([
            home / ".var" / "app" / "org.vinegarhq.Sober" / "data" / "sober" / "sober_logs",
            xdg_data / "sober" / "sober_logs",
            xdg_data / "vinegar" / "logs",
            home / ".local" / "share" / "sober" / "sober_logs",
        ])
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except Exception:
                pass
        return str(candidates[0]) if candidates else str(home)

    def send_webhook_status(self, status, color=None):
        try:
            default_color = 3066993 if "started" in status.lower() else 15158332
            embed_color = color if color is not None else default_color
            
            embeds = [{
                "title": "== 🌟 Macro Status 🌟 ==",
                "description": f"## [{time.strftime('%H:%M:%S')}] {status}",
                "color": embed_color,
                "footer": {
                    "text": "Noteab/Coteab Linux Port",
                }
            }]
            self.post_json_to_webhooks({"embeds": embeds})

        except requests.exceptions.RequestException as e:
            print(f"Failed to send webhook: {e}")
        except Exception as e:
            print(f"An error occurred in webhook_status: {e}")
        
    def activate_roblox_window(self):
        windows = gw.getAllTitles()
        roblox_window = None
        
        for window in windows:
            if "Sober" in window:
                roblox_window = gw.getWindowsWithTitle(window)[0]
                break

        if roblox_window:
            try:
                roblox_window.activate()
                if self.config.get("auto_roblox_fullscreen", False):
                    try:
                        pyautogui.hotkey("alt", "enter")
                    except Exception:
                        pass
            except Exception as e:
                print(f"Failed to activate window: {e}")
        else:
            print("Roblox window not found.")
    
    def start_anti_afk_worker(self):
        if not self.config.get("anti_afk", False):
            return
        if self.anti_afk_thread and self.anti_afk_thread.is_alive():
            return
        self.anti_afk_stop_event.clear()
        self.anti_afk_thread = threading.Thread(target=self.anti_afk_loop, daemon=True)
        self.anti_afk_thread.start()

    def stop_anti_afk_worker(self):
        try:
            self.anti_afk_stop_event.set()
        except Exception:
            pass

    def anti_afk_loop(self):
        while self.detection_running and not self.anti_afk_stop_event.is_set():
            try:
                interval = int(str(self.config.get("anti_afk_interval", "60") or "60"))
            except Exception:
                interval = 60
            interval = max(15, interval)
            if self.anti_afk_stop_event.wait(interval):
                break
            if not self.detection_running or self.config.get("enable_idle_mode", False):
                continue
            self.perform_anti_afk_action()

    def perform_anti_afk_action(self):
        """Portable Coteab-style Anti-AFK: focus Roblox/Sober and make a tiny harmless input."""
        try:
            self.activate_roblox_window()
            x, y = pyautogui.position()
            pyautogui.moveRel(1, 0, duration=0.05)
            pyautogui.moveTo(x, y, duration=0.05)
            self.append_log("[Anti-AFK] Sent small mouse movement")
        except Exception as e:
            self.error_logging(e, "Error in perform_anti_afk_action")

    def terminate_roblox_processes(self):
        """Linux-safe replacement for Coteab's Windows process killer."""
        targets = ("sober", "roblox", "RobloxPlayer", "RobloxPlayerBeta", "vinegar")
        killed = 0
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = proc.info.get("name") or ""
                    cmd = " ".join(proc.info.get("cmdline") or [])
                    haystack = f"{name} {cmd}".lower()
                    if any(t.lower() in haystack for t in targets):
                        proc.terminate()
                        killed += 1
                except Exception:
                    pass
        except Exception:
            # Fallback when psutil is unavailable.
            for name in targets:
                try:
                    subprocess.run(["pkill", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    killed += 1
                except Exception:
                    pass
        self.append_log(f"[Reconnect] Requested Roblox/Sober close. Matched processes: {killed}")
        return killed

    def reconnect_private_server(self):
        """Open the configured private server link; optionally close Roblox/Sober first."""
        link = self.config.get("private_server_link", "")
        if not link:
            self.append_log("[Reconnect] No private server link configured.")
            return False
        if self.config.get("auto_reconnect", False):
            self.terminate_roblox_processes()
            time.sleep(2)
        webbrowser.open(link)
        self.append_log("[Reconnect] Opened private server link.")
        return True

    def start_recording_path(self):
        """Record keyboard events and mouse clicks until Stop+Save is pressed. Requires pynput."""
        if self._recording_state:
            return "Already recording."
        try:
            from pynput import keyboard as pkb, mouse as pms
        except Exception as e:
            messagebox.showerror("Recorder dependency missing", "Install pynput first: pip install pynput")
            return f"pynput missing: {e}"
        events = []
        start = time.time()
        def key_name(key):
            try:
                return key.char
            except Exception:
                return str(key).replace("Key.", "")
        def on_press(key):
            events.append({"t": round(time.time()-start, 4), "type": "key_down", "key": key_name(key)})
        def on_release(key):
            events.append({"t": round(time.time()-start, 4), "type": "key_up", "key": key_name(key)})
        def on_click(x, y, button, pressed):
            if pressed:
                events.append({"t": round(time.time()-start, 4), "type": "click", "x": int(x), "y": int(y), "button": str(button).split(".")[-1]})
        kl = pkb.Listener(on_press=on_press, on_release=on_release)
        ml = pms.Listener(on_click=on_click)
        kl.start(); ml.start()
        self._recording_state = {"events": events, "keyboard_listener": kl, "mouse_listener": ml, "start": start}
        self.append_log("[Recorder] Recording started. Use Stop+Save when done.")
        return "Recording started."

    def stop_recording_path(self, name="obby", save_dir="paths"):
        state = self._recording_state
        if not state:
            return "Not recording."
        self._recording_state = None
        for key in ("keyboard_listener", "mouse_listener"):
            try:
                state[key].stop()
            except Exception:
                pass
        safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", str(name or "recording")).strip() or "recording"
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{safe}.json")
        data = {"version": APP_VERSION, "recorded_at": datetime.now().isoformat(), "events": state["events"]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        msg = f"Saved {len(state['events'])} events to {path}"
        self.append_log(f"[Recorder] {msg}")
        return msg

    def _pyautogui_key_name(self, key):
        aliases = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "shift_l": "shift", "shift_r": "shift", "alt_l": "alt", "alt_r": "alt", "cmd": "win", "cmd_l": "win", "cmd_r": "win"}
        return aliases.get(str(key), str(key))

    def replay_path_recording(self, name="obby", save_dir="paths"):
        safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", str(name or "recording")).strip() or "recording"
        path = os.path.join(save_dir, f"{safe}.json")
        if not os.path.exists(path):
            self.append_log(f"[Recorder] Recording not found: {path}")
            return "Recording not found."
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = data.get("events", []) if isinstance(data, dict) else []
        self.activate_roblox_window()
        last_t = 0.0
        self.append_log(f"[Recorder] Replaying {path} ({len(events)} events)")
        for ev in events:
            if not self.detection_running and self.config.get("require_macro_running_for_replay", False):
                break
            t = float(ev.get("t", 0))
            time.sleep(max(0, t - last_t))
            last_t = t
            typ = ev.get("type")
            try:
                if typ == "key_down":
                    pyautogui.keyDown(self._pyautogui_key_name(ev.get("key", "")))
                elif typ == "key_up":
                    pyautogui.keyUp(self._pyautogui_key_name(ev.get("key", "")))
                elif typ == "click":
                    pyautogui.click(int(ev.get("x", 0)), int(ev.get("y", 0)))
            except Exception as e:
                print(f"[Recorder] Replay event failed: {e}")
        self.append_log("[Recorder] Replay finished.")
        return "Replay finished."

    # ---------------- Coteab Windows features, Linux-safe rewrites ----------------
    def _set_remote_status(self, status, error=""):
        self.remote_bot_status = str(status)
        self.remote_bot_error = str(error or "")
        try:
            label = f"Bot: {self.remote_bot_status}"
            if self.remote_bot_error:
                label += f" ({self.remote_bot_error[:80]})"
            self.remote_status_label.config(text=label)
        except Exception:
            pass
        try:
            self.append_log(f"[Remote] Bot {self.remote_bot_status}" + (f": {self.remote_bot_error}" if self.remote_bot_error else ""))
        except Exception:
            pass

    def _remote_access_toggle(self):
        self.save_config()
        if getattr(self, "remote_access_var", None) and self.remote_access_var.get():
            self.start_remote_bot()
        else:
            self.stop_remote_bot()

    def start_remote_bot(self):
        """Start the optional Discord remote bot on Linux.

        This uses an explicit asyncio loop instead of discord.Bot.run(), which avoids
        signal-handler problems when the bot runs inside the GUI's background thread.
        """
        try:
            token = self.remote_bot_token_var.get().strip()
        except Exception:
            token = str(self.config.get("remote_bot_token", "")).strip()
        if not token:
            self._set_remote_status("stopped", "missing token")
            try:
                messagebox.showwarning("Remote Access", "Please enter a Discord bot token first.")
            except Exception:
                print("[Remote] No bot token provided.")
            return False
        if self.remote_bot_thread and self.remote_bot_thread.is_alive():
            return True

        self.remote_worker_running = True
        self.remote_bot_error = ""
        self._set_remote_status("starting")
        self.remote_bot_thread = threading.Thread(target=self._remote_bot_thread_func, args=(token,), daemon=True, name="NoteabDiscordRemoteBot")
        self.remote_bot_thread.start()
        if self.remote_command_queue is not None and (not self._remote_queue_thread or not self._remote_queue_thread.is_alive()):
            self._remote_queue_thread = threading.Thread(target=self._remote_queue_worker, daemon=True, name="NoteabRemoteCommandWorker")
            self._remote_queue_thread.start()
        self.save_config()
        return True

    def stop_remote_bot(self):
        self.remote_worker_running = False
        self._set_remote_status("stopping")
        try:
            bot = self.remote_bot_obj
            loop = self.remote_bot_loop
            if bot is not None and loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(bot.close(), loop)
        except Exception as e:
            print(f"[Remote] stop failed: {e}")
        finally:
            self.remote_bot_obj = None
            self._set_remote_status("stopped")

    def _remote_bot_thread_func(self, token):
        try:
            import discord
        except Exception as e:
            print("[Remote] Discord package missing. Install with: pip install py-cord")
            self.error_logging(e, "Remote bot dependency missing")
            self._set_remote_status("error", "missing py-cord")
            return

        allowed_raw = str(getattr(self, "remote_allowed_user_id_var", None).get() if getattr(self, "remote_allowed_user_id_var", None) else self.config.get("remote_allowed_user_id", "")).strip()
        allowed_id = int(allowed_raw) if allowed_raw.isdigit() else None
        intents = discord.Intents.default()
        try:
            bot = discord.Bot(intents=intents)
        except Exception:
            bot = discord.Client(intents=intents)
        self.remote_bot_obj = bot

        def authorized(ctx):
            if not allowed_id:
                return True
            user = getattr(ctx, "user", None) or getattr(ctx, "author", None)
            return getattr(user, "id", None) == allowed_id

        async def reply(ctx, message, *, ephemeral=False):
            try:
                if hasattr(ctx, "respond"):
                    return await ctx.respond(message, ephemeral=ephemeral)
                if hasattr(ctx, "send"):
                    return await ctx.send(message)
            except Exception as e:
                print(f"[Remote] failed to reply: {e}")

        async def deny(ctx):
            await reply(ctx, "Unauthorized", ephemeral=True)

        @bot.event
        async def on_ready():
            try:
                if hasattr(bot, "sync_commands"):
                    await bot.sync_commands()
                msg = f"online as {bot.user}"
                print(f"[Remote] Bot {msg}")
                self._set_remote_status("running", msg)
            except Exception as e:
                print(f"[Remote] on_ready error: {e}")

        if hasattr(bot, "slash_command"):
            @bot.slash_command(name="macro_start", description="Start biome detection")
            async def remote_start(ctx):
                if not authorized(ctx):
                    return await deny(ctx)
                self.start_detection()
                await reply(ctx, "Macro started.", ephemeral=True)

            @bot.slash_command(name="macro_stop", description="Stop the macro")
            async def remote_stop(ctx):
                if not authorized(ctx):
                    return await deny(ctx)
                self.stop_detection()
                await reply(ctx, "Macro stopped.", ephemeral=True)

            @bot.slash_command(name="macro_status", description="Show current macro status")
            async def remote_status(ctx):
                if not authorized(ctx):
                    return await deny(ctx)
                status = "Running" if self.detection_running else "Stopped"
                biome = self.current_biome or "None"
                sess = self.get_total_session_time()
                await reply(ctx, f"Status: **{status}**\nCurrent biome: **{biome}**\nSession: `{sess}`", ephemeral=True)

            @bot.slash_command(name="macro_rejoin", description="Close Roblox/Sober and reopen the private server link")
            async def remote_rejoin(ctx):
                if not authorized(ctx):
                    return await deny(ctx)
                if self.remote_command_queue:
                    self.remote_command_queue.put(("__rejoin__", ""))
                await reply(ctx, "Rejoin queued.", ephemeral=True)

            @bot.slash_command(name="macro_screenshot", description="Take a screenshot and send it to configured webhooks")
            async def remote_screenshot(ctx):
                if not authorized(ctx):
                    return await deny(ctx)
                if self.remote_command_queue:
                    self.remote_command_queue.put(("__screenshot__", "full"))
                await reply(ctx, "Screenshot queued.", ephemeral=True)

            @bot.slash_command(name="macro_use", description="Use an inventory item by name")
            async def remote_use(ctx, item: str, amount: int = 1):
                if not authorized(ctx):
                    return await deny(ctx)
                if self.remote_command_queue:
                    self.remote_command_queue.put((str(item), int(amount)))
                await reply(ctx, f"Queued item use: {item} x{amount}", ephemeral=True)

            @bot.slash_command(name="macro_equip_aura", description="Search for and equip an aura")
            async def remote_equip_aura(ctx, name: str):
                if not authorized(ctx):
                    return await deny(ctx)
                if self.remote_command_queue:
                    self.remote_command_queue.put(("__equip_aura__", str(name)))
                await reply(ctx, f"Queued aura equip: {name}", ephemeral=True)
        else:
            print("[Remote] Installed discord package does not expose slash commands. Install py-cord, not discord.py.")
            self._set_remote_status("error", "py-cord slash commands unavailable")
            self.remote_worker_running = False
            return

        loop = asyncio.new_event_loop()
        self.remote_bot_loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(token))
        except Exception as e:
            err = str(e) or type(e).__name__
            print(f"[Remote] bot.start failed: {err}")
            self.error_logging(e, "Remote bot.start failed")
            self._set_remote_status("error", err)
        finally:
            try:
                if not bot.is_closed():
                    loop.run_until_complete(bot.close())
            except Exception:
                pass
            self.remote_bot_obj = None
            self.remote_bot_loop = None
            self.remote_worker_running = False
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            loop.close()
            if self.remote_bot_status != "error":
                self._set_remote_status("stopped")

    def _remote_blocked(self):
        return bool(
            getattr(self, "_br_sc_running", False)
            or getattr(self, "_mt_running", False)
            or getattr(self, "auto_pop_state", False)
            or getattr(self, "on_auto_merchant_state", False)
            or getattr(self, "_egg_collecting", False)
            or self.config.get("enable_idle_mode", False)
        )

    def _remote_queue_worker(self):
        while self.remote_worker_running:
            try:
                item_name, amount = self.remote_command_queue.get(timeout=1)
            except Exception:
                continue
            try:
                if item_name == "__rejoin__":
                    self.reconnect_private_server()
                elif item_name == "__screenshot__":
                    self.remote_take_and_send_screenshot()
                elif item_name == "__equip_aura__":
                    if self._remote_blocked():
                        self.remote_command_queue.put((item_name, amount)); time.sleep(1); continue
                    self.remote_equip_aura(str(amount))
                else:
                    if self._remote_blocked():
                        self.remote_command_queue.put((item_name, amount)); time.sleep(1); continue
                    self.remote_use_item(str(item_name), int(amount))
            except Exception as e:
                self.error_logging(e, f"Remote command failed: {item_name}")

    def remote_use_item(self, item_name, amount=1):
        """Linux-safe rewrite of Coteab's remote /use command."""
        if self.config.get("enable_idle_mode", False):
            return False
        inventory_click_delay = int(self.config.get("inventory_click_delay", "0") or 0) / 1000.0
        self.activate_roblox_window(); time.sleep(0.35)
        inventory_menu = self.config.get("inventory_menu", [36, 535])
        items_tab = self.config.get("items_tab", [1272, 329])
        search_bar = self.config.get("search_bar", [855, 358])
        first_item_slot = self.config.get("first_item_slot", self.config.get("first_item_inventory_slot_pos", [839, 434]))
        amount_box = self.config.get("amount_box", [594, 570])
        use_button = self.config.get("use_button", [710, 573])
        close_button = self.config.get("inventory_close_button", inventory_menu)
        self.Global_MouseClick(inventory_menu[0], inventory_menu[1]); time.sleep(0.22 + inventory_click_delay)
        self.Global_MouseClick(items_tab[0], items_tab[1]); time.sleep(0.22 + inventory_click_delay)
        self.Global_MouseClick(search_bar[0], search_bar[1], click=2); time.sleep(0.22 + inventory_click_delay)
        pyautogui.write(str(item_name).lower()); time.sleep(0.25 + inventory_click_delay)
        self.Global_MouseClick(first_item_slot[0], first_item_slot[1]); time.sleep(0.25 + inventory_click_delay)
        self.Global_MouseClick(amount_box[0], amount_box[1], click=2); time.sleep(0.1 + inventory_click_delay)
        pyautogui.hotkey("ctrl", "a"); pyautogui.press("backspace"); pyautogui.write(str(amount)); time.sleep(0.15 + inventory_click_delay)
        self.Global_MouseClick(use_button[0], use_button[1]); time.sleep(0.25 + inventory_click_delay)
        self.Global_MouseClick(close_button[0], close_button[1])
        self.append_log(f"[Remote] Used item {item_name} x{amount}")
        return True

    def remote_equip_aura(self, aura_name):
        """Linux-safe rewrite of Coteab's remote /equip_aura command."""
        if self.config.get("enable_idle_mode", False):
            return False
        delay = int(self.config.get("inventory_click_delay", "0") or 0) / 1000.0 + 0.55
        self.activate_roblox_window(); time.sleep(0.35)
        aura_menu = self.config.get("aura_menu", [37, 387])
        aura_search_bar = self.config.get("aura_search_bar", [834, 364])
        first_aura_slot = self.config.get("first_aura_slot_pos", [819, 420])
        equip_btn = self.config.get("equip_aura_button", [0, 0])
        close_button = self.config.get("inventory_close_button", [1418, 298])
        if aura_menu[0] > 0: self.Global_MouseClick(aura_menu[0], aura_menu[1])
        time.sleep(delay)
        if aura_search_bar[0] > 0: self.Global_MouseClick(aura_search_bar[0], aura_search_bar[1], click=2)
        time.sleep(delay)
        pyautogui.write(str(aura_name).lower()); pyautogui.press("enter"); time.sleep(delay)
        if first_aura_slot[0] > 0:
            pyautogui.moveTo(first_aura_slot[0], first_aura_slot[1]); pyautogui.scroll(40); time.sleep(delay)
            self.Global_MouseClick(first_aura_slot[0], first_aura_slot[1])
        time.sleep(delay)
        if equip_btn[0] > 0: self.Global_MouseClick(equip_btn[0], equip_btn[1])
        time.sleep(delay)
        if close_button[0] > 0: self.Global_MouseClick(close_button[0], close_button[1])
        self.append_log(f"[Remote] Equipped aura request: {aura_name}")
        return True

    def remote_take_and_send_screenshot(self):
        try:
            self.activate_roblox_window(); time.sleep(0.4)
            os.makedirs("images", exist_ok=True)
            filename = os.path.join("images", f"remote_screenshot_{int(time.time())}.png")
            pyautogui.screenshot().save(filename)
            if hasattr(self, "send_screen_screenshot_webhook"):
                self.send_screen_screenshot_webhook(filename)
            else:
                self.post_file_to_webhooks(filename, content="Remote screenshot")
            self.append_log(f"[Remote] Screenshot saved/sent: {filename}")
            return filename
        except Exception as e:
            self.error_logging(e, "Error taking/sending remote screenshot")
            return None

    def post_file_to_webhooks(self, filename, content=""):
        for url in self.get_webhook_urls():
            try:
                with open(filename, "rb") as f:
                    requests.post(url, data={"content": content}, files={"file": (os.path.basename(filename), f)}, timeout=15)
            except Exception as e:
                print(f"Failed to post file to webhook: {e}")

    def _load_route_events(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = data.get("events", data) if isinstance(data, dict) else data
        if not isinstance(events, list):
            return []
        return [e for e in events if isinstance(e, dict)]

    def _replay_key_route(self, events, allowed_keys=None, speed_multiplier=1.0):
        pressed = set()
        allowed_keys = set(allowed_keys or ["w", "a", "s", "d", "space"])
        base_t = float(events[0].get("t", 0.0)) if events else 0.0
        start = time.perf_counter()
        try:
            for ev in events:
                if not self.detection_running and self.config.get("require_macro_running_for_replay", False):
                    break
                ev_t = (float(ev.get("t", base_t)) - base_t) * float(speed_multiplier or 1.0)
                while time.perf_counter() - start < ev_t:
                    time.sleep(0.005)
                typ = str(ev.get("type", ""))
                key = self._pyautogui_key_name(str(ev.get("key", "")).lower().strip())
                if typ == "key_down" and key in allowed_keys:
                    pyautogui.keyDown(key); pressed.add(key)
                elif typ == "key_up" and key in allowed_keys:
                    pyautogui.keyUp(key); pressed.discard(key)
            return True
        finally:
            for k in list(pressed) + ["w", "a", "s", "d", "space"]:
                try: pyautogui.keyUp(k)
                except Exception: pass

    def run_egg_collect_once(self):
        """Linux-safe first-pass implementation of Coteab egg route replay."""
        if self._egg_collecting:
            return False
        self._egg_collecting = True
        try:
            search_dirs = [Path("paths"), APP_DIR / "paths"]
            route_files = []
            for d in search_dirs:
                if d.exists():
                    route_files.extend(sorted(d.glob("egg_route*.json")))
            if not route_files:
                self.append_log("[EggCollect] No paths/egg_route*.json files found.")
                return True
            self.activate_roblox_window(); time.sleep(0.5)
            for route in route_files:
                events = self._load_route_events(route)
                events = [e for e in events if e.get("type") in ("key_down", "key_up")]
                if not events:
                    self.append_log(f"[EggCollect] {route.name} has no usable movement events.")
                    continue
                self.append_log(f"[EggCollect] Replaying {route.name} ({len(events)} events)")
                self._replay_key_route(events)
                time.sleep(1)
            self.append_log("[EggCollect] Route replay complete.")
            return True
        except Exception as e:
            self.error_logging(e, "run_egg_collect_once failed")
            return False
        finally:
            self._egg_collecting = False


    def auto_pop_buffs(self):
        try:
            inventory_click_delay = int(self.config.get("inventory_click_delay", "0")) / 1000.0
            
            for buff, (enabled, amount) in self.config.get("auto_buff_glitched", {}).items():
                if not self.detection_running: return
                if enabled:
                    print(f"Using {buff} x{amount}")

                    for _ in range(3):
                        if not self.detection_running: return
                        self.activate_roblox_window()
                        time.sleep(0.35)

                    # inventory menu
                    inventory_menu = self.config.get("inventory_menu", [36, 535])
                    self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
                    time.sleep(0.22 + inventory_click_delay)

                    # Search for the buff item
                    search_bar = self.config.get("search_bar", [855, 358]) 
                    self.Global_MouseClick(search_bar[0], search_bar[1], click=2)
                    time.sleep(0.23 + inventory_click_delay)

                    # buff name
                    keyboard.write(buff.lower())
                    time.sleep(0.22 + inventory_click_delay)

                    # first buff item slot
                    first_item_slot = self.config.get("first_item_slot", [839, 434])
                    self.Global_MouseClick(first_item_slot[0], first_item_slot[1])
                    time.sleep(0.22 + inventory_click_delay)

                    # amount box
                    amount_box = self.config.get("amount_box", [594, 570])
                    self.Global_MouseClick(amount_box[0], amount_box[1])
                    time.sleep(0.22 + inventory_click_delay)

                    # amount to use
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.285 + inventory_click_delay)
                    pyautogui.press("backspace")
                    time.sleep(0.285 + inventory_click_delay)
                    pyautogui.write(str(amount))
                    time.sleep(0.285 + inventory_click_delay)

                    # use
                    use_button = self.config.get("use_button", [710, 573])
                    self.Global_MouseClick(use_button[0], use_button[1])
                    time.sleep(0.3 + inventory_click_delay)

                    # Close inventory menu
                    self.Global_MouseClick(inventory_menu[0], inventory_menu[1])
                    time.sleep(0.32 + inventory_click_delay)

        except Exception as e:
            self.error_logging(e, "Error in auto_pop_buffs function")


class MainMacroWebApi:
    """Bridge used by the original React GUI bundled in assets/index.html."""
    def __init__(self, macro):
        self.macro = macro
        self._update_available = None

    def get_config(self):
        return self.macro.config

    def save_config(self, config):
        return self.macro.apply_config_dict(config)

    def get_biome_data(self):
        return {name: data.get("color", "#ffffff") for name, data in self.macro.biome_data.items()}

    def get_full_biome_data(self):
        return self.macro.biome_data

    def get_macro_version(self):
        return APP_VERSION + "+main-gui"

    def set_biome_detection(self, enabled):
        if enabled:
            self.macro.start_detection()
        else:
            self.macro.stop_detection()
        return {"running": self.macro.detection_running}

    def send_webhook_status(self, status, color=None):
        self.macro.send_webhook_status(status, color=color)
        return True

    def open_url(self, url):
        webbrowser.open(str(url))
        return True

    def set_always_on_top(self, enabled):
        try:
            if getattr(self.macro, "webview_window", None):
                self.macro.webview_window.on_top = bool(enabled)
        except Exception:
            pass
        return True

    def import_config(self):
        try:
            path = filedialog.askopenfilename(title="Import config", filetypes=[("JSON", "*.json"), ("All files", "*")])
            if not path:
                return {"success": False, "error": "No file selected"}
            with open(path, "r") as file:
                data = json.load(file)
            self.macro.apply_config_dict(data)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_for_updates(self):
        return self.get_update_available()

    def get_update_available(self):
        try:
            response = requests.get("https://api.github.com/repos/noteab/Noteab-Macro/releases/latest", timeout=10)
            response.raise_for_status()
            release = response.json()
            version = release.get("tag_name")
            assets = release.get("assets") or []
            url = assets[0].get("browser_download_url") if assets else release.get("html_url")
            if version and version != APP_VERSION:
                self._update_available = {"version": version, "url": url}
                return self._update_available
        except Exception as e:
            print(f"Update check failed: {e}")
        return None

    def apply_update(self, download_url, version=None):
        # Linux port intentionally does not self-replace. Open the release/download in a browser.
        if download_url:
            webbrowser.open(str(download_url))
        return "opened"

    def check_winocr_status(self):
        return {"installed": shutil.which("tesseract") is not None, "version": None, "platform": "linux"}

    def create_calibration_window(self, key, mode="point"):
        def _run():
            try:
                self.macro.open_calibration_window(key, is_region=(mode == "region"))
            except TypeError:
                self.macro.open_calibration_window(key)
            except Exception as e:
                self.macro.error_logging(e, "web calibration failed")
        threading.Thread(target=_run, daemon=True).start()
        return True

    def take_calibration_screenshot(self):
        img = pyautogui.screenshot()
        path = APP_DIR / "calibration_screenshot.png"
        img.save(path)
        return [str(path), img.width, img.height]

    def emit_calibration_result(self, result):
        # The bundled calibration overlay calls this when it is used as a secondary window.
        self.last_calibration_result = result
        return True

    def close_window(self):
        try:
            if getattr(self.macro, "webview_window", None):
                self.macro.webview_window.destroy()
        except Exception:
            pass
        return True

    def test_biome_keybind(self):
        threading.Timer(2.0, lambda: pyautogui.press(str(self.macro.config.get("rare_biome_record_keybind", "F8")).lower())).start()
        return True

    def test_aura_keybind(self):
        threading.Timer(2.0, lambda: pyautogui.press(str(self.macro.config.get("aura_record_keybind", "F8")).lower())).start()
        return True

    def start_macro_recording(self):
        return self.macro.start_recording_path()

    def stop_macro_recording(self):
        return self.macro.stop_recording_path("obby", save_dir="paths")

    def stop_macro_recording_potion(self, name):
        return self.macro.stop_recording_path(name or "Potion", save_dir="crafting_files_do_not_open")

    def open_recorder_window_potion(self):
        return True

    def replay_recording(self):
        self.macro.replay_path_recording("obby", save_dir="paths")
        return "Replay Finished"

    def replay_potion_recording(self, name):
        self.macro.replay_path_recording(name or "Potion", save_dir="crafting_files_do_not_open")
        return "Replay Finished"

    def align_camera(self):
        try:
            pyautogui.press("escape")
        except Exception:
            pass
        return True

    def list_potion_files(self):
        directory = APP_DIR / "crafting_files_do_not_open"
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    def check_obby_path_exists(self):
        return (APP_DIR / "paths" / "obby.json").exists() or (APP_DIR / "obby.json").exists()

    def display_all_fishing_calibrations_on_screen(self, ms=3500):
        return False

    def get_active_modules(self):
        cfg = self.macro.config
        modules = {
            "Biome Randomizer": bool(cfg.get("biome_randomizer")),
            "Strange Controller": bool(cfg.get("strange_controller")),
            "Merchant Teleporter": bool(cfg.get("merchant_teleporter")),
            "Aura Detection": bool(cfg.get("enable_aura_detection")),
            "Auto Record": bool(cfg.get("auto_record")),
            "Auto Pop": bool(cfg.get("auto_pop_glitched")),
            "Anti AFK": bool(cfg.get("anti_afk")),
            "Auto Reconnect": bool(cfg.get("auto_reconnect")),
            "Obby Replay": bool(cfg.get("enable_obby_path")),
            "Potion Crafting": bool(cfg.get("enable_potion_crafting")),
        }
        return {"running": self.macro.detection_running, "modules": modules, "incompatibilities": []}

    def start_remote_bot(self):
        return {"started": bool(self.macro.start_remote_bot()), "status": self.macro.remote_bot_status, "error": self.macro.remote_bot_error}

    def stop_remote_bot(self):
        self.macro.stop_remote_bot()
        return {"stopped": True, "status": self.macro.remote_bot_status}

    def get_remote_bot_status(self):
        thread_alive = bool(self.macro.remote_bot_thread and self.macro.remote_bot_thread.is_alive())
        return {"status": self.macro.remote_bot_status, "error": self.macro.remote_bot_error, "thread_alive": thread_alive}

    def test_remote_screenshot(self):
        threading.Thread(target=self.macro.remote_take_and_send_screenshot, daemon=True).start()
        return {"queued": True}

    def confirm_biome_response(self, accepted):
        self.last_biome_confirmation = bool(accepted)
        return True


try:
    biome_presence = BiomePresence()
except KeyboardInterrupt:
    print("Exited (Keyboard Interrupted)")
finally:
    try:
        keyboard.unhook_all()
    except Exception:
        pass
