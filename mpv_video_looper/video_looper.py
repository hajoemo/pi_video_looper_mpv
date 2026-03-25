#!/usr/bin/env python3
"""
mpv_video_looper  –  A Raspberry Pi dedicated video looping application
using mpv as the player backend.

Inspired by adafruit/pi_video_looper (omxplayer-based).
Requires: mpv, python-mpv (pip install python-mpv), pygame, RPi.GPIO (optional)
"""

import configparser
import datetime
import logging
import os
import random
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pygame

# Optional GPIO support
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

# Optional mpv python binding
try:
    import mpv as mpv_module
    MPV_BINDING = True
except ImportError:
    MPV_BINDING = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("mpv_video_looper")

CONFIG_PATH = "/boot/video_looper.ini"
STATE_FILE = "/tmp/mpv_looper_state.txt"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(path: str = CONFIG_PATH) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    # Fallback to local assets/ copy when /boot version doesn't exist
    candidates = [path, os.path.join(os.path.dirname(__file__), "..", "assets", "video_looper.ini")]
    for c in candidates:
        if os.path.exists(c):
            cfg.read(c)
            log.info("Loaded config from %s", c)
            return cfg
    log.warning("No config file found; using built-in defaults.")
    return cfg


def cfg_get(cfg, section, key, fallback=None):
    try:
        return cfg.get(section, key).strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback


def cfg_bool(cfg, section, key, fallback=False):
    val = cfg_get(cfg, section, key)
    if val is None:
        return fallback
    return val.lower() in ("true", "1", "yes")


def cfg_int(cfg, section, key, fallback=0):
    val = cfg_get(cfg, section, key)
    if val is None:
        return fallback
    try:
        return int(val)
    except ValueError:
        return fallback


def cfg_color(cfg, section, key, fallback=(0, 0, 0)):
    val = cfg_get(cfg, section, key)
    if val is None:
        return fallback
    try:
        parts = [int(x.strip()) for x in val.split(",")]
        if len(parts) == 3:
            return tuple(parts)
    except ValueError:
        pass
    return fallback


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

class USBDriveReader:
    """Scans the root of mounted USB drives for media files."""

    def __init__(self, mount_base: str, extensions: list, readonly: bool = True):
        self._mount_base = mount_base
        self._extensions = extensions
        self._readonly = readonly
        self._mounts: list[str] = []

    def mount_drives(self) -> bool:
        """Detect block devices and mount any that aren't already mounted.
        Returns True if at least one drive is mounted."""
        try:
            result = subprocess.run(
                ["lsblk", "-rno", "NAME,TYPE,MOUNTPOINT"],
                capture_output=True, text=True, check=True,
            )
        except FileNotFoundError:
            log.error("lsblk not found – cannot detect USB drives.")
            return False

        mounted_any = False
        index = 0
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name, dev_type = parts[0], parts[1]
            mountpoint = parts[2] if len(parts) > 2 else ""
            if dev_type != "part":
                continue
            if mountpoint:
                # Already mounted somewhere – track it
                if mountpoint not in self._mounts:
                    self._mounts.append(mountpoint)
                    log.info("Tracking already-mounted drive at %s", mountpoint)
                mounted_any = True
                continue
            # Try to mount it ourselves
            mount_path = f"{self._mount_base}{index}"
            os.makedirs(mount_path, exist_ok=True)
            opts = "ro" if self._readonly else "rw"
            cmd = ["mount", f"-o{opts}", f"/dev/{name}", mount_path]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                self._mounts.append(mount_path)
                log.info("Mounted /dev/%s at %s", name, mount_path)
                mounted_any = True
                index += 1
            except subprocess.CalledProcessError as exc:
                log.warning("Could not mount /dev/%s: %s", name, exc.stderr.decode().strip())
        return mounted_any

    def get_files(self) -> list[str]:
        files = []
        for mount in self._mounts:
            for entry in sorted(os.listdir(mount)):
                if any(entry.lower().endswith(f".{ext.strip()}") for ext in self._extensions):
                    files.append(os.path.join(mount, entry))
        return files

    def drive_count(self) -> int:
        return len(self._mounts)


class DirectoryReader:
    """Reads media files from a local directory and watches for changes."""

    def __init__(self, path: str, extensions: list):
        self._path = path
        self._extensions = extensions
        self._last_count = -1

    def get_files(self) -> list[str]:
        if not os.path.isdir(self._path):
            return []
        files = []
        for entry in sorted(os.listdir(self._path)):
            if any(entry.lower().endswith(f".{ext.strip()}") for ext in self._extensions):
                files.append(os.path.join(self._path, entry))
        return files

    def has_changed(self) -> bool:
        current = len(self.get_files())
        if current != self._last_count:
            self._last_count = current
            return True
        return False


# ---------------------------------------------------------------------------
# Copy-mode helper
# ---------------------------------------------------------------------------

def run_copymode(src_root: str, dst_dir: str, mode: str, password: str,
                 copy_loader: bool, osd_callback=None) -> bool:
    """Copy files from USB to local directory.
    Returns True if files were copied."""
    # Check password file
    if password:
        matches = [f for f in os.listdir(src_root)
                   if os.path.splitext(f)[0].lower() == password.lower()]
        if not matches:
            log.warning("Password file '%s' not found on USB drive – skipping copy.", password)
            return False

    # Determine effective mode (can be overridden by 'replace' or 'add' file on drive)
    effective_mode = mode
    for override in ("replace", "add"):
        if any(os.path.splitext(f)[0].lower() == override for f in os.listdir(src_root)):
            effective_mode = override
            log.info("Copy mode overridden by file on drive: %s", override)
            break

    os.makedirs(dst_dir, exist_ok=True)

    if effective_mode == "replace":
        for f in os.listdir(dst_dir):
            os.remove(os.path.join(dst_dir, f))
        log.info("Cleared destination directory for replace mode.")

    src_files = [f for f in os.listdir(src_root) if os.path.isfile(os.path.join(src_root, f))]
    total = len(src_files)

    for i, filename in enumerate(src_files):
        src = os.path.join(src_root, filename)
        dst = os.path.join(dst_dir, filename)
        log.info("Copying [%d/%d] %s ...", i + 1, total, filename)
        if osd_callback:
            osd_callback(f"Copying {i+1}/{total}: {filename}")
        subprocess.run(["cp", src, dst], check=True)

    if copy_loader:
        loader_src = os.path.join(src_root, "loader.png")
        if os.path.exists(loader_src):
            loader_dst = os.path.expanduser("~/.mpv_looper_background.png")
            subprocess.run(["cp", loader_src, loader_dst], check=True)
            log.info("Copied loader.png to %s", loader_dst)
            log.info("Copied loader.png as background.")

    return True


# ---------------------------------------------------------------------------
# Playlist
# ---------------------------------------------------------------------------

class Playlist:
    """Manages the ordered list of files to play, including M3U support,
    _repeat_Nx filename tags, randomisation, and resume."""

    def __init__(self, files: list[str], m3u_path: str = "",
                 is_random: bool = False, is_random_unique: bool = False,
                 resume: bool = False):
        self._raw_files = files
        self._titles: dict[str, str] = {}
        self._is_random = is_random
        self._is_random_unique = is_random_unique
        self._resume = resume
        self._index = 0
        self._unplayed: list[str] = []

        if m3u_path and os.path.exists(m3u_path):
            self._entries, self._titles = self._parse_m3u(m3u_path, files)
        else:
            self._entries = self._expand_repeats(files)

        if self._is_random and not self._is_random_unique:
            random.shuffle(self._entries)

        if resume:
            self._index = self._load_state()
        if is_random_unique:
            self._unplayed = list(self._entries)

    # ------------------------------------------------------------------
    def _expand_repeats(self, files: list[str]) -> list[str]:
        """Expand _repeat_Nx suffix into multiple entries."""
        expanded = []
        for f in files:
            stem = Path(f).stem
            if "_repeat_" in stem.lower():
                try:
                    tag = stem.lower().split("_repeat_")[-1].rstrip("x")
                    n = int(tag)
                    expanded.extend([f] * n)
                    continue
                except ValueError:
                    pass
            expanded.append(f)
        return expanded

    def _parse_m3u(self, path: str, fallback: list[str]):
        entries = []
        titles = {}
        pending_title = None
        m3u_dir = os.path.dirname(os.path.abspath(path))
        try:
            with open(path, "r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line == "#EXTM3U":
                        continue
                    if line.startswith("#EXTINF:"):
                        # Format: #EXTINF:duration,Title
                        parts = line.split(",", 1)
                        pending_title = parts[1] if len(parts) > 1 else None
                    elif not line.startswith("#"):
                        fpath = line if os.path.isabs(line) else os.path.join(m3u_dir, line)
                        if os.path.exists(fpath):
                            entries.append(fpath)
                            if pending_title:
                                titles[fpath] = pending_title
                                pending_title = None
        except OSError as e:
            log.warning("Could not parse playlist %s: %s – falling back to directory scan.", path, e)
            return fallback, {}
        if not entries:
            log.warning("Playlist %s yielded no valid files – falling back to directory scan.", path)
            return fallback, {}
        return entries, titles

    # ------------------------------------------------------------------
    def __len__(self):
        return len(self._entries)

    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def current_file(self) -> str | None:
        if self.is_empty():
            return None
        return self._entries[self._index % len(self._entries)]

    def current_title(self) -> str:
        f = self.current_file()
        if f is None:
            return ""
        return self._titles.get(f, Path(f).stem)

    def advance(self):
        if self.is_empty():
            return
        if self._is_random_unique and self._unplayed:
            self._unplayed.remove(self.current_file())
            if not self._unplayed:
                self._unplayed = list(self._entries)
            next_file = random.choice(self._unplayed)
            self._index = self._entries.index(next_file)
        elif self._is_random:
            self._index = random.randint(0, len(self._entries) - 1)
        else:
            self._index = (self._index + 1) % len(self._entries)
        if self._resume:
            self._save_state()

    def go_previous(self):
        if self.is_empty():
            return
        self._index = (self._index - 1) % len(self._entries)
        if self._resume:
            self._save_state()

    def jump_to(self, target):
        """Jump to absolute index, relative offset (+n/-n) or filename."""
        if self.is_empty():
            return
        if isinstance(target, int):
            self._index = target % len(self._entries)
        elif isinstance(target, str):
            if target.startswith("+"):
                self._index = (self._index + int(target[1:])) % len(self._entries)
            elif target.startswith("-"):
                self._index = (self._index - int(target[1:])) % len(self._entries)
            else:
                # Filename lookup
                for i, f in enumerate(self._entries):
                    if Path(f).name == target:
                        self._index = i
                        return
                log.warning("File not found in playlist: %s", target)
        if self._resume:
            self._save_state()

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as fh:
                fh.write(str(self._index))
        except OSError:
            pass

    def _load_state(self) -> int:
        try:
            with open(STATE_FILE, "r") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return 0


# ---------------------------------------------------------------------------
# MPV player wrapper
# ---------------------------------------------------------------------------

class MPVPlayer:
    """Wraps mpv (via subprocess) to play a single file."""

    def __init__(self, cfg: configparser.ConfigParser):
        self._audio_device = cfg_get(cfg, "mpv", "audio_device", "")
        self._volume = cfg_int(cfg, "mpv", "volume", 100)
        self._extra_args = cfg_get(cfg, "mpv", "extra_args", "").split()
        self._process: subprocess.Popen | None = None

    def play(self, filepath: str):
        args = [
            "mpv",
            "--fs",                          # fullscreen
            "--no-terminal",                  # suppress terminal output
            "--no-input-default-bindings",    # we handle input ourselves
            "--input-ipc-server=/tmp/mpvsocket",
            f"--volume={self._volume}",
        ]
        if self._audio_device:
            args.append(f"--audio-device={self._audio_device}")
        args += self._extra_args
        args.append(filepath)

        log.info("Playing: %s", filepath)
        self._process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def is_playing(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def wait(self):
        if self._process:
            self._process.wait()

    def _send_ipc(self, command: dict):
        """Send a JSON IPC command to a running mpv instance."""
        import json, socket
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect("/tmp/mpvsocket")
            sock.sendall((json.dumps(command) + "\n").encode())
            sock.close()
        except OSError:
            pass

    def pause(self):
        self._send_ipc({"command": ["cycle", "pause"]})

    def next_chapter(self):
        self._send_ipc({"command": ["add", "chapter", 1]})

    def prev_chapter(self):
        self._send_ipc({"command": ["add", "chapter", -1]})


# ---------------------------------------------------------------------------
# Image player
# ---------------------------------------------------------------------------

class ImagePlayer:
    """Displays static images via pygame for a configurable duration."""

    def __init__(self, cfg: configparser.ConfigParser, screen: pygame.Surface):
        self._screen = screen
        self._duration = cfg_int(cfg, "image_player", "duration", 5)
        self._scale = cfg_bool(cfg, "image_player", "scale", True)
        self._center = cfg_bool(cfg, "image_player", "center", True)
        self._playing = False
        self._stop_event = threading.Event()

    def play(self, filepath: str):
        self._stop_event.clear()
        self._playing = True
        try:
            img = pygame.image.load(filepath)
            w, h = self._screen.get_size()
            if self._scale:
                img = pygame.transform.smoothscale(img, (w, h))
            self._screen.fill((0, 0, 0))
            if self._center:
                rect = img.get_rect(center=(w // 2, h // 2))
            else:
                rect = img.get_rect()
            self._screen.blit(img, rect)
            pygame.display.flip()
        except pygame.error as e:
            log.warning("Could not load image %s: %s", filepath, e)

        deadline = time.monotonic() + self._duration
        while time.monotonic() < deadline and not self._stop_event.is_set():
            time.sleep(0.1)
        self._playing = False

    def is_playing(self) -> bool:
        return self._playing

    def stop(self):
        self._stop_event.set()

    def pause(self):
        pass  # Not meaningful for image player


# ---------------------------------------------------------------------------
# OSD renderer
# ---------------------------------------------------------------------------

class OSD:
    def __init__(self, screen: pygame.Surface, fgcolor: tuple, bgcolor: tuple,
                 bgimage_path: str = ""):
        self._screen = screen
        self._fg = fgcolor
        self._bg = bgcolor
        self._bgimage: pygame.Surface | None = None
        if bgimage_path and os.path.exists(bgimage_path):
            try:
                raw = pygame.image.load(bgimage_path)
                w, h = screen.get_size()
                self._bgimage = pygame.transform.smoothscale(raw, (w, h))
            except pygame.error as e:
                log.warning("Could not load bgimage: %s", e)
        w, h = screen.get_size()
        self._font_large = pygame.font.SysFont(None, max(48, h // 12))
        self._font_medium = pygame.font.SysFont(None, max(32, h // 18))
        self._font_small = pygame.font.SysFont(None, max(24, h // 26))

    def _draw_background(self):
        if self._bgimage:
            self._screen.blit(self._bgimage, (0, 0))
        else:
            self._screen.fill(self._bg)

    def show_message(self, lines: list[str]):
        self._draw_background()
        w, h = self._screen.get_size()
        total_h = len(lines) * (self._font_medium.get_linesize() + 6)
        y = (h - total_h) // 2
        for line in lines:
            surf = self._font_medium.render(line, True, self._fg)
            x = (w - surf.get_width()) // 2
            self._screen.blit(surf, (x, y))
            y += self._font_medium.get_linesize() + 6
        pygame.display.flip()

    def show_countdown(self, seconds: int, message: str = "Starting in"):
        for i in range(seconds, 0, -1):
            self.show_message([message, str(i)])
            time.sleep(1)

    def show_datetime(self, top_fmt: str, bottom_fmt: str):
        self._draw_background()
        w, h = self._screen.get_size()
        now = datetime.datetime.now()
        top_text = now.strftime(top_fmt)
        bot_text = now.strftime(bottom_fmt) if bottom_fmt else ""

        top_surf = self._font_large.render(top_text, True, self._fg)
        tx = (w - top_surf.get_width()) // 2
        if bot_text:
            gap = 16
            total = top_surf.get_height() + gap + self._font_small.get_linesize()
            ty = (h - total) // 2
        else:
            ty = (h - top_surf.get_height()) // 2
        self._screen.blit(top_surf, (tx, ty))

        if bot_text:
            bot_surf = self._font_small.render(bot_text, True, self._fg)
            bx = (w - bot_surf.get_width()) // 2
            by = ty + top_surf.get_height() + 16
            self._screen.blit(bot_surf, (bx, by))
        pygame.display.flip()

    def show_title(self, title: str, duration: int):
        """Overlay a title at the top of the screen for `duration` seconds."""
        w, _ = self._screen.get_size()
        surf = self._font_medium.render(title, True, self._fg)
        self._screen.blit(surf, ((w - surf.get_width()) // 2, 20))
        pygame.display.flip()
        # Title fades after duration (non-blocking – caller manages timing)

    def clear(self):
        self._draw_background()
        pygame.display.flip()


# ---------------------------------------------------------------------------
# GPIO controller
# ---------------------------------------------------------------------------

class GPIOController:
    def __init__(self, pin_map: dict, pin_mode_pullup: bool, callback):
        self._callback = callback
        self._pins: dict[int, object] = {}
        if not GPIO_AVAILABLE:
            log.warning("RPi.GPIO not available – GPIO control disabled.")
            return
        GPIO.setmode(GPIO.BOARD)
        pull = GPIO.PUD_UP if pin_mode_pullup else GPIO.PUD_DOWN
        edge = GPIO.FALLING if pin_mode_pullup else GPIO.RISING
        for pin_str, action in pin_map.items():
            pin = int(pin_str.strip('"').strip())
            self._pins[pin] = action
            GPIO.setup(pin, GPIO.IN, pull_up_down=pull)
            GPIO.add_event_detect(
                pin, edge, callback=self._make_handler(pin), bouncetime=200
            )
        log.info("GPIO controller initialised with pins: %s", list(self._pins.keys()))

    def _make_handler(self, pin: int):
        def handler(_channel):
            action = self._pins.get(pin)
            if action is not None:
                log.info("GPIO pin %d triggered → %s", pin, action)
                self._callback(action)
        return handler

    def cleanup(self):
        if GPIO_AVAILABLE:
            GPIO.cleanup()


def parse_gpio_map(raw: str) -> dict:
    """Parse the gpio_pin_map string from ini into a dict."""
    if not raw.strip():
        return {}
    result = {}
    try:
        # Evaluate as a dict literal by wrapping with braces
        parsed = eval("{" + raw + "}", {}, {})
        for k, v in parsed.items():
            result[str(k)] = v
    except Exception as e:
        log.warning("Could not parse gpio_pin_map: %s – %s", raw, e)
    return result


# ---------------------------------------------------------------------------
# Keyboard command map
# ---------------------------------------------------------------------------

PYGAME_KEY_MAP = {
    "K_ESCAPE": pygame.K_ESCAPE,
    "K_k": pygame.K_k,
    "K_b": pygame.K_b,
    "K_s": pygame.K_s,
    "K_p": pygame.K_p,
    "K_SPACE": pygame.K_SPACE,
    "K_o": pygame.K_o,
    "K_i": pygame.K_i,
}


# ---------------------------------------------------------------------------
# Main looper
# ---------------------------------------------------------------------------

class VideoLooper:
    def __init__(self, config_path: str = CONFIG_PATH):
        self._cfg = load_config(config_path)
        self._running = True
        self._paused = False
        self._stopped = False  # one_shot stop state
        self._current_player = None
        self._image_player_thread: threading.Thread | None = None

        # --- Core settings ---
        self._file_reader_type = cfg_get(self._cfg, "video_looper", "file_reader", "usb_drive")
        self._osd_enabled = cfg_bool(self._cfg, "video_looper", "osd", True)
        self._countdown = cfg_int(self._cfg, "video_looper", "countdown_time", 5)
        self._wait_time = cfg_int(self._cfg, "video_looper", "wait_time", 0)
        self._datetime_display = cfg_bool(self._cfg, "video_looper", "datetime_display", False)
        self._top_dt_fmt = cfg_get(self._cfg, "video_looper", "top_datetime_display_format", "%H:%M:%S")
        self._bot_dt_fmt = cfg_get(self._cfg, "video_looper", "bottom_datetime_display_format", "%A %d %B %Y")
        self._is_random = cfg_bool(self._cfg, "video_looper", "is_random", False)
        self._is_random_unique = cfg_bool(self._cfg, "video_looper", "is_random_unique", False)
        self._resume = cfg_bool(self._cfg, "video_looper", "resume_playlist", False)
        self._one_shot = cfg_bool(self._cfg, "video_looper", "one_shot_playback", False)
        self._play_on_startup = cfg_bool(self._cfg, "video_looper", "play_on_startup", True)
        self._bgimage = cfg_get(self._cfg, "video_looper", "bgimage", "")
        self._bgcolor = cfg_color(self._cfg, "video_looper", "bgcolor", (0, 0, 0))
        self._fgcolor = cfg_color(self._cfg, "video_looper", "fgcolor", (255, 255, 255))
        self._console_output = cfg_bool(self._cfg, "video_looper", "console_output", False)

        if self._console_output:
            log.setLevel(logging.DEBUG)

        # --- Control settings ---
        self._keyboard_ctrl = cfg_bool(self._cfg, "control", "keyboard_control", True)
        self._keyboard_disabled_during = cfg_bool(self._cfg, "control", "keyboard_control_disabled_while_playback", False)
        self._gpio_disabled_during = cfg_bool(self._cfg, "control", "gpio_control_disabled_while_playback", False)
        gpio_raw = cfg_get(self._cfg, "control", "gpio_pin_map", "")
        self._gpio_map = parse_gpio_map(gpio_raw)
        self._gpio_pin_mode_pullup = cfg_bool(self._cfg, "control", "gpio_pin_mode", True)

        # --- Player type ---
        # The ini [video_looper] video_player key can be: mpv or image_player.
        # Treat legacy values (omxplayer, hello_video) as mpv.
        raw_player = cfg_get(self._cfg, "video_looper", "video_player", "mpv")
        self._player_type = "image_player" if raw_player == "image_player" else "mpv"
        if self._player_type == "image_player":
            self._extensions = [e.strip() for e in cfg_get(self._cfg, "image_player", "extensions", "jpg,jpeg,png,gif,bmp,webp").split(",")]
        else:
            self._extensions = [e.strip() for e in cfg_get(self._cfg, "mpv", "extensions", "mp4,mkv,avi,mov,webm,flv,wmv,ts,m2ts,m4v").split(",")]

        # --- Playlist settings ---
        self._m3u_path = cfg_get(self._cfg, "playlist", "path", "")
        self._show_titles = cfg_bool(self._cfg, "mpv", "show_titles", False)
        self._title_duration = cfg_int(self._cfg, "mpv", "title_duration", 4)

        # --- Initialise pygame display ---
        pygame.init()
        os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
        os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
        pygame.mouse.set_visible(False)
        info = pygame.display.Info()
        try:
            self._screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        except pygame.error:
            self._screen = pygame.display.set_mode((1920, 1080))
        pygame.display.set_caption("MPV Video Looper")

        self._osd = OSD(self._screen, self._fgcolor, self._bgcolor, self._bgimage)

        # --- MPV player instance ---
        self._mpv = MPVPlayer(self._cfg)

        # --- GPIO ---
        self._gpio_ctrl: GPIOController | None = None
        if self._gpio_map:
            self._gpio_ctrl = GPIOController(
                self._gpio_map,
                self._gpio_pin_mode_pullup,
                self._handle_gpio_action,
            )

        # --- Build file reader ---
        mount_path = cfg_get(self._cfg, "usb_drive", "mount_path", "/mnt/usbdrive")
        default_video_dir = os.path.join(os.path.expanduser("~"), "video")
        dir_path = cfg_get(self._cfg, "directory", "path", default_video_dir)
        self._dir_path = dir_path
        self._mount_path = mount_path
        self._usb_reader: USBDriveReader | None = None
        self._dir_reader: DirectoryReader | None = None

        self._playlist: Playlist | None = None

        # --- Signal handlers ---
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

        # --- Pending GPIO action ---
        self._gpio_action = None
        self._gpio_lock = threading.Lock()

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def _find_files(self) -> list[str]:
        if self._file_reader_type == "usb_drive":
            if self._usb_reader is None:
                readonly = cfg_bool(self._cfg, "usb_drive", "readonly", True)
                self._usb_reader = USBDriveReader(self._mount_path, self._extensions, readonly)
            if not self._usb_reader.mount_drives():
                return []
            return self._usb_reader.get_files()

        elif self._file_reader_type == "directory":
            if self._dir_reader is None:
                self._dir_reader = DirectoryReader(self._dir_path, self._extensions)
            return self._dir_reader.get_files()

        elif self._file_reader_type == "usb_drive_copymode":
            readonly = cfg_bool(self._cfg, "usb_drive", "readonly", True)
            usb = USBDriveReader(self._mount_path, self._extensions, False)
            if not usb.mount_drives():
                return []
            # Copy from first USB mount
            src = self._mount_path + "0"
            password = cfg_get(self._cfg, "copymode", "password", "videopi")
            mode = cfg_get(self._cfg, "copymode", "mode", "replace")
            copy_loader = cfg_bool(self._cfg, "copymode", "copyloader", False)
            run_copymode(src, self._dir_path, mode, password, copy_loader,
                         osd_callback=lambda msg: self._osd.show_message([msg]))
            if self._dir_reader is None:
                self._dir_reader = DirectoryReader(self._dir_path, self._extensions)
            return self._dir_reader.get_files()

        return []

    # ------------------------------------------------------------------
    # Keyboard / GPIO
    # ------------------------------------------------------------------

    def _handle_key(self, key: int):
        """Dispatch a pygame key to a looper action."""
        if key == pygame.K_ESCAPE:
            log.info("ESC: stopping looper.")
            self._running = False
            self._stop_current()

        elif key == pygame.K_k:  # skip
            log.info("K: skipping to next file.")
            self._skip_to_next()

        elif key == pygame.K_b:  # back
            log.info("B: going back to previous file.")
            self._skip_to_prev()

        elif key == pygame.K_s:  # stop/start
            if self._stopped:
                log.info("S: resuming playback.")
                self._stopped = False
            else:
                log.info("S: stopping playback.")
                self._stopped = True
                self._stop_current()

        elif key == pygame.K_p:  # power off
            log.info("P: shutting down.")
            self._stop_current()
            subprocess.run(["sudo", "shutdown", "-h", "now"])

        elif key == pygame.K_SPACE:  # pause/resume
            log.info("SPACE: toggling pause.")
            self._toggle_pause()

        elif key == pygame.K_o:  # next chapter
            if self._current_player and isinstance(self._current_player, MPVPlayer):
                self._current_player.next_chapter()

        elif key == pygame.K_i:  # previous chapter
            if self._current_player and isinstance(self._current_player, MPVPlayer):
                self._current_player.prev_chapter()

    def _handle_gpio_action(self, action):
        """Queue a GPIO action for processing on the main thread."""
        with self._gpio_lock:
            self._gpio_action = action

    def _process_gpio_action(self, action):
        if isinstance(action, str) and action.startswith("K_"):
            key = PYGAME_KEY_MAP.get(action)
            if key:
                self._handle_key(key)
        elif self._playlist:
            self._playlist.jump_to(action)
            self._stop_current()

    def _stop_current(self):
        if self._current_player:
            if isinstance(self._current_player, MPVPlayer):
                self._current_player.stop()
            elif isinstance(self._current_player, ImagePlayer):
                self._current_player.stop()

    def _skip_to_next(self):
        if self._playlist:
            self._playlist.advance()
        self._stop_current()

    def _skip_to_prev(self):
        if self._playlist:
            self._playlist.go_previous()
        self._stop_current()

    def _toggle_pause(self):
        self._paused = not self._paused
        if self._current_player:
            if isinstance(self._current_player, MPVPlayer):
                self._current_player.pause()
            elif isinstance(self._current_player, ImagePlayer):
                self._current_player.pause()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        log.info("mpv_video_looper starting.")

        # Wait for files
        while self._running:
            files = self._find_files()
            if files:
                break
            if self._osd_enabled:
                self._osd.show_message(["Waiting for media files...", "Insert USB drive or add files to directory."])
            self._process_events()
            time.sleep(2)

        if not self._running:
            return

        log.info("Found %d media files.", len(files))

        if self._osd_enabled:
            self._osd.show_message([f"Found {len(files)} file(s)", "Preparing to play..."])
            if self._countdown > 0:
                self._osd.show_countdown(self._countdown)

        self._playlist = Playlist(
            files,
            m3u_path=self._m3u_path,
            is_random=self._is_random,
            is_random_unique=self._is_random_unique,
            resume=self._resume,
        )

        if not self._play_on_startup:
            log.info("play_on_startup=false – waiting for trigger.")
            self._stopped = True

        # Main playback loop
        while self._running:
            # Re-scan for file changes in directory mode
            if self._file_reader_type == "directory" and self._dir_reader and self._dir_reader.has_changed():
                log.info("Directory contents changed – rebuilding playlist.")
                files = self._dir_reader.get_files()
                self._playlist = Playlist(files, m3u_path=self._m3u_path,
                                          is_random=self._is_random,
                                          is_random_unique=self._is_random_unique,
                                          resume=self._resume)

            if self._stopped or self._playlist.is_empty():
                self._show_idle()
                self._process_events()
                time.sleep(0.1)
                continue

            filepath = self._playlist.current_file()
            if not filepath or not os.path.exists(filepath):
                log.warning("File not found: %s – skipping.", filepath)
                self._playlist.advance()
                continue

            # Play the file
            if self._player_type == "image_player":
                self._play_image(filepath)
            else:
                self._play_video(filepath)

            if not self._running:
                break

            if self._one_shot:
                self._stopped = True
                continue

            # Wait between files
            if self._wait_time > 0:
                self._show_between_wait()

            self._playlist.advance()

        self._cleanup()

    def _play_video(self, filepath: str):
        player = MPVPlayer(self._cfg)
        self._current_player = player
        player.play(filepath)

        if self._show_titles:
            title = self._playlist.current_title() if self._playlist else Path(filepath).stem
            # Title is shown via OSD briefly (non-blocking overlay)
            self._osd.show_title(title, self._title_duration)

        while player.is_playing() and self._running and not self._stopped:
            self._process_events()
            time.sleep(0.05)

        if player.is_playing():
            player.stop()
        self._current_player = None

    def _play_image(self, filepath: str):
        img_player = ImagePlayer(self._cfg, self._screen)
        self._current_player = img_player
        t = threading.Thread(target=img_player.play, args=(filepath,), daemon=True)
        t.start()
        while img_player.is_playing() and self._running and not self._stopped:
            self._process_events()
            time.sleep(0.05)
        if img_player.is_playing():
            img_player.stop()
        t.join(timeout=2)
        self._current_player = None

    def _show_idle(self):
        if self._datetime_display:
            self._osd.show_datetime(self._top_dt_fmt, self._bot_dt_fmt)
        else:
            self._osd.clear()

    def _show_between_wait(self):
        deadline = time.monotonic() + self._wait_time
        while time.monotonic() < deadline and self._running:
            if self._datetime_display:
                self._osd.show_datetime(self._top_dt_fmt, self._bot_dt_fmt)
            else:
                self._osd.clear()
            self._process_events()
            time.sleep(0.1)

    def _process_events(self):
        # Process GPIO queue
        with self._gpio_lock:
            action = self._gpio_action
            self._gpio_action = None
        if action is not None:
            is_playing = self._current_player and (
                (isinstance(self._current_player, MPVPlayer) and self._current_player.is_playing()) or
                (isinstance(self._current_player, ImagePlayer) and self._current_player.is_playing())
            )
            if not (is_playing and self._gpio_disabled_during):
                self._process_gpio_action(action)

        # Process pygame events (keyboard)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self._running = False
            elif event.type == pygame.KEYDOWN and self._keyboard_ctrl:
                is_playing = self._current_player and (
                    (isinstance(self._current_player, MPVPlayer) and self._current_player.is_playing()) or
                    (isinstance(self._current_player, ImagePlayer) and self._current_player.is_playing())
                )
                if not (is_playing and self._keyboard_disabled_during):
                    self._handle_key(event.key)

    def _on_signal(self, signum, frame):
        log.info("Received signal %d – shutting down.", signum)
        self._running = False
        self._stop_current()

    def _cleanup(self):
        log.info("Cleaning up.")
        self._stop_current()
        if self._gpio_ctrl:
            self._gpio_ctrl.cleanup()
        pygame.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else CONFIG_PATH
    looper = VideoLooper(config_path)
    looper.run()


if __name__ == "__main__":
    main()
