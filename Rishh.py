import asyncio, json, os, random, time, sys
import re
import telegram.error
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.ext import Application, ContextTypes, MessageHandler, filters, CallbackQueryHandler
import logging
from dataclasses import dataclass, field
from typing import Optional
from collections import OrderedDict

if sys.platform == 'win32':
    os.system('color 08')
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass

C_DARK = "\033[40;90m"
C_GRAY = "\033[40;37m"
C_RESET = "\033[0m"


def _enable_virtual_terminal(stream):
    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError):
        return False
    if sys.platform != "win32":
        return os.environ.get("TERM", "") != "dumb"
    try:
        import ctypes
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


class TerminalDashboard:
    """Small in-place status screen for interactive terminals."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_MAGENTA = "\033[95m"

    def __init__(self, stream=None, enabled=None):
        self.stream = stream or sys.stdout
        if enabled is None:
            enabled = _enable_virtual_terminal(self.stream)
        self.enabled = enabled
        self.started_at = time.monotonic()
        self.status = "Starting"
        self.health = "Starting"
        self.bots = OrderedDict()
        self.events = []
        self.last_screen = None

    @staticmethod
    def _plain(text):
        return re.sub(r"\033\[[0-?]*[ -/]*[@-~]", "", text).strip()

    def _capture(self, text):
        plain = self._plain(text)
        if "[HEALTH] Listening" in plain:
            self.health = plain.split("[HEALTH] Listening", 1)[1].strip()
        if "[SUCCESS] Bot running: @" in plain:
            username = plain.split("[SUCCESS] Bot running: @", 1)[1].strip()
            self.bots[username] = "online"
        if "[START] Rxs Multi-Bot Engine running" in plain:
            self.status = "Running"
        elif "[STOP]" in plain:
            self.status = "Stopping"
        elif "[FATAL]" in plain or "[ERROR] No bots could be started" in plain:
            self.status = "Needs attention"

        if plain.startswith((
            "[FLOODWAIT]", "[INVALID REQUEST]", "[PERMISSION]", "[NET]",
            "[API ERROR]", "[FAILED]", "[ERROR]", "[CONFLICT]",
        )):
            self.events.append(plain)
            del self.events[:-5]

    def _screen(self):
        uptime = max(0, int(time.monotonic() - self.started_at))
        hours, remainder = divmod(uptime, 3600)
        minutes, seconds = divmod(remainder, 60)
        states = globals().get("bot_api_states", {})
        now = time.monotonic()
        cooldowns = [
            (bot_id, max(0, int(state.cooldown_until - now + 0.999)))
            for bot_id, state in states.items()
            if isinstance(bot_id, int) and state.cooldown_until > now
        ]

        lines = [
            "RXSV BOT CONTROL",
            "=" * 64,
            "RUNTIME",
            f"  Status: {self.status}    Uptime: {hours:02}:{minutes:02}:{seconds:02}",
            f"  Health: {self.health}",
            f"  Bots online: {len(self.bots)}",
            "",
            f"BOTS ({len(self.bots)})",
        ]
        names = list(self.bots)
        if names:
            lines.extend(
                "  " + "    ".join(f"@{name}" for name in names[index:index + 2])
                for index in range(0, len(names), 2)
            )
        else:
            lines.append("  Waiting for startup")
        lines.extend(["", "API COORDINATION"])
        if cooldowns:
            lines.extend(f"  Bot {bot_id}: Telegram cooldown, {remaining}s remaining" for bot_id, remaining in cooldowns)
        else:
            lines.append("  No active Telegram cooldowns")
        lines.extend(["", "RECENT EVENTS"])
        if self.events:
            lines.extend(f"  {event[:75]}..." if len(event) > 78 else f"  {event}" for event in self.events)
        else:
            lines.append("  No recent errors or cooldowns")
        lines.extend(["", "Screen refreshes in place; Ctrl+C stops the bot."])
        return "\n".join(lines)

    def _style(self, screen):
        styled = []
        section_headers = {"RUNTIME", f"BOTS ({len(self.bots)})", "API COORDINATION", "RECENT EVENTS"}
        for line in screen.splitlines():
            if line == "RXSV BOT CONTROL" or line in section_headers:
                color = self.BRIGHT_CYAN
                line = f"{self.BOLD}{color}{line}{self.RESET}"
            elif line and set(line) == {"="}:
                line = f"{self.BRIGHT_BLUE}{line}{self.RESET}"
            elif "Status: Running" in line:
                line = line.replace("Running", f"{self.BOLD}{self.BRIGHT_GREEN}Running{self.RESET}")
            elif "Status: Needs attention" in line:
                line = line.replace("Needs attention", f"{self.BOLD}{self.BRIGHT_RED}Needs attention{self.RESET}")
            elif "Health:" in line:
                line = line.replace(self.health, f"{self.BRIGHT_GREEN}{self.health}{self.RESET}")
            elif "Telegram cooldown" in line:
                line = f"{self.BRIGHT_YELLOW}{line}{self.RESET}"
            elif line.strip().startswith(("[INVALID REQUEST]", "[PERMISSION]", "[FAILED]", "[ERROR]", "[CONFLICT]")):
                line = f"{self.BRIGHT_RED}{line}{self.RESET}"
            elif line.strip().startswith(("[FLOODWAIT]", "[NET]", "[API ERROR]")):
                line = f"{self.BRIGHT_YELLOW}{line}{self.RESET}"
            elif line.strip().startswith("@"):
                line = f"{self.BRIGHT_GREEN}{line}{self.RESET}"
            styled.append(line)
        return "\n".join(styled)

    def render(self, clear=False):
        if not self.enabled:
            return
        screen = self._screen()
        if screen == self.last_screen:
            return
        prefix = "\033[2J\033[H" if clear or self.last_screen is None else "\033[H\033[J"
        self.stream.write(prefix + self._style(screen) + "\n")
        self.stream.flush()
        self.last_screen = screen

    def write(self, text):
        self._capture(text)
        if self.enabled:
            self.render()
        else:
            print(text, flush=True)


_console_dashboard = TerminalDashboard()


def console_print(text=""):
    """Print ASCII-only console output while preserving ANSI color escapes."""
    # Telegram errors can contain request URLs. Never allow a bot token to be
    # copied into console logs, even if a caller accidentally includes one.
    redacted_text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_TOKEN]", str(text))
    safe_text = redacted_text.encode("ascii", errors="backslashreplace").decode("ascii")
    _console_dashboard.write(safe_text)


_repeated_console_logs = OrderedDict()


def _console_print_repeated(key, text, interval=30.0):
    """Compact identical recurring errors while reporting how many were skipped."""
    now = time.monotonic()
    previous = _repeated_console_logs.get(key)
    if previous is not None and now - previous[0] < interval:
        _repeated_console_logs[key] = (previous[0], previous[1] + 1)
        _repeated_console_logs.move_to_end(key)
        return

    suffix = f" (suppressed {previous[1]} repeats)" if previous and previous[1] else ""
    console_print(f"{text}{suffix}")
    _repeated_console_logs[key] = (now, 0)
    _repeated_console_logs.move_to_end(key)
    while len(_repeated_console_logs) > 256:
        _repeated_console_logs.popitem(last=False)

_DEFAULT_OWNER_ID = 6002928421
try:
    OWNER_ID = int(os.environ.get("OWNER_ID", str(_DEFAULT_OWNER_ID)).strip())
except Exception:
    OWNER_ID = _DEFAULT_OWNER_ID
SUDO_FILE = "Rxsdmins.json"

TOKEN_PATTERN = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")


def _parse_bot_tokens(raw_tokens):
    """Extract all valid Telegram bot tokens from any format (newlines, commas, JSON, quotes, BOM), ignoring # comments."""
    if not raw_tokens:
        return []
    raw_str = str(raw_tokens).lstrip("\ufeff").strip()
    tokens = []
    seen = set()
    for line in raw_str.splitlines():
        line = line.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        # Strip inline comment if any
        if " #" in line:
            line = line.split(" #", 1)[0]
        for match in TOKEN_PATTERN.findall(line):
            if match not in seen:
                seen.add(match)
                tokens.append(match)
    return tokens


def _load_bot_tokens(environ_value=None, token_file=None):
    """Load bot tokens from environment variables or Tokens.txt (case-insensitive, UTF-8 BOM safe)."""
    if environ_value is not None:
        raw_tokens = environ_value
    else:
        raw_tokens = ""
        for env_key in (
            "TELEGRAM_BOT_TOKENS",
            "TELEGRAM_BOT_TOKEN",
            "BOT_TOKENS",
            "BOT_TOKEN",
            "TOKEN",
            "TOKENS",
            "TG_BOT_TOKEN",
        ):
            val = os.environ.get(env_key, "").strip()
            if val:
                raw_tokens = val
                break

    if raw_tokens.strip():
        parsed = _parse_bot_tokens(raw_tokens)
        if parsed:
            return parsed

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    if token_file:
        candidates.append(token_file)
    for name in ("Tokens.txt", "tokens.txt", "Token.txt", "token.txt", "TOKENS.TXT", "TOKENS.txt"):
        candidates.append(os.path.join(base_dir, name))
        candidates.append(name)
    # Also scan directory for any case-variant of tokens.txt
    try:
        for fname in os.listdir(base_dir):
            if fname.lower() in ("tokens.txt", "token.txt"):
                full_p = os.path.join(base_dir, fname)
                if full_p not in candidates:
                    candidates.append(full_p)
    except Exception:
        pass

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8-sig", errors="ignore") as tokens_file:
                    parsed = _parse_bot_tokens(tokens_file.read())
                    if parsed:
                        return parsed
            except Exception:
                continue
    return []


TOKENS = _load_bot_tokens()


def validate_bot_tokens(tokens, raise_on_empty=False):
    if not tokens:
        if raise_on_empty:
            raise RuntimeError("Set BOT_TOKEN or TELEGRAM_BOT_TOKENS in environment variables or add tokens to Tokens.txt.")
        return False
    valid = [t for t in tokens if TOKEN_PATTERN.fullmatch(t)]
    return bool(valid)

HEARTS = ["🩷", "❤️", "🧡", "💛", "💚", "🩵", "💙", "💜", "🖤", "🩶", "🤎", "🤍"]
def h(): return random.choice(HEARTS)

chars = {
    'a': '𝙖', 'b': '𝙗', 'c': '𝙘', 'd': '𝙙', 'e': '𝙚', 'f': '𝙛', 'g': '𝙜',
    'h': '𝙝', 'i': '𝙞', 'j': '𝙟', 'k': '𝙠', 'l': '𝙡', 'm': '𝙢', 'n': '𝙣',
    'o': '𝙤', 'p': '𝙥', 'q': '𝙦', 'r': '𝙧', 's': '𝙨', 't': '𝙩', 'u': '𝙪',
    'v': '𝙫', 'w': '𝙬', 'x': '𝙭', 'y': '𝙮', 'z': '𝙯',
    'A': '𝘼', 'B': '𝘽', 'C': '𝘾', 'D': '𝘿', 'E': '𝙀', 'F': '𝙁', 'G': '𝙂',
    'H': '𝙃', 'I': '𝙄', 'J': '𝙅', 'K': '𝙆', 'L': '𝙇', 'M': '𝙈', 'N': '𝙉',
    'O': '𝙊', 'P': '𝙋', 'Q': '𝙌', 'R': '𝙍', 'S': '𝙎', 'T': '𝙏', 'U': '𝙐',
    'V': '𝙑', 'W': '𝙒', 'X': '𝙓', 'Y': '𝙔', 'Z': '𝙕',
    '0': '𝟬', '1': '𝟭', '2': '𝟮', '3': '𝟯', '4': '𝟰', '5': '𝟱', '6': '𝟲',
    '7': '𝟳', '8': '𝟴', '9': '𝟵'
}

def font(text: str) -> str:
    return "".join(chars.get(c, c) for c in text)

RAID_TEXTS = [" 𝐊𝐎 𝐏𝐄𝐋𝐓𝐄 𝐇𝐔𝐄 𝐑𝐗𝐒 𝐏𝐀𝐏𝐀 𝐊𝐈 𝐄𝐍𝐓𝐑𝐘😎❤️‍🔥"," 𝐓𝐄𝐑𝐈 𝐌𝐀𝐀 𝐊𝐈 ̷C̷̷H̷̷U̷̷T̷ 𝗠𝗘 𝐋𝐎U𝐃𝐀 ̷M̷̷A̷̷D̷̷A̷̷R̷̷C̷̷H̷̷O̷̷D̷🤣🩷🤚🏼"," 𝐑𝐄𝐏𝐋𝐘 𝐊𝐀𝐑 𝐆𝐀𝐑𝐈𝐁 𝐃𝐀𝐑 𝐊𝐘𝐔 𝐑𝐀𝐇𝐀 𝐇?😂🤙🏼🤍"," 𝐂𝐇𝐀𝐋 𝐓𝐄𝐑𝐈 𝐌𝐀 ̷C̷̷H̷̷O̷̷D̷̷U̷ 𝐏𝐀𝐓𝐀𝐊 𝐏𝐀𝐓𝐀𝐊 𝐊𝐄🤣👻🩶"," ̷C̷̷H̷̷U̷̷D̷̷K̷̷E̷ 𝐒𝐏𝐀𝐌? 𝐘𝐀𝐇𝐈 𝐀𝐔𝐊𝐀𝐓 𝐇 𝐓𝐄𝐑𝐈 𝐆𝐀𝐑𝐈𝐁🤣🩵🙌🏼","𝐂𝐏 𝐊𝐀𝐑  𝐆𝐀𝐑𝐈𝐁 𝐁𝐇𝐀𝐀𝐆 𝐌𝐀𝐓 𝐂𝐇𝐎𝐓𝐄𝐘😂🩶🤚🏼"," Teri mummy को चोदके ̷r̷a̷̷n̷̷d̷̷i̷ बना दूंगा hehehe 🤣🩷✌🏼"," 𝐊𝐄 𝐁𝐀𝐀𝐏 ( 𝐑𝐗𝐒 ) 𝐘𝐄𝐇 𝐇𝐀𝐈 𝐈𝐍𝐊𝐈 𝐌𝐀𝐀 𝐊𝐄 𝐘𝐀𝐀𝐑🤣🩶🤚🏼","𝐊𝐀𝐁𝐀𝐃𝐈 𝐖𝐀𝐋𝐄  𝐊𝐈 𝐌𝐊𝐁😂👻🩷","  𝐊𝐈 𝐌𝐊𝐁 𝐘𝐀𝐀𝐑 𝐁𝐇𝐀𝐆 𝐊𝐀𝐈𝐒𝐄 𝐑𝐇𝐄 𝐇𝐎 𝐆𝐀𝐑𝐈𝐁𝐎😤👻🩷"," 𝐊𝐎 𝐏𝐄𝐋𝐓𝐄 𝐇𝐔𝐄 𝐑𝐗𝐒 𝐏𝐀𝐏𝐀 𝐊𝐈 𝐄𝐗𝐈𝐓😎❤️‍🔥"," 𝐊𝐎 𝐏𝐄𝐋𝐓𝐄 𝐇𝐔𝐄 𝐑𝐗𝐒 𝐏𝐀𝐏𝐀 𝐊𝐈 𝐄𝐍𝐓𝐑𝐘😎❤️‍🔥"," 𝐓𝐄𝐑𝐈 𝐌𝐀𝐀 𝐊𝐈 ̷C̷̷H̷̷U̷̷T̷ 𝗠𝗘 𝐋𝐎U𝐃𝐀 ̷M̷̷A̷̷D̷̷A̷̷R̷̷C̷̷H̷̷O̷̷D̷🤣🩷🤚🏼"," 𝐑𝐄𝐏𝐋𝐘 𝐊𝐀𝐑 𝐆𝐀𝐑𝐈𝐁 𝐃𝐀𝐑 𝐊𝐘𝐔 𝐑𝐀𝐇𝐀 𝐇?😂🤙🏼🤍"," 𝐂𝐇𝐀𝐋 𝐓𝐄𝐑𝐈 𝐌𝐀 ̷C̷̷H̷̷O̷̷D̷̷U̷ 𝐏𝐀𝐓𝐀𝐊 𝐏𝐀𝐓𝐀𝐊 𝐊𝐄🤣👻🩶"," C̷̷H̷̷U̷̷D̷̷K̷̷E̷ 𝐒𝐏𝐀𝐌? 𝐘𝐀𝐇𝐈 𝐀𝐔𝐊𝐀𝐓 𝐇 𝐓𝐄𝐑𝐈 𝐆𝐀𝐑𝐈𝐁🤣🩵🙌🏼","𝐂𝐏 𝐊𝐀𝐑  𝐆𝐀𝐑𝐈𝐁 𝐁𝐇𝐀𝐀𝐆 𝐌𝐀𝐓 𝐂𝐇𝐎𝐓𝐄𝐘😂🩶🤚🏼"," Teri mummy को चोदके ̷r̷a̷̷n̷̷d̷̷i̷ बना दूंगा hehehe🤣🩷✌🏼"," 𝐊𝐄 𝐁𝐀𝐀𝐏 (𝐑𝐗𝐒) 𝐘𝐄𝐇 𝐇𝐀𝐈 𝐈𝐍𝐊𝐈 𝐌𝐀𝐀 𝐊𝐄 𝐘𝐀𝐀𝐑 🤣🩶🤚🏼"," 𝐊𝐄 𝐆𝐀𝐑𝐈𝐁 𝐁𝐇𝐀𝐆 𝐊𝐀𝐈𝐒𝐄 𝐑𝐀𝐇𝐀 𝐇 𝐑𝐄𝐏𝐋𝐘 𝐊𝐑 𝐂𝐇𝐎𝐓𝐄𝐘 😂👻🩷"," 𝐁𝐇𝐀𝐆𝐍𝐀 𝐍𝐘 𝐇 ̷M̷̷A̷̷D̷̷A̷̷R̷̷C̷̷H̷̷O̷̷D̷ 𝐓𝐄𝐑𝐈 𝐌𝐀 𝐌𝐀𝐑 𝐃𝐔𝐍𝐆𝐀 𝐁𝐇𝐀𝐆𝐀 𝐓𝐎𝐇😤👻🩷"," 𝐊𝐎 𝐏𝐄𝐋𝐓𝐄 𝐇𝐔𝐄 𝐑𝐗𝐒 𝐏𝐀𝐏𝐀 𝐊𝐈 𝐄𝐗𝐈𝐓😎❤️‍🔥"," 𝐊𝐎 𝐏𝐄𝐋𝐓𝐄 𝐇𝐔𝐄 𝐑𝐗𝐒 𝐏𝐀𝐏𝐀 𝐊𝐈 𝐄𝐍𝐓𝐑𝐘😎❤️‍🔥"," 𝐓𝐄𝐑𝐈 𝐌𝐀𝐀 𝐊𝐈 ̷C̷̷H̷̷U̷̷T̷ 𝗠𝗘 𝐋𝐎U𝐃𝐀 ̷M̷̷A̷̷D̷̷A̷̷R̷̷C̷̷H̷̷O̷̷D̷🤣🩷🤚🏼"," 𝐑𝐄𝐏𝐋𝐘 𝐊𝐀𝐑 𝐆𝐀𝐑𝐈𝐁 𝐃𝐀𝐑 𝐊𝐘𝐔 𝐑𝐀𝐇𝐀 𝐇?😂🤙🏼🤍"," 𝐂𝐇𝐀𝐋 𝐓𝐄𝐑𝐈 𝐌𝐀 ̷C̷̷H̷̷O̷̷D̷̷U̷ 𝐏𝐀𝐓𝐀𝐊 𝐏𝐀𝐓𝐀𝐊 𝐊𝐄🤣👻🩶"," C̷̷H̷̷U̷̷D̷̷K̷̷E̷ 𝐒𝐏𝐀𝐌? 𝐘𝐀𝐇𝐈 𝐀𝐔𝐊𝐀𝐓 𝐇 𝐓𝐄𝐑𝐈 𝐆𝐀𝐑𝐈𝐁🤣🩵🙌🏼","𝐂𝐏 𝐊𝐀𝐑  𝐆𝐀𝐑𝐈𝐁 𝐁𝐇𝐀𝐀𝐆 𝐌𝐀𝐓 𝐂𝐇𝐎𝐓𝐄𝐘😂🩶🤚🏼"," Teri mummy को चोदके ̷r̷a̷̷n̷̷d̷̷i̷ बना दूंगा hehehe🤣🩷✌🏼"," 𝐊𝐄 𝐁𝐀𝐀𝐏 (𝐑𝐗𝐒) 𝐘𝐄𝐇 𝐇𝐀𝐈 𝐈𝐍𝐊𝐈 𝐌𝐀𝐀 𝐊𝐄 𝐘𝐀𝐀𝐑 🤣🩶🤚🏼"," 𝐊𝐄 𝐆𝐀𝐑𝐈𝐁 𝐁𝐇𝐀𝐆 𝐊𝐀𝐈𝐒𝐄 𝐑𝐀𝐇𝐀 𝐇 𝐑𝐄𝐏𝐋𝐘 𝐊𝐑 𝐂𝐇𝐎𝐓𝐄𝐘 😂👻🩷"," 𝐁𝐇𝐀𝐆𝐍𝐀 𝐍𝐘 𝐇 ̷M̷̷A̷̷D̷̷A̷̷R̷̷C̷̷H̷̷O̷̷D̷ 𝐓𝐄𝐑𝐈 𝐌𝐀 𝐌𝐀𝐑 𝐃𝐔𝐍𝐆𝐀 𝐁𝐇𝐀𝐆𝐀 𝐓𝐎𝐇😤👻🩷"," 𝐊𝐎 𝐏𝐄𝐋𝐓𝐄 𝐇𝐔𝐄 𝐑𝐗𝐒 𝐏𝐀𝐏𝐀 𝐊𝐈 𝐄𝐗𝐈𝐓😎❤️‍🔥"]
NCEMO_EMOJIS = ["👻","🩷","😂","🤣","♥️","💦","😹","🥶","💦","🥀","🎀","😈","👑","😤","🤷🏻‍♂️", "🥶", "👅", "🤣", "🤙🏻", "🤦🏻‍♂️", "😏", "👏🏻"]
REPLY_rxs_TEXTS = ["beta cabi abbu co स्लाइड mt krna 😁😁⛔👋🏼🔥", "ƬƦƖ ƁӇEƝ ⱮƛƦƘE ƦχᏕ ƁӇƛƓ ƓƳƛ 🤦🏾💔", "ƬƳƤE ƘƦ Ɲƛ ƛƁ ƬEƦE ƁƛƛƤ ƘE ƧƛⱮƝE ⁉", "ƤƲJƛ ƘƦ ƬEƦƛ ƁƛƛƤ ƦχᏕ ƘƖ 🙏🏻🔥", "ƘƳƛ ƦχᏕ ƬEƦƛ ƁƛƛƤ ӇƛƖ 👻❓", "ƬEƦƖ Ɱƛƛ ƇƲƊ ƦӇƖ ӇƛƖ ƝƛƇӇƠ 👻🕺", "ƦχᏕ ƇӇƠƊ ƦӇƛ ӇƛƖ 👻🔥", "ƖƵƵƛƬ ƘƦƠ ƬƲⱮӇƛƦE ƁƛƛƤ ƦχᏕ ƘƖ 😑🙌🏾", "etna ताबड़तोड़ coduga ci slide krna bhul jyega 🙄✍️🤍", "भाँगी ke larke tery हिम्मत kese hui rply करने की 💪🏼😂😁😁👍🏼🤸🏼🔥⛔🛺", "𝐂𝐇𝐀𝐋 𝐓𝐄𝐑𝐈 𝐌𝐀 𝐗𝐇𝐎𝐃𝐔 𝐏𝐀𝐓𝐀𝐊 𝐏𝐀𝐓𝐀𝐊 𝐊𝐄🤣👻🩶", "Kaan kholke sunn 👂🏻👂🏻👂🏻👂🏻👂🏻👂🏻 तेरी माँ रंडी है 😂😂😂🔥🔥🔥", "𝐂ʜʟ 𝐇ᴀʀᴍᴢᴀᴅ𝐈 𝐊ᴇ लड़के 💛🤍🩵", "hlw hlw mja aarha cudne me?", "bina ruke thukai hogi teri", "kr na fyt", "Teri ma ko senapati se chudwadengge🪖🖲️🪖🖲️🪖🖲️", "Ary😳 ye😍 kese🤔 Kiya 😱re 🤡mc 😂teri😁 ma😘 rndi🤣 hai🤨 100% 🙊", "काले Doraemon रोता reh", "Try mom ke sath bad manners krdugga😈🙏🏽💯😂🙏🏽💔", "Sukhi roti khane waly teri ma k dehant hogaya😂🙏🏽😂🙏🏽😂🙏🏽😂🙏🏽😂🙏🏽😂🙏🏽", "Tery maa ko qabar naseeb na ho rndyke😑🖕🏽😑🖕🏽😑🖕🏽", "chup rndyke 🍟⛏️🍟⛏️🍟⛏️🍟🔥😁😁", "Are rundyke bngya fyter kal ana 😂😂💔😂💔", "दूर हट मादरचौद के बच्चे 🙏🔥🙏🔥🙏🔥🙏🔥", "पिल्ले Lᴜɴᴅ pe उछल ?🩷", "केला खा मादरचोद 🍌🍌", "कोई बात नहीं trymom mia khalifa है इसलिए तुझे माफ़ कर रहा हूँ 😍😈🔥🤙🏻", "Teri मोम कa रेpe hogya bc 😩🥺🥳😎💔🔥💔😡🤕💪😘🥺🤣"]
KENG_TEMPLATES = [{"text": "NAME ⱮƎ ƬƎƦƖ ⱮƛƘƠ ƇӇƠƊƲƝƓƛ", "emoji": "🥱"}, {"text": "NAME ƇӇƲƤ ƦƝƊƳƘƎ", "emoji": "😂"}, {"text": "NAME ƲƬӇ ƦƝƊƖƘƎ ƁƛƇӇƎ", "emoji": "🍌"}, {"text": "NAME ƛƦE NAME JƛƖƧE ƘƲƬƬƠ ƘƠ ⱮƛƛƦ Ƙ HƲⱮ ƤƛƝƖ Ɣ ƝƛӇƖ ƤƲƇӇƬE ⱮƇ", "emoji": "🩷"}, {"text": "NAME Ƙƛ ƁƛƛƤ ƛƛƳƛ", "emoji": "❔"}, {"text": "NAME ƘƖ Ɱƛ Ƙƛ ƁƠƠƦ", "emoji": "🤪"}, {"text": "ƛƦE NAME ƁӇƛƓ ƘEƧE ƦӇE ӇƠ ƓƛƦEEƁƠ", "emoji": "👻"}, {"text": "NAME ƲƬӇƛƘ ƁƛƖƬӇƛƘ ʟƛƓƛ ⱮƇ", "emoji": "😹"}, {"text": "NAME ƵƠƦ ʟƛƓƛ ƝƇ ƇƔ ʟE ⱮƇ", "emoji": "🤣"}, {"text": "NAME ƇӇƛʟ JӇƲƘ ƦƝƊƘ", "emoji": "😎"}]
SPMNC_LONG = ["NAME ƲƬӇ ƤƛƖƦ ƤƘƊ ӇƲⱮƛƦE\n\n\n\n\n\n" * 40, "ƝƳ ƝƳ ⱮE ƘƲƇӇ ƝƳ JƛƝƬƛ ƁƧ NAME ƘƖ Ɱƛ ƦƝƊƳ EƳ\n\n\n\n\n\n" * 40, "NAME ƲƬӇ ƘE ƁƛƖƬӇ ƦƝƊƘ\n\n\n\n\n\n" * 40, "NAME ƬEƦƖ Ɱƛ ƘƖ ƇӇƲƬ ⱮE ƛƛƓ ʟƛƓƛ ƊƲƝƓƛ ⱮƇ\n\n\n\n\n\n" * 40,"NAME ƬEƦƖ ⱮƠⱮ ƦƝƊƳ\n\n\n\n\n\n" * 40,"NAME ƬEƦƖ Ɱƛ ƘƠ ƇӇƠƊƲƝ\n\n\n\n\n\n" * 40,"NAME JӇƛƬƲ ƧƛʟE ƁƛƛƤ ʟƠƓ ƧE ʟƛƊӇEƓƛ?\n\n\n\n\n\n" * 40]
SPMNC_SMALL = ["NAME ƲƬӇ ƤƛƖƦ ƤƘƊ ӇƲⱮƛƦE","ƝƳ ƝƳ ⱮE ƘƲƇӇ ƝƳ JƛƝƬƛ ƁƧ NAME ƘƖ Ɱƛ ƦƝƊƳ EƳ","NAME ƲƬӇ ƘE ƁƛƖƬӇ ƦƝƊƘ","NAME ƬEƦƖ Ɱƛ ƘƖ ƇӇƲƬ ⱮƐ ƛƛƓ ʟƛƓƛ ƊƲƝƓƛ ⱮƇ","NAME ƬEƦƖ ⱮƠⱮ ƦƝƊƳ","NAME ƬƎƦƖ Ɱƛ ƘƠ ƇӇƠƊƲƝ","NAME JӇƛƬƲ ƧƛʟE ƁƛƛƤ ʟƠƓ ƧE ʟƛƊӇEƓƛ?"]

gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks, spmnc_tasks, pfpnc_tasks = {}, {}, {}, {}, {}, {}, {}
spm_loop_tasks, rspm_tasks, rstyle_tasks, rxsspam_tasks, slidespam_tasks, poll_tasks = {}, {}, {}, {}, {}, {}
rdesc_tasks = {}
rfrwd_tasks = {}
rpin_tasks = {}

swipe_targets, replyrxs_targets, react_targets = set(), set(), {}
apps, bots, bot_usernames = [], [], []

chat_names = {}
pending_links = {}
notified_chats = set()
pagination_data = {}
pending_pfpnc = {}
pfpnc_photo_sets = {}

# Arise panel state
arise_state = {}

# Rights spam tasks (per-chat, per-bot)
rights_spam_tasks = {}

# StartPFP global media - NOW PER BOT
STARTPFP_FILE = "startpfp_media.json"
# Per-bot startpfp meta: {bot_username: {"path":..., "type":..., "file_id":...}}
STARTPFP_PER_BOT = {}
# Cached user media sent by the bot once (per user, per bot) to speed up non-sudo start
STARTPFP_USER_CACHE = {}

# Lock to serialize startpfp file access / writes
STARTPFP_LOCK = asyncio.Lock()

try:
    if os.path.exists(STARTPFP_FILE):
        with open(STARTPFP_FILE, "r") as f:
            _m = json.load(f)
            # Backward compat: if old format (single dict), ignore or migrate
            if isinstance(_m, dict) and "path" in _m:
                # old single format - won't be used anymore (per-bot now)
                pass
            elif isinstance(_m, dict):
                STARTPFP_PER_BOT = _m
                # validate paths exist
                for _u in list(STARTPFP_PER_BOT.keys()):
                    _entry = STARTPFP_PER_BOT[_u]
                    if not _entry.get("path") or not os.path.exists(_entry.get("path", "")):
                        if not _entry.get("file_id"):
                            STARTPFP_PER_BOT.pop(_u, None)
except:
    pass

def _save_startpfp():
    try:
        with open(STARTPFP_FILE, "w") as f:
            json.dump(STARTPFP_PER_BOT, f)
    except:
        pass

# Bot API coordination is local to each bot. The public wall-clock map remains
# for the existing status UI; deadlines used for waiting are monotonic.
bot_floodwait = {}
bot_api_states = {}


@dataclass
class BotApiState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cooldown_until: float = 0.0
    backoff_until: float = 0.0
    transient_failures: int = 0
    transient_generation: int = 0


def _bot_api_state(bot):
    try:
        bot_id = bot.id
    except RuntimeError:
        # PTB's id/username properties are unavailable until initialize() has
        # completed. Keep startup requests on a per-instance state key.
        bot_id = None

    if bot_id is None:
        pending_key = ("pending", id(bot))
        return id(bot), bot_api_states.setdefault(pending_key, BotApiState())

    pending_key = ("pending", id(bot))
    pending_state = bot_api_states.pop(pending_key, None)
    state = bot_api_states.setdefault(bot_id, pending_state or BotApiState())
    if pending_state is not None and pending_state is not state:
        state.cooldown_until = max(state.cooldown_until, pending_state.cooldown_until)
        state.backoff_until = max(state.backoff_until, pending_state.backoff_until)
        state.transient_failures = max(state.transient_failures, pending_state.transient_failures)
        state.transient_generation = max(state.transient_generation, pending_state.transient_generation)

    pending_wall_deadline = bot_floodwait.pop(id(bot), 0.0)
    if pending_wall_deadline:
        bot_floodwait[bot_id] = max(bot_floodwait.get(bot_id, 0.0), pending_wall_deadline)
    return bot_id, state


def _retry_after_seconds(value):
    if hasattr(value, "total_seconds"):
        return max(0.0, float(value.total_seconds()))
    return max(0.0, float(value))


async def wait_for_bot_floodwait(bot, retry_after, action="Telegram API", *, already_recorded=False):
    """Wait for the active server cooldown without releasing it for other tasks."""
    bot_id, state = _bot_api_state(bot)
    if not already_recorded:
        _record_bot_cooldown(bot, retry_after)
    await _wait_for_bot_deadline(state, cooldown_only=True)
    if state.cooldown_until <= time.monotonic():
        bot_floodwait[bot_id] = 0


def _record_bot_cooldown(bot, retry_after):
    """Extend, never shorten, this bot's Telegram-mandated cooldown."""
    bot_id, state = _bot_api_state(bot)
    retry_seconds = _retry_after_seconds(retry_after)
    now = time.monotonic()
    state.cooldown_until = max(state.cooldown_until, now + retry_seconds)
    wall_deadline = time.time() + max(0.0, state.cooldown_until - now)
    bot_floodwait[bot_id] = max(bot_floodwait.get(bot_id, 0.0), wall_deadline)
    return retry_seconds


async def _wait_for_bot_deadline(state, cooldown_only=False):
    """Sleep to a deadline and recheck after waking in case it was extended."""
    while True:
        deadline = state.cooldown_until
        if not cooldown_only:
            deadline = max(deadline, state.backoff_until)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _console_dashboard.render()
            return
        await asyncio.sleep(remaining)


def _is_transient_api_error(error):
    return isinstance(error, (
        telegram.error.NetworkError,
        asyncio.TimeoutError,
        httpx.TimeoutException,
        httpx.TransportError,
    ))


def _record_transient_backoff(bot, action, error):
    bot_id, state = _bot_api_state(bot)
    state.transient_failures += 1
    state.transient_generation += 1
    exponent = min(state.transient_failures - 1, 10)
    delay = min(30.0, 0.5 * (2 ** exponent) + random.uniform(0.0, 0.25))
    state.backoff_until = max(state.backoff_until, time.monotonic() + delay)
    console_print(f"{C_DARK}[NET] bot_id={bot_id} {action} failed ({type(error).__name__}); transient backoff scheduled{C_RESET}")


async def _execute_bot_api(bot, action, invoke, *, serialized=True):
    """Run one Bot API operation through shared per-bot cooldown/backoff state."""
    bot_id, state = _bot_api_state(bot)

    async def run_once():
        await _wait_for_bot_deadline(state)
        failure_generation = state.transient_generation
        try:
            result = await invoke()
        except asyncio.CancelledError:
            raise
        except telegram.error.RetryAfter as error:
            retry_seconds = _record_bot_cooldown(bot, error.retry_after)
            console_print(f"{C_DARK}[FLOODWAIT] bot_id={bot_id} {action} received RetryAfter={retry_seconds:g}s{C_RESET}")
            raise
        except Exception as error:
            if isinstance(error, telegram.error.Forbidden):
                _console_print_repeated(
                    (bot_id, action, type(error).__name__),
                    f"{C_GRAY}[PERMISSION] bot_id={bot_id} {action} denied ({type(error).__name__}){C_RESET}",
                )
            elif isinstance(error, telegram.error.BadRequest):
                _console_print_repeated(
                    (bot_id, action, type(error).__name__),
                    f"{C_GRAY}[INVALID REQUEST] bot_id={bot_id} {action} rejected ({type(error).__name__}){C_RESET}",
                )
            elif _is_transient_api_error(error):
                _record_transient_backoff(bot, action, error)
            else:
                console_print(f"{C_GRAY}[API ERROR] bot_id={bot_id} {action} failed ({type(error).__name__}){C_RESET}")
            raise
        else:
            if state.transient_generation == failure_generation:
                state.transient_failures = 0
                state.backoff_until = 0.0
            if state.cooldown_until <= time.monotonic():
                bot_floodwait[bot_id] = 0
            return result

    if not serialized:
        return await run_once()
    async with state.lock:
        return await run_once()


class CoordinatedBot(telegram.Bot):
    """PTB Bot with per-bot request coordination at the common API boundary."""

    async def _post(self, *args, **kwargs):
        endpoint = kwargs.get("endpoint", args[0] if args else "unknown")
        invoke = lambda: super(CoordinatedBot, self)._post(*args, **kwargs)
        # Long polling must not hold the API lock; it still publishes RetryAfter
        # and transient failure state so ordinary operations observe it.
        return await _execute_bot_api(
            self, endpoint, invoke, serialized=endpoint != "getUpdates"
        )

# RNC cadence remains coordinated per bot on top of the shared API gate.
rnc_title_locks = {}
rnc_next_title_at = {}
rnc_error_backoff = {}
worker_tasks = {}
poll_supervisor_tasks = {}

# Rights monitoring globals
rights_check_tasks = {}
bot_rights_cache = {}

last_rxs_reply = {}
REPLY_rxs_DELAY = 0.05

DELAYS = {"nc": 0.5, "spm": 0.3, "rspm": 0.3, "rxsspam": 0.5, "frwd": 0.5, "desc": 2.0, "pin": 0.5, "rights": 1.5, "poll": 0.75, "rpin": 0.5, "rstyle": 0.3}

BOT_START_TIME = time.time()

logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)

class LRUCache:
    def __init__(self, capacity: int):
        self.cache = OrderedDict()
        self.capacity = capacity
    def contains(self, key) -> bool:
        if key not in self.cache:
            self.cache[key] = True
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
            return False
        return True

processed_cmds = LRUCache(2000)

def is_processed(update: Update) -> bool:
    msg = update.effective_message
    if not msg: return True
    key = (update.effective_chat.id, msg.message_id)
    return processed_cmds.contains(key)

async def fast_reply(update: Update, text: str, **kwargs):
    msg = update.effective_message
    if not msg: return
    try:
        await msg.reply_text(text, **kwargs)
    except telegram.error.BadRequest:
        try:
            await update.effective_chat.send_message(text=text, **kwargs)
        except:
            pass
    except:
        pass

if os.path.exists(SUDO_FILE):
    try:
        with open(SUDO_FILE, "r") as f:
            SUDO_USERS = set(int(x) for x in json.load(f))
    except Exception: SUDO_USERS = {OWNER_ID}
else:
    SUDO_USERS = {OWNER_ID}

def save_sudo():
    with open(SUDO_FILE, "w") as f:
        json.dump(list(SUDO_USERS), f)

def get_auth_id(update: Update) -> int:
    msg = update.effective_message
    if msg:
        if msg.sender_chat: return msg.sender_chat.id
        if msg.from_user: return msg.from_user.id
    if update.effective_user: return update.effective_user.id
    if update.effective_chat: return update.effective_chat.id
    return 0

def only_sudo(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_processed(update): return
        uid = get_auth_id(update)
        if uid not in SUDO_USERS:
            return await fast_reply(update, f"{h()} {font('You are not SUDO.')}")
        return await func(update, context)
    return wrapper

def only_owner(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_processed(update): return
        uid = get_auth_id(update)
        if uid != OWNER_ID:
            return await fast_reply(update, f"{h()} {font('Only OWNER can do this.')}")
        return await func(update, context)
    return wrapper

def extract_command_text(raw_text: Optional[str]) -> str:
    if not raw_text: return ""
    parts = raw_text.split(" ", 1)
    if len(parts) == 1: return ""
    return parts[1].lstrip()


def _cleanup_managed_worker(task_dict, chat_id, bot, coro_func, marker, key, task):
    current = task_dict.get(chat_id)
    if current is not None and current.get(bot.id) is marker:
        current.pop(bot.id, None)
        if not current:
            task_dict.pop(chat_id, None)
    is_current_worker = worker_tasks.get(key) is task
    if is_current_worker:
        worker_tasks.pop(key, None)
    if getattr(coro_func, "__name__", "") == "snc_loop" and key not in worker_tasks:
        rnc_error_backoff.pop((bot.id, chat_id), None)


def _start_managed_worker(task_dict, chat_id, bot, coro_func, *args, bot_idx=None):
    key = (id(task_dict), chat_id, bot.id)
    existing = worker_tasks.get(key)
    if existing is not None and not existing.done():
        return existing

    task_dict.setdefault(chat_id, {})
    marker = object()
    task_dict[chat_id][bot.id] = marker

    async def run_worker():
        try:
            if bot_idx is None:
                await coro_func(bot, chat_id, *args)
            else:
                await coro_func(bot, chat_id, *args, bot_idx)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            console_print(f"{C_GRAY}[WORKER ERROR] bot_id={bot.id} {getattr(coro_func, '__name__', 'worker')} failed ({type(error).__name__}){C_RESET}")
        finally:
            _cleanup_managed_worker(task_dict, chat_id, bot, coro_func, marker, key, asyncio.current_task())

    task = asyncio.create_task(run_worker())
    worker_tasks[key] = task
    task.add_done_callback(
        lambda completed: _cleanup_managed_worker(
            task_dict, chat_id, bot, coro_func, marker, key, completed
        )
    )
    return task


def start_task(task_dict, chat_id, coro_func, *args):
    for idx, bot in enumerate(bots):
        _start_managed_worker(task_dict, chat_id, bot, coro_func, *args, bot_idx=idx)


def stop_task(task_dict, chat_id):
    if task_dict is poll_tasks:
        stop_poll_task(chat_id)
        return
    for key, task in list(worker_tasks.items()):
        dict_id, worker_chat_id, _bot_id = key
        if dict_id == id(task_dict) and worker_chat_id == chat_id and not task.done():
            task.cancel()
            worker_tasks.pop(key, None)
    if chat_id in task_dict:
        task_dict[chat_id].clear()
        task_dict.pop(chat_id, None)


def stop_all_managed_workers(*task_dicts):
    if any(task_dict is poll_tasks for task_dict in task_dicts):
        stop_all_poll_tasks()
    dict_ids = {id(task_dict) for task_dict in task_dicts}
    for key, task in list(worker_tasks.items()):
        dict_id, _, _bot_id = key
        if dict_id in dict_ids and not task.done():
            task.cancel()
            worker_tasks.pop(key, None)
    for task_dict in task_dicts:
        task_dict.clear()


def start_poll_task(chat_id, name):
    existing = poll_supervisor_tasks.get(chat_id)
    if existing is not None and not existing.done():
        return existing
    marker = {"running": True}
    poll_tasks[chat_id] = marker

    async def run_poll():
        try:
            await seq_poll_loop(chat_id, name)
        finally:
            _cleanup_poll_task(chat_id, marker, asyncio.current_task())

    task = asyncio.create_task(run_poll())
    poll_supervisor_tasks[chat_id] = task
    task.add_done_callback(lambda completed: _cleanup_poll_task(chat_id, marker, completed))
    return task


def _cleanup_poll_task(chat_id, marker, task):
    if poll_tasks.get(chat_id) is marker:
        poll_tasks.pop(chat_id, None)
    if poll_supervisor_tasks.get(chat_id) is task:
        poll_supervisor_tasks.pop(chat_id, None)


def stop_poll_task(chat_id):
    poll_tasks.pop(chat_id, None)
    task = poll_supervisor_tasks.pop(chat_id, None)
    if task is not None and not task.done():
        task.cancel()


def stop_all_poll_tasks():
    for chat_id, task in list(poll_supervisor_tasks.items()):
        if not task.done():
            task.cancel()
        poll_supervisor_tasks.pop(chat_id, None)
    poll_tasks.clear()

async def safe_set_title(bot, chat_id, title):
    try:
        await bot.set_chat_title(chat_id, title)
        # Only clear an expired cooldown; another task's successful call must
        # not release a server-directed wait early.
        if bot_floodwait.get(bot.id, 0) <= time.time():
            bot_floodwait[bot.id] = 0
        return True
    except asyncio.CancelledError:
        raise
    except telegram.error.RetryAfter as e:
        # INDIVIDUAL floodwait: only this bot waits
        await wait_for_bot_floodwait(bot, e.retry_after, "setChatTitle", already_recorded=True)
        return False
    except telegram.error.BadRequest as e:
        err = str(e).lower()
        if "chat_title_not_modified" in err or "not modified" in err:
            return True
        if "admin" in err or "right" in err or "permission" in err:
            return False
        return False
    except telegram.error.NetworkError:
        return False
    except Exception:
        return False

async def safe_set_description(bot, chat_id, description):
    try:
        await bot.set_chat_description(chat_id, description)
        return True
    except telegram.error.RetryAfter as e:
        # INDIVIDUAL floodwait: only this bot waits
        await wait_for_bot_floodwait(bot, e.retry_after, "setChatDescription", already_recorded=True)
        return False
    except asyncio.CancelledError:
        raise
    except telegram.error.BadRequest:
        return False
    except telegram.error.NetworkError:
        return False
    except Exception:
        return False

# ============================================================
# ACTIVE LOOP CHECK HELPER
# ============================================================
def _chat_has_active_loop(chat_id) -> bool:
    """Return True if ANY loop/spam is currently active in this specific chat."""
    if rpin_tasks.get(chat_id):
        return True
    all_task_dicts = [
        gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks, spmnc_tasks,
        pfpnc_tasks, spm_loop_tasks, rspm_tasks, rstyle_tasks, rxsspam_tasks,
        slidespam_tasks, poll_tasks, rdesc_tasks, rfrwd_tasks,
    ]
    for td in all_task_dicts:
        entry = td.get(chat_id)
        if not entry:
            continue
        if isinstance(entry, dict):
            if any(entry.values()):
                return True
        else:
            if entry:
                return True
    return False

# ============================================================
# NC LOOPS
# ============================================================
async def bot_loop(bot, chat_id, base, task_dict, mode, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.15)
    i = bot_idx
    while task_dict.get(chat_id, {}).get(bot.id):
        try:
            if mode == "raid":
                text = f"{base} {RAID_TEXTS[i % len(RAID_TEXTS)]}"
            else:
                text = f"{base} {NCEMO_EMOJIS[i % len(NCEMO_EMOJIS)]}"
            await safe_set_title(bot, chat_id, text[:128])
            i += 1
            await asyncio.sleep(DELAYS["nc"])
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

RNC_SYMBOLS = {
    "rnc": "𒐫",
    "rnc1": "﷽",
    "rnc2": "꧅",
    "rnc3": "ဪ",
    "rnc4": "ௌ",
    "rnc5": "𒈙",
    "rnc6": "⸻",
}
RNC_EMOJIS = tuple("🫪😹👑🐦‍🔥🪸🫯🧊🩷❤💜🧞🪼‍🍭🍧⚜️🌂️🩵🖤🩶🤍🤎🈵🉐🈹㊗💀🧞‍♂🦖🪽🐦‍🐚🪷🍓🍥️️🎚🏚🦅🦆🐔🐧🐦🐤👾")


def precompute_rnc_titles(base_text, symbol):
    """Build every RNC title once before its worker loop starts."""
    titles = []
    for emoji in RNC_EMOJIS:
        pattern = f"{symbol}{symbol}{symbol}{emoji}"
        pattern_len = len(pattern)
        if pattern_len >= 128:
            titles.append(pattern[:128])
            continue

        trimmed_base = base_text[:128 - pattern_len] if len(base_text) + pattern_len > 128 else base_text
        title = trimmed_base
        while len(title) + pattern_len <= 128:
            title += pattern
        titles.append(title[:128])
    return tuple(titles)


async def safe_rnc_set_title(bot, chat_id, title):
    """Keep successful RNC cadence unchanged; serialize bursts and back off errors."""
    lock = rnc_title_locks.setdefault(bot.id, asyncio.Lock())
    async with lock:
        next_allowed = rnc_next_title_at.get(bot.id, 0)
        wait = next_allowed - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

        # Match the existing per-loop delay, but only serialize concurrent RNC
        # workers for this bot. A single worker retains its existing cadence.
        rnc_next_title_at[bot.id] = time.monotonic() + max(0.0, DELAYS["nc"])
        succeeded = await safe_set_title(bot, chat_id, title)
        key = (bot.id, chat_id)
        if succeeded:
            rnc_error_backoff.pop(key, None)
        else:
            previous = rnc_error_backoff.get(key, 0.0)
            delay = min(60.0, max(1.0, previous * 2.0) + random.uniform(0.1, 0.5))
            rnc_error_backoff[key] = delay
            rnc_next_title_at[bot.id] = max(
                rnc_next_title_at[bot.id], time.monotonic() + delay
            )
        return succeeded


async def snc_loop(bot, chat_id, precomputed_titles, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.15)
    i = bot_idx
    while snc_tasks.get(chat_id, {}).get(bot.id):
        try:
            title = precomputed_titles[i % len(precomputed_titles)]
            await safe_rnc_set_title(bot, chat_id, title)
            i += 1
            await asyncio.sleep(DELAYS["nc"])
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.3)

async def kengnc_loop(bot, chat_id, name, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.15)
    while keng_tasks.get(chat_id, {}).get(bot.id):
        try:
            template = random.choice(KENG_TEMPLATES)
            base_text = template["text"].replace("NAME", name)
            emoji_block = template["emoji"]
            pad_len = max(1, (128 - len(base_text) - 2) // len(emoji_block))
            dynamic_gap = " " * random.randint(1, 3)
            title = (base_text + dynamic_gap + (emoji_block * pad_len))[:128]
            await safe_set_title(bot, chat_id, title)
            await asyncio.sleep(DELAYS["nc"])
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def rxsnc_loop(bot, chat_id, name, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.15)
    templates = ["ƘƳƲ ƦЄ NAME ƘƲƬƬЄ [ ƦχᏕ ] ƁƛƛƤ ƧЄ ƁƛƊƬƛⱮЄЄƵƖ? ", "NAME ƲƬӇ ƬⱮƘƇ ", "NAME ƝƇ ƝƇ ƘƦЄƓƛ ƦƝƊƖƘ", "NAME ƬƠƦ ⱮƛƖ ƘЄ ƇӇƠƊƠ", "NAME ƝƊ ƲƝƘЄ ӇƠⱮƖЄƧ ƘƖ Ɱƛ ƦƝƊƖ", "NAME ƲƬӇ ƦƝƊƖƘ ƠҲƳƓЄƝ ԼЄ", "ƇӇƛԼ ƇӇƛԼ NAME ƵƠƦ ԼƛƓƛ ⱮƇ", "NAME ⱮƇ"]
    while rxsnc_tasks.get(chat_id, {}).get(bot.id):
        try:
            base = random.choice(templates).replace("NAME", name)
            c1 = "✘" if random.random() > 0.5 else "─"
            c2 = "✘" if random.random() > 0.5 else "─"
            pattern = f"─────── {c1} ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬ ────────{c2}─────❗"
            title = (base + pattern)[:128]
            await safe_set_title(bot, chat_id, title)
            await asyncio.sleep(DELAYS["nc"])
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def pfpnc_loop(bot, chat_id, name, photo_paths, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.2)
    i = bot_idx
    if isinstance(photo_paths, str):
        photo_paths = [photo_paths]
    while pfpnc_tasks.get(chat_id, {}).get(bot.id):
        try:
            rt = RAID_TEXTS[i % len(RAID_TEXTS)]
            title = f"{name} {rt}"[:128]
            await safe_set_title(bot, chat_id, title)
            current_photo = photo_paths[i % len(photo_paths)]
            try:
                with open(current_photo, "rb") as f:
                    await bot.set_chat_photo(chat_id, photo=f)
            except telegram.error.RetryAfter as e:
                # INDIVIDUAL floodwait
                await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
            except:
                pass
            i += 1
            await asyncio.sleep(DELAYS["nc"] + 0.5)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def spmnc_loop(bot, chat_id, name, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.15)
    last_msg_time = 0
    while spmnc_tasks.get(chat_id, {}).get(bot.id):
        try:
            long_raw = random.choice(SPMNC_LONG).replace("NAME", name)
            small_raw = random.choice(SPMNC_SMALL).replace("NAME", name)
            random_emoji = random.choice(NCEMO_EMOJIS)
            title = f"{random_emoji} {small_raw} {random_emoji}"[:128]
            await safe_set_title(bot, chat_id, title)
            await asyncio.sleep(DELAYS["nc"])

            if time.time() - last_msg_time > 4.0:
                try:
                    await bot.send_message(chat_id, long_raw)
                    last_msg_time = time.time()
                except telegram.error.RetryAfter as e:
                    await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
                except:
                    pass
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def rdesc_loop(bot, chat_id, base_text, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    while rdesc_tasks.get(chat_id, {}).get(bot.id):
        try:
            heart = random.choice(HEARTS)
            desc = f"{base_text} <{heart}>"
            await safe_set_description(bot, chat_id, desc[:255])
            await asyncio.sleep(DELAYS["desc"])
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.5)

# ============================================================
# SPAM LOOPS
# ============================================================
async def spm_loop_sender(bot, chat_id, text, task_dict, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    while task_dict.get(chat_id, {}).get(bot.id):
        try:
            sent = await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
            if rpin_tasks.get(chat_id):
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                except:
                    pass
            await asyncio.sleep(DELAYS["spm"])
        except telegram.error.RetryAfter as e:
            # INDIVIDUAL floodwait - update dict AND sleep
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def slidespam_loop(bot, chat_id, msg_id, text, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    while slidespam_tasks.get(chat_id, {}).get(bot.id):
        try:
            sent = await bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=msg_id)
            if rpin_tasks.get(chat_id):
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                except:
                    pass
            await asyncio.sleep(DELAYS["spm"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except telegram.error.BadRequest:
            try:
                sent = await bot.send_message(chat_id=chat_id, text=text)
                if rpin_tasks.get(chat_id):
                    try:
                        await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                    except:
                        pass
                await asyncio.sleep(DELAYS["spm"])
            except:
                await asyncio.sleep(0.3)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def rfrwd_loop(bot, chat_id, from_chat_id, msg_id, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    while rfrwd_tasks.get(chat_id, {}).get(bot.id):
        try:
            sent = await bot.forward_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=msg_id)
            if rpin_tasks.get(chat_id):
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                except:
                    pass
            await asyncio.sleep(DELAYS["frwd"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def rspm_loop(bot, chat_id, name, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    while rspm_tasks.get(chat_id, {}).get(bot.id):
        try:
            chunk = f"{name} {random.choice(RAID_TEXTS)} {random.choice(NCEMO_EMOJIS)}\n\n\n\n\n\n\n\n\n\n"
            msg_text = ""
            while len(msg_text) + len(chunk) < 4000: msg_text += chunk
            sent = await bot.send_message(chat_id=chat_id, text=msg_text)
            if rpin_tasks.get(chat_id):
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                except:
                    pass
            await asyncio.sleep(DELAYS["rspm"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def rstyle_loop(bot, chat_id, name, style, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    while rstyle_tasks.get(chat_id, {}).get(bot.id):
        try:
            rt = random.choice(RAID_TEXTS)
            em = random.choice(NCEMO_EMOJIS)

            if style == "rstyle1": chunk = f"🔥 {name} 🔥\n⬇️\n{rt}\n⬇️\n{em}\n\n\n\n\n"
            elif style == "rstyle2": chunk = f"{name} 𒐫𒐫𒐫 {rt} 𒐫𒐫𒐫 {em}\n\n\n"
            elif style == "rstyle3": chunk = f"┏ ━ {name} ━ ┓\n┣ ━ {rt} ━ ┫\n┗ ━ {em} ━ ┛\n\n\n"
            elif style == "rstyle4": chunk = f"────── ✘ {name} ✘ ──────\n{rt} {em}\n\n\n"
            elif style == "rstyle5": chunk = f"{em} ⸻ {name} ⸻ {em}\n{rt}\n\n\n\n\n"
            elif style == "rstyle6": chunk = f"﷽ {name} ﷽\n{rt} {em}\n\n\n\n\n"
            else: chunk = f"{name} {rt} {em}\n\n\n\n\n\n\n\n\n\n"

            msg_text = ""
            while len(msg_text) + len(chunk) < 4000: msg_text += chunk

            sent = await bot.send_message(chat_id=chat_id, text=msg_text)
            if rpin_tasks.get(chat_id):
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                except:
                    pass
            await asyncio.sleep(DELAYS["rstyle"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def rxsspam_loop(bot, chat_id, text, bot_idx=0):
    await asyncio.sleep(bot_idx * 0.1)
    # rxsSpam: template at start + blank space + template at end = 4000 chars total
    template = f"ᎪᎪʟᎪ ᖇᎬ ᎪᎪʟᎪ -#ƦχᏕ ᎪᎪʟᎪ [🩷] {{{text}}} -ᏀᏟ Ꮮᴇғᴛ Ꮮᴇ Ꭱɴᴅɪʙᴀʟᴀꪻ♡"
    content_len = len(template) * 2
    if content_len < 4000:
        msg = template + ("\n" * (4000 - content_len)) + template
    else:
        msg = template[:4000]
    while rxsspam_tasks.get(chat_id, {}).get(bot.id):
        try:
            sent = await bot.send_message(chat_id=chat_id, text=msg)
            if rpin_tasks.get(chat_id):
                try:
                    await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=True)
                except:
                    pass
            await asyncio.sleep(DELAYS["rxsspam"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError: break
        except:
            await asyncio.sleep(0.3)

async def _poll_worker(bot, chat_id, question, options, bot_idx=0):
    """Send one poll from one bot in a loop until stopped."""
    await asyncio.sleep(bot_idx * 0.08)
    while poll_tasks.get(chat_id):
        try:
            try:
                poll_msg = await bot.send_poll(chat_id=chat_id, question=question, options=options)
            except telegram.error.BadRequest:
                safe_q = question[:255] if len(question) > 255 else question
                safe_opts = [o[:100] for o in options[:10]]
                poll_msg = await bot.send_poll(chat_id=chat_id, question=safe_q, options=safe_opts)

            try:
                await bot.pin_chat_message(chat_id=chat_id, message_id=poll_msg.message_id, disable_notification=True)
            except:
                pass

            await asyncio.sleep(DELAYS["poll"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError:
            break
        except:
            await asyncio.sleep(0.5)

async def seq_poll_loop(chat_id, name):
    # ONLY ONE LINE at the top of the poll (no repetition, no multi-line mess)
    question = f"{name} ƘƖ MƠM ƦƛƝƊƖ ӇƛƖ ƦƛƝƊƖ 🤣🩷"
    if len(question) > 255:
        question = question[:255]

    raw_options = ["{name} ƬЄƦƖ Mƛ ƦƝƊƖ ӇƛƖ ƘƳƛ ƁЄ?",
        "{name} ƬƠ ƦƛƝƊƖƘƛ ƁƛƇӇƛ Ӈ",
        "{name} ƬƲ ƬӇƠƊƛ Ƨƛ ƦƛƝƊƖƘƛ ƁƛƇӇƛ Ӈ ƘƳ",
        "{name} ƳЄ ƜӇƖ ƓƲԼƛM Ӈ ʆƠ ЄƘ ƦƊƤ Ƙ ԼƖƳЄ ƛƤƝƳ MƲMMƳ ƘƖ ƤƛƝƬƳ Ƙƛ ƧƝƛƤƧӇƠƬ ƁӇʆƊЄ",
        "ƘƳƲ {name} ƁӇƛƝƓƖ ƘƖ ƘƲƇӇ ƛƲƘƛƬ ƝƛƖ ӇƛƖ",
        "{name} ԼƲƝƊ ƇӇƠƠƧƬƛ ӇƛƖ ӇƲMƛƦƛ",
        "{name} ԼƠƊЄ ƤЄ ƲƇӇƛԼƝƛ ƇӇƠƦƊЄ MƇ",
        "ƛƁЄ {name} ƊЄӇƛƬƖ ƇӇƲƊƛƖƘӇƛƝЄ",
        "{name} ƇӇƲƤ ƦƝƊƖƘЄ",
        "{name} ƘƦ ƛƁ ƬЄƦƄ ƦχᏕ ƁƛƛƤ ƘЄ ƧƛMƝЄ ƧƤƛMMƖƝƓ"
    ]

    poll_emojis = ["🩷","🩵", "💙", "💜", "🖤", "🩶", "🤎", "🤍","👻","🩷","😂","🤣","♥️","💦","😹","🥶","💦","🥀","🎀","😈","👑",]

    options = []
    for i, opt in enumerate(raw_options):
        formatted_opt = opt.replace("{name}", name) + " "
        chosen_emoji = poll_emojis[i % len(poll_emojis)]
        while len(formatted_opt) < 95:
            formatted_opt += chosen_emoji
        options.append(formatted_opt)

    if not bots:
        return

    # Launch ONE worker per bot so all bots send polls in parallel
    workers = []
    for idx, bot in enumerate(bots):
        workers.append(asyncio.create_task(_poll_worker(bot, chat_id, question, options, idx)))

    try:
        while poll_tasks.get(chat_id):
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        for w in workers:
            if not w.done():
                w.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)


# ============================================================
# RIGHTS SPAM LOOP (spams group + pins + DMs owner)
# ============================================================
async def rights_spam_loop(bot, chat_id, group_name, owner_id, link, rights_text):
    """Spam the group with rights alert, pin each message, and DM owner once."""
    # Build the alert text with the best available link
    spam_text = (
        f"{h()} {font('BOT RIGHTS ALERT')} {h()}\n\n"
        f"{font('Bot:')} @{bot.username}\n"
        f"{font('Group:')} {group_name}\n"
        f"{font('Group ID:')} `{chat_id}`\n"
        f"{font('Group Owner ID:')} `{owner_id}`\n"
        f"{font('Group Link:')} {link}\n\n"
        f"{font('Rights Lost:')} {rights_text}\n\n"
        f"{h()} {font('Please check immediately!')} {h()}"
    )

    while rights_spam_tasks.get(chat_id, {}).get(bot.id):
        try:
            # Only spam if the chat still has an active loop
            if not _chat_has_active_loop(chat_id):
                rights_spam_tasks.get(chat_id, {}).clear()
                rights_spam_tasks.pop(chat_id, None)
                return

            sent = await bot.send_message(chat_id=chat_id, text=spam_text, parse_mode="Markdown", disable_web_page_preview=True)
            try:
                await bot.pin_chat_message(chat_id=chat_id, message_id=sent.message_id, disable_notification=False)
            except:
                pass
            await asyncio.sleep(DELAYS["rights"])
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(bot, e.retry_after, "API request", already_recorded=True)
        except asyncio.CancelledError:
            break
        except:
            await asyncio.sleep(1.0)


async def _fetch_best_group_link(chat_id, exclude_bot_id=None):
    """Try all bots in the fleet (except the excluded one) to fetch the group link."""
    for b in bots:
        if b.id == exclude_bot_id:
            continue
        try:
            link = await asyncio.wait_for(b.export_chat_invite_link(chat_id), timeout=5.0)
            if link:
                return link
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(b, e.retry_after, "exportChatInviteLink", already_recorded=True)
            return "𝘾𝙤𝙪𝙡𝙙𝙣’𝙩 𝙁𝙚𝙩𝙘𝙝 𝙇𝙞𝙣𝙠 (𝙣𝙤 𝙧𝙞𝙜𝙝𝙩𝙨)"
        except asyncio.TimeoutError as error:
            _record_transient_backoff(b, "exportChatInviteLink", error)
            continue
        except (telegram.error.BadRequest, telegram.error.Forbidden):
            continue
        except Exception:
            continue
    # fallback to username link if public
    try:
        chat = await bots[0].get_chat(chat_id)
        if chat.username:
            return f"https://t.me/{chat.username}"
    except telegram.error.RetryAfter as e:
        await wait_for_bot_floodwait(bots[0], e.retry_after, "getChat", already_recorded=True)
    except:
        pass
    return "𝘾𝙤𝙪𝙡𝙙𝙣’𝙩 𝙁𝙚𝙩𝙘𝙝 𝙇𝙞𝙣𝙠 (𝙣𝙤 𝙧𝙞𝙜𝙝𝙩𝙨)"


def start_rights_spam(chat_id, group_name, owner_id, link, rights_text):
    # Only spam if this chat actually has an active loop running
    if not _chat_has_active_loop(chat_id):
        return
    for bot in bots:
        _start_managed_worker(
            rights_spam_tasks, chat_id, bot, rights_spam_loop,
            group_name, owner_id, link, rights_text,
        )


def stop_rights_spam(chat_id):
    if chat_id in rights_spam_tasks:
        stop_task(rights_spam_tasks, chat_id)
        rights_spam_tasks.pop(chat_id, None)


# ============================================================
# SPEED: Prebuilt markup singletons (avoid rebuilding dict/list every call)
# ============================================================
_MAIN_MARKUP_CACHE = {}
_NC_MARKUP_CACHE = {}
_RNC_MARKUP_CACHE = {}
_SPM_MARKUP_CACHE = {}
_RSLAY_MARKUP_CACHE = {}
_STOP_MARKUP_CACHE = {}
_SUDO_MARKUP_CACHE = {}

def _build_start_markup(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(font("-#ƠƤЄƝ MЄƝƲ"), callback_data=f"arise|main|{chat_id}")]
    ])

def _build_help_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(font("-#ƝƇ/ƓƦƠƲƤ"), callback_data="help_ncs"), InlineKeyboardButton(font("-#ᏕⱠƛƳ/ᏕƤM"), callback_data="help_spam")],
        [InlineKeyboardButton(font("-#ƦЄƤԼƳ/ƦЄƇƬ"), callback_data="help_reply"), InlineKeyboardButton(font("-#ᏕƲƊƠ/ƲƤƬƖMЄ"), callback_data="help_misc")],
        [InlineKeyboardButton(font("-#ƇⱠƠᏕЄ"), callback_data="help_close")]
    ])

HELP_MAIN_MARKUP = _build_help_markup()

# ============================================================
# Send menu WITH media attached (pic/video/gif + caption + buttons)
# PER-BOT: uses the bot that is sending the menu
# ============================================================
def _get_bot_startpfp(bot):
    """Return this bot's startpfp entry (or None)."""
    if not bot or not bot.username:
        return None
    return STARTPFP_PER_BOT.get(bot.username)

async def send_menu_with_media(bot, chat_id, caption: str, markup: InlineKeyboardMarkup):
    """Send this bot's startpfp media (if set) with the caption AND inline buttons attached."""
    async with STARTPFP_LOCK:
        entry = _get_bot_startpfp(bot)
        if not entry:
            return await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup)

        path = entry.get("path")
        mtype = entry.get("type")
        file_id = entry.get("file_id")

    # FAST PATH: cached file_id → single API call, no upload
    if file_id:
        try:
            if mtype == "photo":
                return await asyncio.wait_for(
                    bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, reply_markup=markup),
                    timeout=8.0
                )
            elif mtype == "video":
                return await asyncio.wait_for(
                    bot.send_video(chat_id=chat_id, video=file_id, caption=caption, reply_markup=markup),
                    timeout=8.0
                )
            elif mtype == "animation":
                return await asyncio.wait_for(
                    bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption, reply_markup=markup),
                    timeout=8.0
                )
        except asyncio.TimeoutError as error:
            _record_transient_backoff(bot, "sendMenuMedia", error)
        except (telegram.error.Forbidden, telegram.error.BadRequest, telegram.error.TimedOut, telegram.error.NetworkError):
            pass
        except Exception as e:
            console_print(f"{C_GRAY}[MENU FILEID ERR] {e}{C_RESET}")

    # SLOW PATH: upload once, cache the file_id
    if path and os.path.exists(path):
        try:
            with open(path, "rb") as f:
                sent = None
                if mtype == "photo":
                    sent = await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, reply_markup=markup)
                elif mtype == "video":
                    sent = await bot.send_video(chat_id=chat_id, video=f, caption=caption, reply_markup=markup)
                elif mtype == "animation":
                    sent = await bot.send_animation(chat_id=chat_id, animation=f, caption=caption, reply_markup=markup)
                if sent:
                    try:
                        async with STARTPFP_LOCK:
                            if mtype == "photo" and sent.photo:
                                entry["file_id"] = sent.photo[-1].file_id
                            elif mtype == "video" and sent.video:
                                entry["file_id"] = sent.video.file_id
                            elif mtype == "animation" and sent.animation:
                                entry["file_id"] = sent.animation.file_id
                            _save_startpfp()
                    except:
                        pass
                    return sent
        except (telegram.error.Forbidden, telegram.error.BadRequest, telegram.error.TimedOut, telegram.error.NetworkError):
            pass
        except Exception as e:
            console_print(f"{C_GRAY}[MENU MEDIA ERR] {e}{C_RESET}")
    return await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup)


# ============================================================
# ARISE PANEL
# ============================================================

def _arise_main_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#ƘЄƝƓ"), url=f"tg://user?id={OWNER_ID}")],
        [InlineKeyboardButton(font("-#ƝƇ ᏕⱠƛƳ"), callback_data=f"arise|nc|{chat_id}")],
        [InlineKeyboardButton(font("-#ᏕⱠƛƳ ᏕƤM"), callback_data=f"arise|spm|{chat_id}")],
        [InlineKeyboardButton(font("-#ƦᏕⱠƛƳ"), callback_data=f"arise|rslay|{chat_id}")],
        [InlineKeyboardButton(font("-#ᏕƬƠƤ ᏕⱠƛƳ"), callback_data=f"arise|stop|{chat_id}")],
        [InlineKeyboardButton(font("-#ᏕƲƊƠ/ƲƤƬƖMЄ"), callback_data=f"arise|sudo|{chat_id}")],
        [InlineKeyboardButton(font("-#ƇⱠƠᏕЄ"), callback_data=f"arise|close|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_nc_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#Gcnc"), callback_data=f"arise|nc_gcnc|{chat_id}"),
         InlineKeyboardButton(font("-#Ncemo"), callback_data=f"arise|nc_ncemo|{chat_id}")],
        [InlineKeyboardButton(font("-#Rnc"), callback_data=f"arise|nc_rnc_menu|{chat_id}"),
         InlineKeyboardButton(font("-#KengNc"), callback_data=f"arise|nc_kengnc|{chat_id}")],
        [InlineKeyboardButton(font("-#RxsNc"), callback_data=f"arise|nc_rxsnc|{chat_id}"),
         InlineKeyboardButton(font("-#SpmNc"), callback_data=f"arise|nc_spmnc|{chat_id}")],
        [InlineKeyboardButton(font("-#PfpNc"), callback_data=f"arise|nc_pfpnc|{chat_id}"),
         InlineKeyboardButton(font("-#Rdesc"), callback_data=f"arise|nc_rdesc|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|main|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_rnc_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#Rnc"), callback_data=f"arise|nc_rnc|{chat_id}"),
         InlineKeyboardButton(font("-#Rnc 1"), callback_data=f"arise|nc_rnc1|{chat_id}"),
         InlineKeyboardButton(font("-#Rnc 2"), callback_data=f"arise|nc_rnc2|{chat_id}")],
        [InlineKeyboardButton(font("-#Rnc 3"), callback_data=f"arise|nc_rnc3|{chat_id}"),
         InlineKeyboardButton(font("-#Rnc 4"), callback_data=f"arise|nc_rnc4|{chat_id}"),
         InlineKeyboardButton(font("-#Rnc 5"), callback_data=f"arise|nc_rnc5|{chat_id}")],
        [InlineKeyboardButton(font("-#Rnc 6"), callback_data=f"arise|nc_rnc6|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|nc|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_rstyle_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#Rstyle"), callback_data=f"arise|spm_rstyle|{chat_id}"),
         InlineKeyboardButton(font("-#Rstyle 1"), callback_data=f"arise|spm_rstyle1|{chat_id}"),
         InlineKeyboardButton(font("-#Rstyle 2"), callback_data=f"arise|spm_rstyle2|{chat_id}")],
        [InlineKeyboardButton(font("-#Rstyle 3"), callback_data=f"arise|spm_rstyle3|{chat_id}"),
         InlineKeyboardButton(font("-#Rstyle 4"), callback_data=f"arise|spm_rstyle4|{chat_id}"),
         InlineKeyboardButton(font("-#Rstyle 5"), callback_data=f"arise|spm_rstyle5|{chat_id}")],
        [InlineKeyboardButton(font("-#Rstyle 6"), callback_data=f"arise|spm_rstyle6|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|spm|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_spm_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#Spm"), callback_data=f"arise|spm_spm|{chat_id}"),
         InlineKeyboardButton(font("-#Rstyle"), callback_data=f"arise|spm_rstyle_menu|{chat_id}")],
        [InlineKeyboardButton(font("-#Rspm"), callback_data=f"arise|spm_rspm|{chat_id}"),
         InlineKeyboardButton(font("-#RxsSpam"), callback_data=f"arise|spm_rxsspam|{chat_id}")],
        [InlineKeyboardButton(font("-#SlideSpam"), callback_data=f"arise|spm_slidespam|{chat_id}"),
         InlineKeyboardButton(font("-#Poll"), callback_data=f"arise|spm_poll|{chat_id}")],
        [InlineKeyboardButton(font("-#Rfrwd"), callback_data=f"arise|spm_rfrwd|{chat_id}"),
         InlineKeyboardButton(font("-#Rpin"), callback_data=f"arise|spm_pin|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|main|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_rslay_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#ReplyRxs"), callback_data=f"arise|r_replyrxs|{chat_id}"),
         InlineKeyboardButton(font("-#Swipe"), callback_data=f"arise|r_swipe|{chat_id}")],
        [InlineKeyboardButton(font("-#React"), callback_data=f"arise|r_react|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|main|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_stop_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#GcNc"), callback_data=f"arise|st_gcnc|{chat_id}"),
         InlineKeyboardButton(font("-#NcEmo"), callback_data=f"arise|st_ncemo|{chat_id}")],
        [InlineKeyboardButton(font("-#rnc"), callback_data=f"arise|st_rnc|{chat_id}"),
         InlineKeyboardButton(font("-#KengNc"), callback_data=f"arise|st_kengnc|{chat_id}")],
        [InlineKeyboardButton(font("-#RxsNc"), callback_data=f"arise|st_rxsnc|{chat_id}"),
         InlineKeyboardButton(font("-#SpmNc"), callback_data=f"arise|st_spmnc|{chat_id}")],
        [InlineKeyboardButton(font("-#PfpNc"), callback_data=f"arise|st_pfpnc|{chat_id}"),
         InlineKeyboardButton(font("-#Rdesc"), callback_data=f"arise|st_rdesc|{chat_id}")],
        [InlineKeyboardButton(font("-#Spm"), callback_data=f"arise|st_spm|{chat_id}"),
         InlineKeyboardButton(font("-#Rstyle"), callback_data=f"arise|st_rstyle|{chat_id}")],
        [InlineKeyboardButton(font("-#Rspm"), callback_data=f"arise|st_rspm|{chat_id}"),
         InlineKeyboardButton(font("-#Rxsspam"), callback_data=f"arise|st_rxsspam|{chat_id}")],
        [InlineKeyboardButton(font("-#SlideSpam"), callback_data=f"arise|st_slidespam|{chat_id}"),
         InlineKeyboardButton(font("-#Poll"), callback_data=f"arise|st_poll|{chat_id}")],
        [InlineKeyboardButton(font("-#Rfrwd"), callback_data=f"arise|st_rfrwd|{chat_id}"),
         InlineKeyboardButton(font("-#Rpin"), callback_data=f"arise|st_rpin|{chat_id}")],
        [InlineKeyboardButton(font("-#ReplyRxs"), callback_data=f"arise|st_replyrxs|{chat_id}"),
         InlineKeyboardButton(font("-#Swipe"), callback_data=f"arise|st_swipe|{chat_id}")],
        [InlineKeyboardButton(font("-#React"), callback_data=f"arise|st_react|{chat_id}")],
        [InlineKeyboardButton(font("-#RightsSpm"), callback_data=f"arise|st_rights|{chat_id}")],
        [InlineKeyboardButton(font("-#Stopall"), callback_data=f"arise|st_all|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|main|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def _arise_sudo_markup(chat_id):
    rows = [
        [InlineKeyboardButton(font("-#Status"), callback_data=f"arise|su_status|{chat_id}"),
         InlineKeyboardButton(font("-#Uptime"), callback_data=f"arise|su_uptime|{chat_id}")],
        [InlineKeyboardButton(font("-#ActiveBots"), callback_data=f"arise|su_activebots|{chat_id}"),
         InlineKeyboardButton(font("-#Missing"), callback_data=f"arise|su_missing|{chat_id}")],
        [InlineKeyboardButton(font("-#GetAllActiveLinks"), callback_data=f"arise|su_links|{chat_id}"),
         InlineKeyboardButton(font("-#My Id"), callback_data=f"arise|su_myid|{chat_id}")],
        [InlineKeyboardButton(font("-#Sudo Users"), callback_data=f"arise|su_listsudo|{chat_id}")],
        [InlineKeyboardButton(font("-#Stopall"), callback_data=f"arise|su_stopall|{chat_id}")],
        [InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data=f"arise|main|{chat_id}")],
    ]
    return InlineKeyboardMarkup(rows)


@only_sudo
async def arise_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    caption = f"{h()} {font('Rxs v5!')}\n{h()} {font('Choose your panel below:')}"
    try:
        msg = await send_menu_with_media(context.bot, chat_id, caption, _arise_main_markup(chat_id))
        arise_state[msg.message_id] = {"chat_id": chat_id, "stage": "main"}
    except Exception as e:
        console_print(f"{C_GRAY}[ARISE ERROR] {e}{C_RESET}")


async def arise_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return

    if query.from_user.id not in SUDO_USERS:
        try: await query.answer(f"{h()} {font('You are not SUDO!')}", show_alert=True)
        except: pass
        return

    asyncio.create_task(_safe_answer(query))

    parts = query.data.split("|")
    if len(parts) < 3 or parts[0] != "arise":
        return
    action = parts[1]
    chat_id = int(parts[2])
    msg_id = query.message.message_id

    if action == "main":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "main"}
        await _fast_edit(query, f"{h()} {font('Rxs v5!')}\n{h()} {font('Choose your panel below:')}", _arise_main_markup(chat_id))
        return

    if action == "close":
        arise_state.pop(msg_id, None)
        try: await query.message.delete()
        except: pass
        return

    if action == "nc":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "nc_menu"}
        await _fast_edit(query, f"{h()} {font('-#Nc Slay')}\n{h()} {font('Select an NC mode:')}", _arise_nc_markup(chat_id))
        return

    if action == "nc_rnc_menu":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "rnc_menu"}
        await _fast_edit(query, f"{h()} {font('-#Rnc Varients')}\n{h()} {font('Select a RNC variant:')}", _arise_rnc_markup(chat_id))
        return

    if action.startswith("nc_"):
        mode = action[3:]
        if mode in ("rnc", "rnc1", "rnc2", "rnc3", "rnc4", "rnc5", "rnc6"):
            arise_state[msg_id] = {"chat_id": chat_id, "stage": f"waiting_nc_{mode}"}
            await _fast_edit(query, f"{h()} {font(f'Send the base text for {mode.upper()}:')}\n\n{font('Type it in this chat now.')}", None)
            return

        prompts = {
            "gcnc":  f"{font('Send the base text for GCNC:')}",
            "ncemo": f"{font('Send the base text for NCEMO:')}",
            "kengnc":f"{font('Send the NAME for KENGNC:')}",
            "rxsnc": f"{font('Send the NAME for RXSNC:')}",
            "spmnc": f"{font('Send the NAME for SPMNC:')}",
            "rdesc": f"{font('Send the base text for RDESC:')}",
        }

        if mode == "pfpnc":
            arise_state[msg_id] = {"chat_id": chat_id, "stage": "waiting_pfp"}
            await _fast_edit(query, f"{h()} {font('PFPNC')}\n{h()} {font('Send a photo now.')}", None)
            return

        if mode in prompts:
            arise_state[msg_id] = {"chat_id": chat_id, "stage": f"waiting_nc_{mode}"}
            await _fast_edit(query, f"{h()} {prompts[mode]}\n\n{font('Type it in this chat now.')}", None)
            return

    if action == "spm":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "spm_menu"}
        await _fast_edit(query, f"{h()} {font('-#Slay/Spm')}\n{h()} {font('Select a spam mode:')}", _arise_spm_markup(chat_id))
        return

    if action == "spm_rstyle_menu":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "rstyle_menu"}
        await _fast_edit(query, f"{h()} {font('-#Rstyle Varients')}\n{h()} {font('Select a RSTYLE variant:')}", _arise_rstyle_markup(chat_id))
        return

    if action.startswith("spm_"):
        mode = action[4:]

        if mode in ("rstyle", "rstyle1", "rstyle2", "rstyle3", "rstyle4", "rstyle5", "rstyle6"):
            arise_state[msg_id] = {"chat_id": chat_id, "stage": f"waiting_spm_{mode}"}
            await _fast_edit(query, f"{h()} {font(f'Send the NAME for {mode.upper()}:')}\n\n{font('Type it in this chat now.')}", None)
            return

        if mode == "pin":
            rpin_tasks[chat_id] = True
            await _fast_edit(query, f"{h()} {font('rpin Mode ON for this chat.')}", _arise_spm_markup(chat_id))
            return

        if mode == "rfrwd":
            arise_state[msg_id] = {"chat_id": chat_id, "stage": "waiting_rfrwd"}
            await _fast_edit(query, f"{h()} {font('RFRWD')}\n{h()} {font('Send the message/photo/video/gif to forward, or paste a message link from this group.')}\n\n{font('Example:')}\n{font('https://t.me/c/123456789/123')}\n{font('OR just send/reply the message here.')}", None)
            return

        if mode == "slidespam":
            arise_state[msg_id] = {"chat_id": chat_id, "stage": "waiting_slidespam"}
            await _fast_edit(query, f"{h()} {font('SLIDESPAM')}\n{h()} {font('Send in this format:')}\n\n{font('USER_ID or MESSAGE_LINK + TEXT')}\n\n{font('Example:')}\n{font('123456789 Your text here')}\n{font('OR')}\n{font('https://t.me/c/123456789/123 Your text here')}\n\n{font('You can also reply to a message with text in old way.')}", None)
            return

        prompts = {
            "spm":     f"{font('Send the text for SPM:')}",
            "rspm":    f"{font('Send the NAME for RSPM:')}",
            "rxsspam": f"{font('Send the text for RXSSPAM:')}",
            "poll":    f"{font('Send the NAME for POLL:')}",
        }
        if mode in prompts:
            arise_state[msg_id] = {"chat_id": chat_id, "stage": f"waiting_spm_{mode}"}
            await _fast_edit(query, f"{h()} {prompts[mode]}\n\n{font('Type it in this chat now.')}", None)
            return

    if action == "rslay":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "rslay_menu"}
        await _fast_edit(query, f"{h()} {font('-#Rslay')}\n{h()} {font('Select a mode:')}", _arise_rslay_markup(chat_id))
        return

    if action in ("r_replyrxs", "r_swipe", "r_react"):
        r_mode = action[2:]
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "waiting_r_uid", "r_mode": r_mode}
        extra = ""
        if r_mode == "react":
            extra = f"\n{h()} {font('Optionally append the emoji after the ID (e.g. 123456 then emoji). Default is heart.')}"
        await _fast_edit(query, f"{h()} {font('Send the numeric User ID of the target.')}{extra}", None)
        return

    if action == "stop":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "stop_menu"}
        await _fast_edit(query, f"{h()} {font('-#Stop slay')}\n{h()} {font('Tap any button to stop that loop.')}", _arise_stop_markup(chat_id))
        return

    if action.startswith("st_"):
        what = action[3:]
        stop_map = {
            "gcnc": gcnc_tasks, "ncemo": ncemo_tasks, "rnc": snc_tasks,
            "kengnc": keng_tasks, "rxsnc": rxsnc_tasks, "spmnc": spmnc_tasks,
            "pfpnc": pfpnc_tasks, "rdesc": rdesc_tasks,
            "spm": spm_loop_tasks, "rstyle": rstyle_tasks, "rspm": rspm_tasks,
            "rxsspam": rxsspam_tasks, "slidespam": slidespam_tasks, "poll": poll_tasks,
            "rfrwd": rfrwd_tasks,
        }
        if what == "rpin":
            rpin_tasks.pop(chat_id, None)
            msg_txt = f"{h()} {font('RPIN stopped.')}"
        elif what == "replyrxs":
            replyrxs_targets.clear()
            msg_txt = f"{h()} {font('REPLYRXS targets cleared.')}"
        elif what == "swipe":
            swipe_targets.clear()
            msg_txt = f"{h()} {font('SWIPE targets cleared.')}"
        elif what == "react":
            react_targets.clear()
            msg_txt = f"{h()} {font('REACT targets cleared.')}"
        elif what == "rights":
            stop_rights_spam(chat_id)
            msg_txt = f"{h()} {font('RIGHTS SPAM stopped for this chat.')}"
        elif what == "all":
            for task_dict in (gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks,
                              spmnc_tasks, pfpnc_tasks, rdesc_tasks, spm_loop_tasks,
                              rstyle_tasks, rspm_tasks, rxsspam_tasks, slidespam_tasks,
                              poll_tasks, rfrwd_tasks):
                stop_task(task_dict, chat_id)
                task_dict.pop(chat_id, None)
            rpin_tasks.pop(chat_id, None); replyrxs_targets.clear(); swipe_targets.clear(); react_targets.clear()
            stop_rights_spam(chat_id)
            msg_txt = f"{h()} {font('EVERYTHING stopped in this chat.')}"
        elif what in stop_map:
            stop_task(stop_map[what], chat_id)
            stop_map[what].pop(chat_id, None)
            msg_txt = f"{h()} {font('Stopped:')} {font(what.upper())}"
        else:
            msg_txt = f"{h()} {font('Unknown stop action.')}"

        await _fast_edit(query, msg_txt, _arise_stop_markup(chat_id))
        return

    if action == "sudo":
        arise_state[msg_id] = {"chat_id": chat_id, "stage": "sudo_menu"}
        await _fast_edit(query, f"{h()} {font('#Sudo/Uptime')}\n{h()} {font('Select an action:')}", _arise_sudo_markup(chat_id))
        return

    if action.startswith("su_"):
        sub = action[3:]
        if sub == "status":
            active_chats = set()
            all_tasks = [gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks, spmnc_tasks, pfpnc_tasks, spm_loop_tasks, rspm_tasks, rstyle_tasks, slidespam_tasks, poll_tasks, rdesc_tasks, rfrwd_tasks]
            for t in all_tasks:
                for cid, bdict in t.items():
                    if isinstance(bdict, dict) and any(bdict.values()): active_chats.add(cid)
                    elif bdict: active_chats.add(cid)
            if not active_chats and not rpin_tasks and not rights_spam_tasks:
                txt = f"{h()} {font('No active loops running.')}"
            else:
                lines = [f"{h()} {font('Active Loops:')}\n"]
                for cid in active_chats:
                    cname = chat_names.get(cid, "Unknown")
                    running = []
                    task_names = ["GCNC","NCEMO","RNC","KENG","RXSNC","SPMNC","PFPNC","SPM","RSPM","RSTYLE","SLIDESPAM","POLL","RDESC","RFRWD"]
                    for i, t in enumerate(all_tasks):
                        val = t.get(cid, {})
                        if (isinstance(val, dict) and any(val.values())) or (not isinstance(val, dict) and val):
                            if isinstance(val, dict):
                                cnt = sum(1 for x in val.values() if x)
                                running.append(f"{task_names[i]}({cnt})")
                            else:
                                running.append(task_names[i])
                    if rpin_tasks.get(cid):
                        running.append("RPIN")
                    if rights_spam_tasks.get(cid):
                        running.append("RIGHTS")
                    lines.append(f"{h()} {font('Group:')} {cname} | `{cid}`")
                    lines.append(f"  {font('Loops:')} {', '.join(running)}\n")
                txt = "\n".join(lines)
            await _fast_edit(query, txt, _arise_sudo_markup(chat_id))
            return

        if sub == "uptime":
            total = int(time.time() - BOT_START_TIME)
            d = total // 86400; hh = (total % 86400)//3600; mm = (total%3600)//60; ss = total%60
            await _fast_edit(query, f"{h()} {font('Uptime:')} {d} {font('days')} {hh} {font('hours')} {mm} {font('mins')} {ss} {font('secs')}", _arise_sudo_markup(chat_id))
            return

        if sub == "activebots":
            lines = [f"{h()} {font('Active Bots:')}\n"]
            for u, b in zip(bot_usernames, bots):
                fw = bot_floodwait.get(b.id, 0)
                status = f"{font('FloodWait')} {int(fw-time.time())}s" if fw > time.time() else font("Active")
                lines.append(f"• @{u} — {status}")
            await _fast_edit(query, "\n".join(lines), _arise_sudo_markup(chat_id))
            return

        if sub == "missing":
            cid = query.message.chat_id
            missing = []
            for b in bots:
                try:
                    member = await b.get_chat_member(cid, b.id)
                    if member.status in ['left','kicked','banned']: missing.append(b.username)
                except: missing.append(b.username)
            txt = f"{h()} {font('No missing bots.')}" if not missing else f"{h()} {font('Missing Bots:')}\n" + "\n".join(f"@{u}" for u in missing)
            await _fast_edit(query, txt, _arise_sudo_markup(chat_id))
            return

        if sub == "links":
            active_chats = set()
            all_tasks = [gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks, spmnc_tasks, pfpnc_tasks, spm_loop_tasks, rspm_tasks, rstyle_tasks, slidespam_tasks, poll_tasks]
            for t in all_tasks:
                for cid, bdict in t.items():
                    if isinstance(bdict, dict) and any(bdict.values()): active_chats.add(cid)
                    elif bdict: active_chats.add(cid)
            if not active_chats:
                txt = f"{h()} {font('No active loops.')}"
            else:
                lines = [f"{h()} {font('Active Links:')}\n"]
                for cid in active_chats:
                    link = None
                    for b in bots:
                        try:
                            link = await b.export_chat_invite_link(cid); break
                        except telegram.error.RetryAfter as e:
                            await wait_for_bot_floodwait(b, e.retry_after, "exportChatInviteLink", already_recorded=True)
                            break
                        except: pass
                    if link:
                        lines.append(f"• {chat_names.get(cid, cid)}: {link}")
                txt = "\n".join(lines)
            try:
                await query.edit_message_caption(caption=txt, reply_markup=_arise_sudo_markup(chat_id))
            except:
                try: await query.edit_message_text(txt, reply_markup=_arise_sudo_markup(chat_id), disable_web_page_preview=True)
                except: pass
            return

        if sub == "myid":
            await _fast_edit(query, f"{h()} {font('Your ID:')} `{query.from_user.id}`", _arise_sudo_markup(chat_id), parse_mode="Markdown")
            return

        if sub == "listsudo":
            await _fast_edit(query, f"{h()} {font('SUDO Users:')}\n" + "\n".join(f"• `{u}`" for u in SUDO_USERS), _arise_sudo_markup(chat_id), parse_mode="Markdown")
            return

        if sub == "stopall":
            stop_all_managed_workers(
                gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks,
                spmnc_tasks, pfpnc_tasks, spm_loop_tasks, rspm_tasks,
                rstyle_tasks, rxsspam_tasks, slidespam_tasks, poll_tasks,
                rdesc_tasks, rfrwd_tasks, rights_spam_tasks, rights_check_tasks,
            )
            rpin_tasks.clear()
            await _fast_edit(query, f"{h()} {font('ALL loops stopped globally.')}", _arise_sudo_markup(chat_id))
            return


async def _safe_answer(query):
    try:
        await query.answer()
    except:
        pass


async def _fast_edit(query, caption: str, markup, parse_mode=None):
    """Try edit_message_caption first (works if original msg had media), then edit_message_text."""
    try:
        if parse_mode:
            await query.edit_message_caption(caption=caption, reply_markup=markup, parse_mode=parse_mode)
        else:
            await query.edit_message_caption(caption=caption, reply_markup=markup)
        return
    except:
        pass
    try:
        if parse_mode:
            await query.edit_message_text(text=caption, reply_markup=markup, parse_mode=parse_mode)
        else:
            await query.edit_message_text(text=caption, reply_markup=markup)
    except:
        pass


# ============================================================
# ORIGINAL COMMANDS
# ============================================================

def _build_non_sudo_start_markup(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(font("-#ƘЄƝƓ"), url=f"tg://user?id={OWNER_ID}")],
        [InlineKeyboardButton(font("-#ƖƝƑƠ"), callback_data="start_info")],
        [InlineKeyboardButton(font("#ƠƤЄƝ MЄƝƲ"), callback_data=f"arise|main|{chat_id}")],
    ])


async def _send_with_media(bot, chat_id, caption, markup):
    """Helper to send THIS bot's startpfp media with caption+markup, falls back to text."""
    async with STARTPFP_LOCK:
        entry = _get_bot_startpfp(bot)
        if not entry:
            return await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup)

        path = entry.get("path")
        mtype = entry.get("type")
        file_id = entry.get("file_id")

    if file_id:
        try:
            if mtype == "photo":
                return await asyncio.wait_for(bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, reply_markup=markup), timeout=8.0)
            elif mtype == "video":
                return await asyncio.wait_for(bot.send_video(chat_id=chat_id, video=file_id, caption=caption, reply_markup=markup), timeout=8.0)
            elif mtype == "animation":
                return await asyncio.wait_for(bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption, reply_markup=markup), timeout=8.0)
        except asyncio.TimeoutError as error:
            _record_transient_backoff(bot, "sendStartMedia", error)
        except (telegram.error.Forbidden, telegram.error.BadRequest, telegram.error.TimedOut, telegram.error.NetworkError):
            pass
        except Exception:
            pass

    if path and os.path.exists(path):
        try:
            with open(path, "rb") as f:
                if mtype == "photo":
                    sent = await bot.send_photo(chat_id=chat_id, photo=f, caption=caption, reply_markup=markup)
                elif mtype == "video":
                    sent = await bot.send_video(chat_id=chat_id, video=f, caption=caption, reply_markup=markup)
                elif mtype == "animation":
                    sent = await bot.send_animation(chat_id=chat_id, animation=f, caption=caption, reply_markup=markup)
                else:
                    sent = None
                if sent:
                    try:
                        async with STARTPFP_LOCK:
                            if mtype == "photo" and sent.photo:
                                entry["file_id"] = sent.photo[-1].file_id
                            elif mtype == "video" and sent.video:
                                entry["file_id"] = sent.video.file_id
                            elif mtype == "animation" and sent.animation:
                                entry["file_id"] = sent.animation.file_id
                            _save_startpfp()
                    except:
                        pass
                    return sent
        except (telegram.error.Forbidden, telegram.error.BadRequest, telegram.error.TimedOut, telegram.error.NetworkError):
            pass
        except Exception:
            pass

    return await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup)


async def _send_start_for_bot(bot, uid, chat_id):
    """Send the start menu using the given bot (used to have ALL bots reply in groups)."""
    if uid in SUDO_USERS:
        caption = f"{h()} {font('Welcome to Rxs Bot Engine!')}\n{h()} {font('Use !help to see all commands.')}"
        try:
            await send_menu_with_media(bot, chat_id, caption, _build_start_markup(chat_id))
        except:
            try:
                await bot.send_message(chat_id=chat_id, text=caption)
            except:
                pass
        return

    caption = f"{h()} {font('Welcome to Rxs Bot Engine!')}"
    markup = _build_non_sudo_start_markup(chat_id)
    try:
        await _send_with_media(bot, chat_id, caption, markup)
    except:
        try:
            await bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup)
        except:
            pass


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    if is_processed(update):
        return

    uid = update.effective_user.id

    if uid in pending_links and context.bot.id in pending_links[uid]:
        link = pending_links[uid].pop(context.bot.id)
        await fast_reply(update, f"{h()} {font('Here is your requested link:')}\n{link}")
        return

    chat_id = update.effective_chat.id
    is_group = update.effective_chat.type in ('group', 'supergroup')

    # FIX: Only send the menu ONCE in groups (using the bot that received the command)
    # to prevent non-stop menu spamming from all bots.
    if is_group:
        await _send_start_for_bot(context.bot, uid, chat_id)
    else:
        await _send_start_for_bot(context.bot, uid, chat_id)


async def start_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    owner_name = "Owner"
    try:
        owner_user = await context.bot.get_chat(OWNER_ID)
        if owner_user.first_name:
            owner_name = owner_user.first_name
            if owner_user.last_name:
                owner_name += " " + owner_user.last_name
    except:
        pass
    text = f"{h()} {font('I am an advanced bot created by')} {font(owner_name)}"
    try:
        await query.answer(text=text, show_alert=True)
    except:
        try:
            await query.answer(text=text, show_alert=True)
        except:
            pass


@only_sudo
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    caption = f"{h()} {font('Welcome to Rxs Bot Menu!')}\n{font('Select a category below:')}"
    try:
        await send_menu_with_media(context.bot, chat_id, caption, HELP_MAIN_MARKUP)
    except:
        await fast_reply(update, caption, reply_markup=HELP_MAIN_MARKUP)

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return

    if query.from_user.id not in SUDO_USERS:
        try: await query.answer(f"{h()} {font('You are not SUDO!')}", show_alert=True)
        except: pass
        return

    asyncio.create_task(_safe_answer(query))

    data = query.data

    if data == "help_close":
        try: await query.message.delete()
        except: pass
        return

    if data == "help_main":
        await _fast_edit(query, f"{h()} {font('Rxs v5!')}\n{font('Select a category below:')}", HELP_MAIN_MARKUP)
        return

    texts = {
        "help_ncs": (
            f"-#ƝƇ/ƓƦƠƲƤ {h()}\n\n"
            f"!gcnc <text>\n"
            f"!ncemo <text>\n"
            f"!pfpnc {font('(Reply to Photo/Album)')}\n"
            f"!rnc | !rnc1 up to !rnc6 <text>\n"
            f"!kengnc <name>\n"
            f"!rxsnc <name>\n"
            f"!spmnc <text>\n"
            f"!rdesc <text>\n\n"
            f"{font('Stop Commands:')}\n"
            f"!stopgcnc | !stopncemo | !stoprnc\n"
            f"!stopkengnc | !stoprxsnc | !stopspmnc\n"
            f"!stoppfpnc | !stoprdesc\n\n"
            f"!delaync <sec> | !descdelay <sec>"
        ),
        "help_spam": (
            f"-#ᏕⱠƛƳ/ᏕƤM {h()}\n\n"
            f"!spm <text>\n"
            f"!rstyle | !rstyle1 up to !rstyle6 <name>\n"
            f"!rspm <name>\n"
            f"!slidespam <text> (reply)\n"
            f"!rfrwd (reply to any msg/photo/video/gif/sticker/album)\n"
            f"!poll <name>\n\n"
            f"!rpin (pin all incoming spm in this chat)\n"
            f"!stoprpin\n\n"
            f"{font('Stop Commands (accepts chat_id):')}\n"
            f"!stopspm | !stopslidespam\n"
            f"!stoprstyle | !stoprspm\n"
            f"!stoprfrwd | !stoppoll\n\n"
            f"!delayspm <sec> | !delayrspm <sec> | !delayrfrwd <sec> | !delaypoll <sec> | !delayrxsspam <sec> | !delayrpin <sec> | !delayrstyle <sec>"
        ),
        "help_reply": (
            f"-#ƦЄƤԼƳ/ƦЄƇƬ {h()}\n\n"
            f"{font('Target Locks (reply/mention):')}\n"
            f"!replyrxs\n"
            f"!stopreplyrxs\n\n"
            f"!swipe\n"
            f"!stopswipe\n\n"
            f"!react <emoji>\n"
            f"!stopreact"
        ),
        "help_misc": (
            f"-#ᏕƲƊƠ/ƲƤƬƖMЄ {h()}\n\n"
            f"!assign (promote all bots with rights check)\n"
            f"!leave (make all bots leave this chat)\n\n"
            f"!uptime\n"
            f"!stopall (Global Killswitch)\n"
            f"!addsudo <user_id> | !addsudo (reply)\n"
            f"!delsudo <user_id> | !delsudo (reply)\n"
            f"!listsudo\n"
            f"!myid | !ping | !getlink <chat_id>\n"
            f"!status | !activebots | !missing\n"
            f"!promotebots | !getallactivelinks\n"
            f"!checkrights | !stoprightsspam"
        )
    }

    if data not in texts: return

    back_markup = InlineKeyboardMarkup([[InlineKeyboardButton(font("-#ƁƛƇƘ"), callback_data="help_main")]])
    await _fast_edit(query, font(texts[data]), back_markup)

@only_sudo
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = time.perf_counter()
    msg = update.effective_message
    try:
        reply_msg = await msg.reply_text(font("Pinging..."))
        ms = int((time.perf_counter() - st) * 1000)
        await reply_msg.edit_text(f"{h()} {font('Pong!')} {ms} ms")
    except:
        pass

@only_sudo
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fast_reply(update, f"{h()} {font('Your ID:')} `{update.effective_user.id}`", parse_mode="Markdown")

@only_sudo
async def activebots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"{h()} {font('Active Running Bots:')}\n"]
    for u, b in zip(bot_usernames, bots):
        fw = bot_floodwait.get(b.id, 0)
        if fw > time.time():
            status = f"{font('FloodWait')} {int(fw - time.time())}s"
        else:
            status = font("Active")
        lines.append(f"• @{u} — {status}")
    lines.append(f"\n{h()} {font('Total Connected:')} {len(bots)}")
    await fast_reply(update, "\n".join(lines))

@only_sudo
async def missing_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    missing = []
    for b in bots:
        try:
            member = await b.get_chat_member(cid, b.id)
            if member.status in ['left', 'kicked', 'banned']:
                missing.append(b.username)
        except:
            missing.append(b.username)
    if not missing:
        await fast_reply(update, f"{h()} {font('No bots are missing.')}")
    else:
        await fast_reply(update, f"{h()} {font('Missing Bots:')}\n" + "\n".join(f"@{u}" for u in missing))

@only_sudo
async def promotebots_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    bot_id = context.bot.id

    # Find ANY bot in the fleet that already has promote rights (not just the one that received the command)
    promoter = None
    for b in bots:
        try:
            member = await b.get_chat_member(cid, b.id)
            if member.status == 'administrator' and getattr(member, 'can_promote_members', False):
                promoter = b
                break
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(b, e.retry_after, "getChatMember", already_recorded=True)
            break
        except:
            continue

    if promoter is None:
        return await fast_reply(update, f"{h()} {font('No bot in this group has promote rights! Promote at least ONE bot manually first, then run !assign')}")

    promoted = 0
    already = 0
    failed = 0
    for b in bots:
        if b.id == promoter.id:
            continue
        try:
            member = await promoter.get_chat_member(cid, b.id)
            if member.status == 'administrator':
                already += 1
                continue
        except:
            pass
        try:
            await promoter.promote_chat_member(
                cid, b.id,
                is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                can_manage_video_chats=True, can_restrict_members=True,
                can_promote_members=True, can_change_info=True,
                can_invite_users=True, can_pin_messages=True
            )
            promoted += 1
        except telegram.error.BadRequest:
            already += 1
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(promoter, e.retry_after, "promoteChatMember", already_recorded=True)
            try:
                await promoter.promote_chat_member(
                    cid, b.id,
                    is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                    can_manage_video_chats=True, can_restrict_members=True,
                    can_promote_members=True, can_change_info=True,
                    can_invite_users=True, can_pin_messages=True
                )
                promoted += 1
            except:
                failed += 1
        except:
            failed += 1

    await fast_reply(update, f"{h()} {font('Promote done via')} @{promoter.username}: {font('promoted=')}{promoted}, {font('already=')}{already}, {font('failed=')}{failed}")

async def _fetch_link_for_chat(cid):
    for b in bots:
        try:
            link = await asyncio.wait_for(b.export_chat_invite_link(cid), timeout=5.0)
            if link:
                return link
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(b, e.retry_after, "exportChatInviteLink", already_recorded=True)
            break
        except asyncio.TimeoutError as error:
            _record_transient_backoff(b, "exportChatInviteLink", error)
            continue
        except (telegram.error.BadRequest, telegram.error.Forbidden):
            continue
        except Exception:
            continue
    cid_str = str(cid)
    if cid_str.startswith("-100"):
        return f"https://t.me/c/{cid_str[4:]}"
    return None

@only_sudo
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_chats = set()
    all_tasks = [gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks, spmnc_tasks, pfpnc_tasks, spm_loop_tasks, rspm_tasks, rstyle_tasks, slidespam_tasks, poll_tasks, rdesc_tasks, rfrwd_tasks]
    task_names = ["GCNC", "NCEMO", "RNC", "KENG", "RXSNC", "SPMNC", "PFPNC", "SPM", "RSPM", "RSTYLE", "SLIDESPAM", "POLL", "RDESC", "RFRWD"]

    for t in all_tasks:
        for cid, bdict in t.items():
            if isinstance(bdict, dict) and any(bdict.values()): active_chats.add(cid)
            elif bdict: active_chats.add(cid)

    if not active_chats and not rpin_tasks and not rights_spam_tasks:
        return await fast_reply(update, f"{h()} {font('No active loops running.')}")

    chat_list = list(active_chats)
    link_results = await asyncio.gather(
        *[_fetch_link_for_chat(cid) for cid in chat_list],
        return_exceptions=True
    )
    link_map = {}
    for cid, res in zip(chat_list, link_results):
        if isinstance(res, Exception) or not res:
            link_map[cid] = None
        else:
            link_map[cid] = res

    lines = [f"{h()} {font('Active Loops:')}\n"]
    for cid in chat_list:
        cname = chat_names.get(cid, "Unknown")
        link = link_map.get(cid)

        running = []
        for i, t in enumerate(all_tasks):
            val = t.get(cid, {})
            if (isinstance(val, dict) and any(val.values())) or (not isinstance(val, dict) and val):
                if isinstance(val, dict):
                    cnt = sum(1 for x in val.values() if x)
                    running.append(f"{task_names[i]}({cnt})")
                else:
                    running.append(task_names[i])
        if rpin_tasks.get(cid):
            running.append("RPIN")
        if rights_spam_tasks.get(cid):
            running.append("RIGHTS")

        lines.append(f"{h()} {font('Group:')} {cname}")
        if link:
            lines.append(f"  {font('Link:')} {link}")
        else:
            lines.append(f"  {font('Link:')} {font('(no link available - add bot as admin)')}")
        lines.append(f"  {font('ID:')} `{cid}`")
        lines.append(f"  {font('Loops:')} {', '.join(running)}\n")

    await fast_reply(update, "\n".join(lines), parse_mode="Markdown")

@only_sudo
async def getlink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not context.args: return await fast_reply(update, f"{h()} {font('Usage: !getlink <chat_id>')}")
    try: cid = int(context.args[0])
    except: return await fast_reply(update, f"{h()} {font('Invalid ID.')}")

    user_id = get_auth_id(update)
    link = None
    exporting_bot = None

    for b in bots:
        try:
            link = await b.export_chat_invite_link(cid)
            exporting_bot = b
            break
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(b, e.retry_after, "exportChatInviteLink", already_recorded=True)
            break
        except: pass

    if not link:
        return await fast_reply(update, f"{h()} {font('No bot is in that group or has rights to export link.')}")

    try:
        await exporting_bot.send_message(user_id, f"{h()} {font('Here is your link:')}\n{link}")
        if exporting_bot.id == context.bot.id:
            await fast_reply(update, f"{h()} {font('Link sent to your DM!')}")
        else:
            await fast_reply(update, f"{h()} @{exporting_bot.username} {font('sent you the link in DM!')}")
    except telegram.error.Forbidden:
        pending_links.setdefault(user_id, {})
        pending_links[user_id][exporting_bot.id] = link
        await fast_reply(update, f"{h()} {font('I cannot DM you. Please send /start to')} @{exporting_bot.username} {font('to get your link!')}")

@only_sudo
async def getallactivelinks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = None
    try:
        msg = await update.effective_message.reply_text(f"{h()} {font('Generating links...')}")
    except:
        msg = await update.effective_chat.send_message(f"{h()} {font('Generating links...')}")

    active_chats = set()
    all_tasks = [gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks, spmnc_tasks, pfpnc_tasks, spm_loop_tasks, rspm_tasks, rstyle_tasks, slidespam_tasks, poll_tasks]

    for t in all_tasks:
        for cid, bdict in t.items():
            if isinstance(bdict, dict) and any(bdict.values()): active_chats.add(cid)
            elif bdict: active_chats.add(cid)

    if not active_chats:
        try: return await msg.edit_text(f"{h()} {font('No active loops.')}")
        except: return

    keyboard = []
    for cid in active_chats:
        link = None
        for b in bots:
            try:
                link = await b.export_chat_invite_link(cid)
                break
            except telegram.error.RetryAfter as e:
                await wait_for_bot_floodwait(b, e.retry_after, "exportChatInviteLink", already_recorded=True)
                break
            except: pass
        if link:
            cname = chat_names.get(cid, str(cid))
            keyboard.append([InlineKeyboardButton(text=font(f"Join {cname[:20]}"), url=link)])

    if not keyboard:
        try: return await msg.edit_text(f"{h()} {font('Failed to generate links (no admin rights).')}")
        except: return

    pages = [keyboard[i:i+10] for i in range(0, len(keyboard), 10)]
    pid = str(msg.message_id)
    pagination_data[pid] = pages

    nav_buttons = []
    if len(pages) > 1:
        nav_buttons.append(InlineKeyboardButton(text=font("Next"), callback_data=f"page_{pid}_1"))

    reply_markup = InlineKeyboardMarkup(pages[0] + ([nav_buttons] if nav_buttons else []))
    try: await msg.edit_text(f"{h()} {font('Active Group Links:')}", reply_markup=reply_markup)
    except: pass

async def pagination_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return

    try: await query.answer()
    except: pass

    data = query.data.split('_')
    if data[0] == "page":
        pid = data[1]
        try: page_num = int(data[2])
        except: return
        pages = pagination_data.get(pid)
        if not pages: return

        nav = []
        if page_num > 0:
            nav.append(InlineKeyboardButton(text=font("Back"), callback_data=f"page_{pid}_{page_num-1}"))
        if page_num < len(pages) - 1:
            nav.append(InlineKeyboardButton(text=font("Next"), callback_data=f"page_{pid}_{page_num+1}"))

        markup = InlineKeyboardMarkup(pages[page_num] + ([nav] if nav else []))
        try: await query.edit_message_reply_markup(reply_markup=markup)
        except: pass

@only_sudo
async def gcnc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !gcnc <text>')}")
    start_task(gcnc_tasks, update.effective_chat.id, bot_loop, text, gcnc_tasks, "raid")
    await fast_reply(update, f"{h()} {font('GC Name loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def ncemo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !ncemo <text>')}")
    start_task(ncemo_tasks, update.effective_chat.id, bot_loop, text, ncemo_tasks, "emoji")
    await fast_reply(update, f"{h()} {font('Emoji GC Name loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def rnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !rnc/rnc1/rnc2... <text>')}")
    cmd_base = msg.text.split()[0].lower()
    command_name = cmd_base.lstrip("!/")
    precomputed_titles = precompute_rnc_titles(text, RNC_SYMBOLS.get(command_name, RNC_SYMBOLS["rnc"]))
    start_task(snc_tasks, update.effective_chat.id, snc_loop, precomputed_titles)
    await fast_reply(update, f"{h()} {font(f'{cmd_base.upper()} Loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def kengnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !kengnc <name>')}")
    start_task(keng_tasks, update.effective_chat.id, kengnc_loop, text)
    await fast_reply(update, f"{h()} {font('Keng NC Loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def rxsnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !rxsnc <name>')}")
    start_task(rxsnc_tasks, update.effective_chat.id, rxsnc_loop, text)
    await fast_reply(update, f"{h()} {font('RXS NC Loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def spmnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !spmnc <name>')}")
    start_task(spmnc_tasks, update.effective_chat.id, spmnc_loop, text)
    await fast_reply(update, f"{h()} {font('SPMNC Loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def pfpnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        return await fast_reply(update, f"{h()} {font('Usage: Reply to a photo/album with !pfpnc')}")

    if msg.reply_to_message.photo:
        try:
            photo_file = await msg.reply_to_message.photo[-1].get_file()
            os.makedirs("pfps", exist_ok=True)
            file_path = f"pfps/{photo_file.file_unique_id}.jpg"
            await photo_file.download_to_drive(file_path)
            rep = await msg.reply_text(f"{h()} {font('Photo downloaded! Reply with name.')}")
            pending_pfpnc[rep.message_id] = [file_path]
        except:
            await fast_reply(update, f"{h()} {font('Error downloading photo.')}")
        return

    await fast_reply(update, f"{h()} {font('Please reply to a photo (single) with !pfpnc.')}")

@only_sudo
async def stopgcnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(gcnc_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('GCNC stopped.')}")

@only_sudo
async def stopncemo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(ncemo_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('NCEMO stopped.')}")

@only_sudo
async def stoprnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(snc_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('RNC variants stopped.')}")

@only_sudo
async def stopkengnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(keng_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('KengNC stopped.')}")

@only_sudo
async def stoprxsnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(rxsnc_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('rxsNC stopped.')}")

@only_sudo
async def stopspmnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(spmnc_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('SPMNC stopped.')}")

@only_sudo
async def stoppfpnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(pfpnc_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('PFPNC stopped.')}")

@only_sudo
async def rdesc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !rdesc <text>')}")
    chat_id = update.effective_chat.id
    start_task(rdesc_tasks, chat_id, rdesc_loop, text)
    await fast_reply(update, f"{h()} {font('rdesc loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def stoprdesc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_task(rdesc_tasks, update.effective_chat.id)
    await fast_reply(update, f"{h()} {font('rdesc stopped.')}")

@only_sudo
async def descdelay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Desc interval:')} {DELAYS['desc']}s")
    try:
        DELAYS["desc"] = max(0.5, float(text))
        await fast_reply(update, f"{h()} {font('Desc delay set to')} {DELAYS['desc']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def rfrwd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        return await fast_reply(update, f"{h()} {font('Reply to a message/photo/video/gif/sticker/album with !rfrwd')}")
    chat_id = update.effective_chat.id
    from_chat_id = update.effective_chat.id
    msg_id = msg.reply_to_message.message_id
    start_task(rfrwd_tasks, chat_id, rfrwd_loop, from_chat_id, msg_id)
    await fast_reply(update, f"{h()} {font('rfrwd loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def stoprfrwd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_task(rfrwd_tasks, cid)
    await fast_reply(update, f"{h()} {font('rfrwd stopped.')}")

@only_sudo
async def delayrfrwd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('rfrwd interval:')} {DELAYS['frwd']}s")
    try:
        DELAYS["frwd"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('rfrwd delay set to')} {DELAYS['frwd']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def rpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rpin_tasks[chat_id] = True
    await fast_reply(update, f"{h()} {font('rpin Mode ON.')}")

@only_sudo
async def stoprpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rpin_tasks.pop(chat_id, None)
    await fast_reply(update, f"{h()} {font('rpin Mode OFF.')}")

@only_sudo
async def stoprightsspam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stop_rights_spam(chat_id)
    await fast_reply(update, f"{h()} {font('RIGHTS SPAM stopped in this chat.')}")

@only_sudo
async def assign_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id

    # Find ANY bot in the fleet that already has promote rights (not just the one that received the command)
    promoter = None
    for b in bots:
        try:
            member = await b.get_chat_member(cid, b.id)
            if member.status == 'administrator' and getattr(member, 'can_promote_members', False):
                promoter = b
                break
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(b, e.retry_after, "getChatMember", already_recorded=True)
            break
        except:
            continue

    if promoter is None:
        return await fast_reply(update, f"{h()} {font('No bot in this group has promote rights! Promote at least ONE bot manually first, then run !assign')}")

    assigned = 0
    already = 0
    failed = 0
    for b in bots:
        if b.id == promoter.id:
            continue
        try:
            member = await promoter.get_chat_member(cid, b.id)
            if member.status == 'administrator':
                already += 1
                continue
        except:
            pass
        try:
            await promoter.promote_chat_member(
                cid, b.id,
                is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                can_manage_video_chats=True, can_restrict_members=True,
                can_promote_members=True, can_change_info=True,
                can_invite_users=True, can_pin_messages=True
            )
            assigned += 1
        except telegram.error.BadRequest as e:
            err = str(e).lower()
            if "already" in err or "administrator" in err or "participant" in err:
                already += 1
            else:
                failed += 1
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(promoter, e.retry_after, "promoteChatMember", already_recorded=True)
            try:
                await promoter.promote_chat_member(
                    cid, b.id,
                    is_anonymous=False, can_manage_chat=True, can_delete_messages=True,
                    can_manage_video_chats=True, can_restrict_members=True,
                    can_promote_members=True, can_change_info=True,
                    can_invite_users=True, can_pin_messages=True
                )
                assigned += 1
            except:
                failed += 1
        except:
            failed += 1

    try:
        await update.effective_chat.send_message(
            f"{h()} {font('Assign done via')} @{promoter.username}: {font('promoted=')}{assigned}, {font('already=')}{already}, {font('failed=')}{failed}"
        )
    except:
        pass

@only_sudo
async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    left = 0
    failed = 0
    for b in bots:
        try:
            await b.leave_chat(cid)
            left += 1
        except telegram.error.RetryAfter as e:
            await wait_for_bot_floodwait(b, e.retry_after, "leaveChat", already_recorded=True)
            try:
                await b.leave_chat(cid)
                left += 1
            except:
                failed += 1
        except:
            failed += 1
    try:
        await update.effective_chat.send_message(
            f"{h()} {font('Leave done:')} {font('left=')}{left}, {font('failed=')}{failed}"
        )
    except:
        pass

@only_sudo
async def delaync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('NC delay:')} {DELAYS['nc']}s")
    try:
        DELAYS["nc"] = max(0.3, float(text))
        await fast_reply(update, f"{h()} {font('NC delay set to')} {DELAYS['nc']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def spm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !spm <text>')}")
    start_task(spm_loop_tasks, update.effective_chat.id, spm_loop_sender, text, spm_loop_tasks)
    await fast_reply(update, f"{h()} {font('SPM loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def rstyle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !rstyle/1-6 <name>')}")
    cmd_base = msg.text.split()[0].lower()
    style = cmd_base.replace("!", "").replace("/", "")
    start_task(rstyle_tasks, update.effective_chat.id, rstyle_loop, text, style)
    await fast_reply(update, f"{h()} {font(f'{cmd_base.upper()} loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def rspm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !rspm <name>')}")
    start_task(rspm_tasks, update.effective_chat.id, rspm_loop, text)
    await fast_reply(update, f"{h()} {font('RSPM loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def rxsspam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !rxsspam <text>')}")
    start_task(rxsspam_tasks, update.effective_chat.id, rxsspam_loop, text)
    await fast_reply(update, f"{h()} {font('rxsSpam loop started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def poll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !poll <name>')}")

    chat_id = update.effective_chat.id
    start_poll_task(chat_id, text)
    await fast_reply(update, f"{h()} {font('SPoll started.')}")

@only_sudo
async def stopspm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_task(spm_loop_tasks, cid)
    await fast_reply(update, f"{h()} {font('SPM stopped.')}")

@only_sudo
async def stoprstyle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_task(rstyle_tasks, cid)
    await fast_reply(update, f"{h()} {font('RSTYLE stopped.')}")

@only_sudo
async def stoprspm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_task(rspm_tasks, cid)
    await fast_reply(update, f"{h()} {font('RSPM stopped.')}")

@only_sudo
async def stoprxsspam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_task(rxsspam_tasks, cid)
    await fast_reply(update, f"{h()} {font('rxsSpam stopped.')}")

@only_sudo
async def stoppoll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_poll_task(cid)
    await fast_reply(update, f"{h()} {font('Poll spam stopped.')}")

@only_sudo
async def delayspm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('SPM interval:')} {DELAYS['spm']}s")
    try:
        DELAYS["spm"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('SPM delay set to')} {DELAYS['spm']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def delayrspm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('RSPM interval:')} {DELAYS['rspm']}s")
    try:
        DELAYS["rspm"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('RSPM delay set to')} {DELAYS['rspm']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

# ============================================================
# NEW DELAY COMMANDS
# ============================================================

@only_sudo
async def delaypoll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Poll interval:')} {DELAYS['poll']}s")
    try:
        DELAYS["poll"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('Poll delay set to')} {DELAYS['poll']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def delayrxsspam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('rxsSpam interval:')} {DELAYS['rxsspam']}s")
    try:
        DELAYS["rxsspam"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('rxsSpam delay set to')} {DELAYS['rxsspam']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def delayrpin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('rpin interval:')} {DELAYS['rpin']}s")
    try:
        DELAYS["rpin"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('rpin delay set to')} {DELAYS['rpin']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def delayrstyle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('RSTYLE interval:')} {DELAYS['rstyle']}s")
    try:
        DELAYS["rstyle"] = max(0.15, float(text))
        await fast_reply(update, f"{h()} {font('RSTYLE delay set to')} {DELAYS['rstyle']}s")
    except: await fast_reply(update, f"{h()} {font('Invalid number.')}")

@only_sudo
async def stopall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_all_managed_workers(
        gcnc_tasks, ncemo_tasks, snc_tasks, keng_tasks, rxsnc_tasks,
        spmnc_tasks, pfpnc_tasks, spm_loop_tasks, rspm_tasks,
        rstyle_tasks, rxsspam_tasks, slidespam_tasks, poll_tasks,
        rdesc_tasks, rfrwd_tasks, rights_check_tasks, rights_spam_tasks,
    )
    rpin_tasks.clear()
    await fast_reply(update, f"{h()} {font('ALL Loops stopped globally.')}")

@only_sudo
async def uptime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_seconds = int(time.time() - BOT_START_TIME)
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    await fast_reply(update, f"{h()} {font('Uptime:')} {days} {font('days')} {hours} {font('hours')} {minutes} {font('mins')} {seconds} {font('secs')}")

def get_target_uid(update: Update):
    msg = update.effective_message
    if not msg: return None
    if msg.reply_to_message:
        if msg.reply_to_message.sender_chat: return msg.reply_to_message.sender_chat.id
        if msg.reply_to_message.from_user: return msg.reply_to_message.from_user.id
    for ent in (msg.entities or []):
        if ent.type == 'text_mention': return ent.user.id
    return None

@only_owner
async def addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = None
    text = extract_command_text(msg.text)
    if text:
        try:
            uid = int(text.split()[0])
        except (ValueError, IndexError):
            uid = None
    if uid is None:
        uid = get_target_uid(update)
    if not uid:
        return await fast_reply(update, f"{h()} {font('Usage: !addsudo <user_id> or reply to a user.')}")
    SUDO_USERS.add(uid)
    save_sudo()
    await fast_reply(update, f"{h()} `{uid}` {font('added as SUDO.')}", parse_mode="Markdown")

@only_owner
async def delsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = None
    text = extract_command_text(msg.text)
    if text:
        try:
            uid = int(text.split()[0])
        except (ValueError, IndexError):
            uid = None
    if uid is None:
        uid = get_target_uid(update)
    if not uid:
        return await fast_reply(update, f"{h()} {font('Usage: !delsudo <user_id> or reply to a user.')}")
    if uid in SUDO_USERS:
        SUDO_USERS.remove(uid)
        save_sudo()
        await fast_reply(update, f"{h()} `{uid}` {font('removed from SUDO.')}", parse_mode="Markdown")

@only_sudo
async def listsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await fast_reply(update, f"{h()} {font('SUDO Users:')}\n" + "\n".join(f"• `{u}`" for u in SUDO_USERS), parse_mode="Markdown")

@only_sudo
async def slidespam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message: return await fast_reply(update, f"{h()} {font('Reply to a message.')}")
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !slidespam <text>')}")
    start_task(slidespam_tasks, update.effective_chat.id, slidespam_loop, msg.reply_to_message.message_id, text)
    await fast_reply(update, f"{h()} {font('Slide Spam started on')} {len(bots)} {font('bots.')}")

@only_sudo
async def stopslidespam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = extract_command_text(msg.text)
    cid = update.effective_chat.id
    if text:
        try: cid = int(text)
        except: pass
    stop_task(slidespam_tasks, cid)
    await fast_reply(update, f"{h()} {font('Slide Spam stopped.')}")

@only_sudo
async def swipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = get_target_uid(update)
    if not uid: return await fast_reply(update, f"{h()} {font('Reply to a user/channel/bot.')}")
    swipe_targets.add(uid)
    await fast_reply(update, f"{h()} {font('Swipe Mode ON for this target.')}")

@only_sudo
async def stopswipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = get_target_uid(update)
    if not uid: return await fast_reply(update, f"{h()} {font('Reply to a user/channel/bot.')}")
    swipe_targets.discard(uid)
    await fast_reply(update, f"{h()} {font('Swipe Mode stopped for this target.')}")

@only_sudo
async def replyrxs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = get_target_uid(update)
    if not uid: return await fast_reply(update, f"{h()} {font('Reply to a user/channel/bot.')}")
    replyrxs_targets.add(uid)
    await fast_reply(update, f"{h()} {font('Replyrxs enabled for target.')}")

@only_sudo
async def stopreplyrxs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = get_target_uid(update)
    if not uid: return await fast_reply(update, f"{h()} {font('Reply to a user/channel/bot.')}")
    replyrxs_targets.discard(uid)
    await fast_reply(update, f"{h()} {font('Replyrxs disabled for target.')}")

@only_sudo
async def react_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = get_target_uid(update)
    msg = update.effective_message
    if not uid: return await fast_reply(update, f"{h()} {font('Reply to a user/channel/bot.')}")
    text = extract_command_text(msg.text)
    if not text: return await fast_reply(update, f"{h()} {font('Usage: !react <emoji>')}")
    react_targets[uid] = text
    await fast_reply(update, f"{h()} {font('React Mode ON for this target with')} {text}.")

@only_sudo
async def stopreact_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = get_target_uid(update)
    if not uid: return await fast_reply(update, f"{h()} {font('Reply to a user/channel/bot.')}")
    react_targets.pop(uid, None)
    await fast_reply(update, f"{h()} {font('React Mode stopped for this target.')}")

async def check_bot_rights(bot, chat_id):
    """Return:
       - {'status': 'not_admin'} if bot is not admin in the chat
       - {'status':'administrator', 'can_change_info':bool, 'can_send_messages':bool} if admin
       - None on real exception (network error etc.) — caller should NOT touch cache
    """
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        if member.status != 'administrator':
            return {'status': 'not_admin'}
        return {
            'can_change_info': getattr(member, 'can_change_info', False),
            'can_send_messages': getattr(member, 'can_send_messages', True),
            'status': member.status
        }
    except Exception:
        return None

async def rights_monitor_loop(bot, chat_id):
    global bot_rights_cache
    cache_key = (bot.id, chat_id)
    await asyncio.sleep(random.uniform(2, 5))

    while rights_check_tasks.get(chat_id, {}).get(bot.id):
        try:
            rights = await check_bot_rights(bot, chat_id)

            # Exception — skip entirely, don't touch cache, don't alert
            if rights is None:
                await asyncio.sleep(10)
                continue

            prev = bot_rights_cache.get(cache_key)

            # Case A: bot is NOT admin now
            if rights.get('status') == 'not_admin':
                if prev is not None and prev.get('status') == 'administrator':
                    # Bot WAS admin and LOST admin → alert + spam
                    link = await _fetch_best_group_link(chat_id, exclude_bot_id=bot.id)
                    await notify_rights_loss(bot, chat_id, "removed_from_admin", link)
                    bot_rights_cache.pop(cache_key, None)
                    rights_check_tasks.get(chat_id, {}).pop(bot.id, None)
                    return
                # Bot never was admin → just cache, no spam
                bot_rights_cache[cache_key] = rights
            else:
                # Bot IS admin now
                if prev is None or prev.get('status') == 'not_admin':
                    # First time we see it as admin — just cache, no alert
                    bot_rights_cache[cache_key] = rights
                else:
                    # Was admin before, check for lost permissions
                    lost = []
                    if prev.get('can_change_info') and not rights.get('can_change_info'):
                        lost.append('can_change_info')
                    if prev.get('can_send_messages', True) and not rights.get('can_send_messages', True):
                        lost.append('can_send_messages')

                    if lost:
                        # Only spam if the chat has an active loop
                        link = await _fetch_best_group_link(chat_id, exclude_bot_id=bot.id)
                        await notify_rights_loss(bot, chat_id, ','.join(lost), link)

                    bot_rights_cache[cache_key] = rights

            await asyncio.sleep(30)  # check every 30s to reduce bandwidth/API calls
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(15)

async def notify_rights_loss(bot, chat_id, rights_lost, link=None):
    """DM the owner + start spamming the group with rights alert + pin each message.
    Group spam starts ONLY if that specific chat has an active loop running."""
    try:
        chat = await bot.get_chat(chat_id)
        group_name = chat.title or "Unknown"

        # Use the provided link (fetched via fleet) or fallback
        if not link:
            link = await _fetch_best_group_link(chat_id, exclude_bot_id=bot.id)

        owner_id = "𝙐𝙣𝙠𝙣𝙤𝙬𝙣"
        try:
            admins = await bot.get_chat_administrators(chat_id)
            for adm in admins:
                if adm.status == 'creator':
                    owner_id = adm.user.id
                    break
        except Exception:
            pass

        rights_text = rights_lost.replace('can_change_info', '𝘾𝙝𝙖𝙣𝙜𝙚 𝙂𝙧𝙤𝙪𝙥 𝙄𝙣𝙛𝙤').replace('can_send_messages', '𝙎𝙚𝙣𝙙 𝙈𝙚𝙨𝙨𝙖𝙜𝙚𝙨').replace('removed_from_admin', '𝙍𝙚𝙢𝙤𝙫𝙚𝙙 𝙁𝙧𝙤𝙢 𝘼𝙙𝙢𝙞𝙣')

        # DM the owner once
        alert_msg = (
            f" {font('BOT RIGHTS ALERT')}\n\n"
            f"{font('Bot:')} @{bot.username}\n"
            f"{font('Group:')} {group_name}\n"
            f"{font('Group ID:')} `{chat_id}`\n"
            f"{font('Group Owner ID:')} `{owner_id}`\n"
            f"{font('Group Link:')} {link}\n\n"
            f"{font('Rights Lost:')} {rights_text}\n\n"
            f"{h()} {font('Please check immediately!')}"
        )

        sent = False
        rate_limited = False
        try:
            await bot.send_message(chat_id=OWNER_ID, text=alert_msg, parse_mode="Markdown")
            sent = True
        except telegram.error.RetryAfter as error:
            rate_limited = True
            await wait_for_bot_floodwait(bot, error.retry_after, "rights notification", already_recorded=True)
        except Exception:
            pass

        if not sent and not rate_limited:
            for b in bots:
                if b.id == bot.id:
                    continue
                try:
                    await b.send_message(chat_id=OWNER_ID, text=alert_msg, parse_mode="Markdown")
                    break
                except telegram.error.RetryAfter as error:
                    await wait_for_bot_floodwait(b, error.retry_after, "rights notification", already_recorded=True)
                    break
                except Exception:
                    continue

        # INSTANT: start spamming the affected GROUP itself with pin — only if that
        # chat currently has an active loop running.
        start_rights_spam(chat_id, group_name, owner_id, link, rights_text)

    except Exception as e:
        console_print(f"{C_GRAY}[RIGHTS ALERT ERROR] {e}{C_RESET}")

def start_rights_monitor(chat_id):
    for bot in bots:
        _start_managed_worker(rights_check_tasks, chat_id, bot, rights_monitor_loop)

def stop_rights_monitor(chat_id):
    if chat_id in rights_check_tasks:
        stop_task(rights_check_tasks, chat_id)
        rights_check_tasks.pop(chat_id, None)

@only_sudo
async def checkrights_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if update.effective_chat.type not in ('group', 'supergroup'):
        return await fast_reply(update, f"{h()} {font('Use this in a group.')}")

    start_rights_monitor(cid)

    lines = [f"{h()} {font('Checking bot rights...')}\n"]
    for bot in bots:
        rights = await check_bot_rights(bot, cid)
        if rights is None or rights.get('status') == 'not_admin':
            lines.append(f"@{bot.username} — {font('Not admin / No access')}")
        else:
            ci = "OK" if rights['can_change_info'] else "NO"
            sm = "OK" if rights['can_send_messages'] else "NO"
            lines.append(f"@{bot.username} — ChangeInfo:{ci} SendMsg:{sm}")

    await fast_reply(update, "\n".join(lines))


# ============================================================
# STARTPFP HIDDEN COMMAND  (reply to pic/video/gif)
# PER-BOT: reply within a specific bot's DM to set that bot's own startpfp
# ============================================================
@only_sudo
async def startpfp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a photo/video/gif with !startpfp (or /startpfp) to set THIS bot's start media.
    Works per-bot: send the command in the specific bot's DM to set that bot's own media."""
    msg = update.effective_message
    if not msg.reply_to_message:
        return await fast_reply(update, f"{h()} {font('Usage: Reply to a photo / video / gif with !startpfp')}")

    replied = msg.reply_to_message
    media = None
    ext = ".jpg"
    mtype = "photo"

    if replied.photo:
        media = replied.photo[-1]
        ext = ".jpg"
        mtype = "photo"
    elif replied.video:
        media = replied.video
        ext = ".mp4"
        mtype = "video"
    elif replied.animation:
        media = replied.animation
        ext = ".mp4"
        mtype = "animation"

    if media is None:
        return await fast_reply(update, f"{h()} {font('Please reply to a photo / video / gif.')}")

    bot_username = context.bot.username
    if not bot_username:
        return await fast_reply(update, f"{h()} {font('Could not detect bot username.')}")

    try:
        os.makedirs("startpfp", exist_ok=True)
        f = await media.get_file()
        path = f"startpfp/{bot_username}_{f.file_unique_id}{ext}"
        await f.download_to_drive(path)

        async with STARTPFP_LOCK:
            STARTPFP_PER_BOT[bot_username] = {
                "path": path,
                "type": mtype,
                "file_id": media.file_id
            }
            _save_startpfp()
        success_msg = f"{h()} {font('STARTPFP set for')} @{bot_username}! {font('It will now appear attached to this bots /start, !arise and !help menus.')}"
        await fast_reply(update, success_msg)
    except Exception as e:
        await fast_reply(update, f"{h()} {font('Error downloading media:')} {e}")


# ============================================================
# ARISE TEXT HANDLER
# ============================================================

async def _handle_arise_pfp(update: Update, context: ContextTypes.DEFAULT_TYPE, state, msg):
    chat_id = state["chat_id"]
    media = None
    ext = ".jpg"
    if msg.photo:
        media = msg.photo[-1]
        ext = ".jpg"
    elif msg.video:
        media = msg.video
        ext = ".mp4"
    elif msg.animation:
        media = msg.animation
        ext = ".mp4"

    if media is None:
        await msg.reply_text(f"{h()} {font('Please send a photo now.')}")
        return

    try:
        os.makedirs("pfps", exist_ok=True)
        f = await media.get_file()
        path = f"pfps/{f.file_unique_id}{ext}"
        await f.download_to_drive(path)
        state["stage"] = "waiting_pfp_name"
        state["media_path"] = path
        await msg.reply_text(f"{h()} {font('Media saved! Now send the NAME for PFPNC.')}")
    except Exception as e:
        await msg.reply_text(f"{h()} {font('Error downloading media:')} {e}")


async def _handle_startpfp_media(update: Update, context: ContextTypes.DEFAULT_TYPE, state, msg):
    """Save the media the user sends for STARTPFP (pic/video/gif) — per-bot."""
    media = None
    ext = ".jpg"
    mtype = "photo"
    if msg.photo:
        media = msg.photo[-1]
        ext = ".jpg"
        mtype = "photo"
    elif msg.video:
        media = msg.video
        ext = ".mp4"
        mtype = "video"
    elif msg.animation:
        media = msg.animation
        ext = ".mp4"
        mtype = "animation"

    if media is None:
        await msg.reply_text(f"{h()} {font('Please send a photo / video / gif.')}")
        return

    bot_username = context.bot.username
    if not bot_username:
        await msg.reply_text(f"{h()} {font('Could not detect bot username.')}")
        return

    try:
        os.makedirs("startpfp", exist_ok=True)
        f = await media.get_file()
        path = f"startpfp/{bot_username}_{f.file_unique_id}{ext}"
        await f.download_to_drive(path)
        async with STARTPFP_LOCK:
            STARTPFP_PER_BOT[bot_username] = {
                "path": path,
                "type": mtype,
                "file_id": media.file_id
            }
            _save_startpfp()
        await msg.reply_text(f"{h()} {font('STARTPFP set for')} @{bot_username}!")
    except Exception as e:
        await msg.reply_text(f"{h()} {font('Error downloading media:')} {e}")


async def arise_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg: return False
    chat_id = update.effective_chat.id

    current_msg_id = None
    state = None
    for mid, st in list(arise_state.items()):
        if st.get("chat_id") == chat_id and st.get("stage", "").startswith("waiting"):
            current_msg_id = mid
            state = st
            break

    if not state:
        return False

    stage = state["stage"]

    if stage == "waiting_pfp":
        if msg.photo or msg.video or msg.animation:
            await _handle_arise_pfp(update, context, state, msg)
            return True
        if msg.text and not msg.text.startswith("!"):
            await msg.reply_text(f"{h()} {font('Please send a photo (or press BACK to cancel).')}")
            return True
        return True

    if stage == "waiting_pfp_name":
        name = msg.text or ""
        if not name:
            return True
        path = state.get("media_path")
        arise_state.pop(current_msg_id, None)
        if path:
            start_task(pfpnc_tasks, chat_id, pfpnc_loop, name, path)
            await msg.reply_text(f"{h()} {font('PFPNC started with name:')} {name}")
        return True

    # ============================================================
    # NEW: RFRWD from arise menu — supports forwarded message OR link
    # ============================================================
    if stage == "waiting_rfrwd":
        arise_state.pop(current_msg_id, None)
        
        if msg.forward_origin:
            start_task(rfrwd_tasks, chat_id, rfrwd_loop, chat_id, msg.message_id)
            await msg.reply_text(f"{h()} {font('RFRWD started! Forwarding this message repeatedly.')}")
            return True

        if msg.text:
            text = msg.text.strip()
            link_match = None
            import re
            patterns = [
                r'https?://t\.me/c/(\d+)/(\d+)',
                r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)',
                r't\.me/c/(\d+)/(\d+)',
                r't\.me/([a-zA-Z0-9_]+)/(\d+)',
            ]
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    link_match = m
                    break

            if link_match:
                groups = link_match.groups()
                if len(groups) == 2:
                    if groups[0].isdigit():
                        real_chat_id = int(f"-100{groups[0]}")
                        real_msg_id = int(groups[1])
                        if real_chat_id == chat_id:
                            start_task(rfrwd_tasks, chat_id, rfrwd_loop, real_chat_id, real_msg_id)
                            await msg.reply_text(f"{h()} {font('RFRWD started! Forwarding linked message repeatedly.')}")
                            return True
                        else:
                            await msg.reply_text(f"{h()} {font('Link must be from this group!')}")
                            return True
                    else:
                        try:
                            chat_obj = await context.bot.get_chat(f"@{groups[0]}")
                            if chat_obj.id == chat_id:
                                start_task(rfrwd_tasks, chat_id, rfrwd_loop, chat_obj.id, int(groups[1]))
                                await msg.reply_text(f"{h()} {font('RFRWD started! Forwarding linked message repeatedly.')}")
                                return True
                            else:
                                await msg.reply_text(f"{h()} {font('Link must be from this group!')}")
                                return True
                        except:
                            await msg.reply_text(f"{h()} {font('Could not resolve link. Make sure the link is valid.')}")
                            return True

            if msg.reply_to_message:
                start_task(rfrwd_tasks, chat_id, rfrwd_loop, chat_id, msg.reply_to_message.message_id)
                await msg.reply_text(f"{h()} {font('RFRWD started! Forwarding replied message repeatedly.')}")
                return True

            await msg.reply_text(f"{h()} {font('Send a message link from this group, or reply to a message.')}")
            return True

        if msg.photo or msg.video or msg.animation or msg.sticker or msg.document:
            start_task(rfrwd_tasks, chat_id, rfrwd_loop, chat_id, msg.message_id)
            await msg.reply_text(f"{h()} {font('RFRWD started! Forwarding this media repeatedly.')}")
            return True

        return True

    # ============================================================
    # NEW: SLIDESPAM from arise menu
    # ============================================================
    if stage == "waiting_slidespam":
        arise_state.pop(current_msg_id, None)

        if msg.reply_to_message:
            text = msg.text or ""
            if text:
                start_task(slidespam_tasks, chat_id, slidespam_loop, msg.reply_to_message.message_id, text)
                await msg.reply_text(f"{h()} {font('SLIDESPAM started (reply mode) on')} {len(bots)} {font('bots.')}")
                return True

        if msg.text:
            import re
            text = msg.text.strip()
            parts = text.split(None, 1)
            if len(parts) < 2:
                await msg.reply_text(f"{h()} {font('Format: USER_ID or MESSAGE_LINK + TEXT')}\n{font('Example:')}\n{font('123456789 Your text here')}\n{font('OR')}\n{font('https://t.me/c/123456789/123 Your text here')}")
                return True

            first = parts[0]
            spm_text = parts[1]

            patterns = [
                r'https?://t\.me/c/(\d+)/(\d+)',
                r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)',
                r't\.me/c/(\d+)/(\d+)',
                r't\.me/([a-zA-Z0-9_]+)/(\d+)',
            ]
            link_match = None
            for pat in patterns:
                m = re.search(pat, first)
                if m:
                    link_match = m
                    break

            target_msg_id = None

            if link_match:
                groups = link_match.groups()
                if groups[0].isdigit():
                    real_chat_id = int(f"-100{groups[0]}")
                    target_msg_id = int(groups[1])
                    if real_chat_id != chat_id:
                        await msg.reply_text(f"{h()} {font('Link must be from this group!')}")
                        return True
                else:
                    try:
                        chat_obj = await context.bot.get_chat(f"@{groups[0]}")
                        if chat_obj.id != chat_id:
                            await msg.reply_text(f"{h()} {font('Link must be from this group!')}")
                            return True
                        target_msg_id = int(groups[1])
                    except:
                        await msg.reply_text(f"{h()} {font('Could not resolve link.')}")
                        return True
            else:
                try:
                    target_uid = int(first)
                    await msg.reply_text(f"{h()} {font('For user ID, please reply to their message with text, OR use message link format.')}\n{font('Example:')}\n{font('https://t.me/c/123456789/123 Your text here')}")
                    return True
                except:
                    await msg.reply_text(f"{h()} {font('Invalid format. Send USER_ID or MESSAGE_LINK + TEXT')}")
                    return True

            if target_msg_id:
                start_task(slidespam_tasks, chat_id, slidespam_loop, target_msg_id, spm_text)
                await msg.reply_text(f"{h()} {font('SLIDESPAM started on')} {len(bots)} {font('bots.')}")
                return True

        return True

    if not msg.text:
        return True

    text = msg.text.strip()
    if text.startswith("!"):
        arise_state.pop(current_msg_id, None)
        return False

    if stage.startswith("waiting_nc_"):
        mode = stage[len("waiting_nc_"):]
        arise_state.pop(current_msg_id, None)
        if mode == "gcnc":
            start_task(gcnc_tasks, chat_id, bot_loop, text, gcnc_tasks, "raid")
        elif mode == "ncemo":
            start_task(ncemo_tasks, chat_id, bot_loop, text, ncemo_tasks, "emoji")
        elif mode in RNC_SYMBOLS:
            precomputed_titles = precompute_rnc_titles(text, RNC_SYMBOLS[mode])
            start_task(snc_tasks, chat_id, snc_loop, precomputed_titles)
        elif mode == "kengnc":
            start_task(keng_tasks, chat_id, kengnc_loop, text)
        elif mode == "rxsnc":
            start_task(rxsnc_tasks, chat_id, rxsnc_loop, text)
        elif mode == "spmnc":
            start_task(spmnc_tasks, chat_id, spmnc_loop, text)
        elif mode == "rdesc":
            start_task(rdesc_tasks, chat_id, rdesc_loop, text)
        await msg.reply_text(f"{h()} {font('Started NC mode:')} {mode}")
        return True

    if stage.startswith("waiting_spm_"):
        mode = stage[len("waiting_spm_"):]
        arise_state.pop(current_msg_id, None)
        if mode == "spm":
            start_task(spm_loop_tasks, chat_id, spm_loop_sender, text, spm_loop_tasks)
        elif mode == "rstyle":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle")
        elif mode == "rstyle1":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle1")
        elif mode == "rstyle2":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle2")
        elif mode == "rstyle3":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle3")
        elif mode == "rstyle4":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle4")
        elif mode == "rstyle5":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle5")
        elif mode == "rstyle6":
            start_task(rstyle_tasks, chat_id, rstyle_loop, text, "rstyle6")
        elif mode == "rspm":
            start_task(rspm_tasks, chat_id, rspm_loop, text)
        elif mode == "rxsspam":
            start_task(rxsspam_tasks, chat_id, rxsspam_loop, text)
        elif mode == "poll":
            start_poll_task(chat_id, text)
        await msg.reply_text(f"{h()} {font('Started SPM mode:')} {mode}")
        return True

    if stage == "waiting_r_uid":
        arise_state.pop(current_msg_id, None)
        r_mode = state.get("r_mode")
        parts = text.split()
        uid = None
        emoji = "❤️"
        try:
            uid = int(parts[0])
            if r_mode == "react" and len(parts) > 1:
                emoji = parts[1]
        except:
            uid = None

        if not uid:
            await msg.reply_text(f"{h()} {font('Invalid ID. Please send a numeric User ID.')}")
            return True

        if r_mode == "replyrxs":
            replyrxs_targets.add(uid)
            await msg.reply_text(f"{h()} {font('Replyrxs ON for')} `{uid}`", parse_mode="Markdown")
        elif r_mode == "swipe":
            swipe_targets.add(uid)
            await msg.reply_text(f"{h()} {font('Swipe ON for')} `{uid}`", parse_mode="Markdown")
        elif r_mode == "react":
            react_targets[uid] = emoji
            await msg.reply_text(f"{h()} {font('React ON for')} `{uid}` {font('with')} {emoji}", parse_mode="Markdown")
        return True

    return False


async def auto_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg: return

    uid = get_auth_id(update)
    chat_id = update.effective_chat.id

    # ============================================================
    # RESTRICT NO-PREFIX COMMANDS TO OWNER ONLY
    # ============================================================
    if msg.text:
        text = msg.text.strip()
        # Allow normal no-prefix for owner only
        if not text.startswith("!") and not text.startswith("/"):
            if uid != OWNER_ID:
                # Non-owner: only process auto-replies (react/swipe/replyrxs) and rights monitor
                # Do not treat text as a command
                pass
        # If text starts with ! or /, only owner can proceed for no-prefix; but commands
        # with prefix are handled by the command handlers anyway, so nothing to do here.
    # Continue with other auto-reply logic for all users where appropriate
    if (msg.text or "").startswith("!"):
        # prefixed commands are handled by command handlers
        pass
    else:
        # no-prefix handling: only OWNER may trigger command-like behavior
        pass

    chat_names[chat_id] = update.effective_chat.title or "Unknown"

    if update.effective_chat.type in ('group', 'supergroup'):
        if chat_id not in rights_check_tasks:
            start_rights_monitor(chat_id)

    if msg.reply_to_message:
        replied_id = msg.reply_to_message.message_id
        if replied_id in pending_pfpnc and uid in SUDO_USERS:
            name = msg.text
            if name:
                photo_paths = pending_pfpnc.pop(replied_id)
                start_task(pfpnc_tasks, chat_id, pfpnc_loop, name, photo_paths)
                await fast_reply(update, f"{h()} {font('PFP + NC Loop started with name:')} {name} ({len(photo_paths)} photo(s))")
                return

    if uid in SUDO_USERS:
        try:
            handled = await arise_text_handler(update, context)
            if handled:
                return
        except Exception as e:
            console_print(f"{C_GRAY}[ARISE TEXT ERR] {e}{C_RESET}")

    try:
        if uid in react_targets:
            emoji = react_targets[uid]
            msg_id = msg.message_id
            for b in bots:
                async def do_react(b_inst, e, c_id, m_id):
                    await asyncio.sleep(random.uniform(0.05, 0.3))
                    try: await b_inst.set_message_reaction(chat_id=c_id, message_id=m_id, reaction=e)
                    except: pass
                asyncio.create_task(do_react(b, emoji, chat_id, msg_id))

        if uid in swipe_targets:
            fname = msg.sender_chat.title if msg.sender_chat else (msg.from_user.first_name if msg.from_user else "Target")
            mention = f"[{fname}](tg://user?id={uid})" if uid > 0 else f"{fname}"
            await msg.reply_text(f"{mention} {font(random.choice(REPLY_rxs_TEXTS))}", parse_mode="Markdown")

        if uid in replyrxs_targets and REPLY_rxs_TEXTS:
            bot_limits = last_rxs_reply.setdefault(context.bot.id, {})
            now = time.time()
            if now - bot_limits.get(uid, 0) >= REPLY_rxs_DELAY:
                bot_limits[uid] = now
                await msg.reply_text(font(random.choice(REPLY_rxs_TEXTS)))
    except:
        pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        error = context.error
        if isinstance(error, telegram.error.TimedOut):
            console_print(f"{C_DARK}[TIMEOUT] Request timed out, suppressed - bot continues{C_RESET}")
        elif isinstance(error, (telegram.error.NetworkError, httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError)):
            console_print(f"{C_DARK}[NET] Network issue suppressed: {type(error).__name__}{C_RESET}")
        elif isinstance(error, telegram.error.RetryAfter):
            await wait_for_bot_floodwait(context.bot, error.retry_after, "unhandled API request", already_recorded=True)
        elif isinstance(error, telegram.error.Forbidden):
            pass
        elif isinstance(error, telegram.error.BadRequest):
            pass
        else:
            console_print(f"{C_GRAY}[ERR] {type(error).__name__}: {error}{C_RESET}")
    except:
        pass
    return

def Cmd(cmd, callback):
    if isinstance(cmd, list):
        pattern = r'(?i)^[!/]?(?:' + '|'.join(cmd) + r')(?:\s|$)'
    else:
        pattern = r'(?i)^[!/]?' + cmd + r'(?:\s|$)'
    return MessageHandler(filters.Regex(pattern), callback)

def build_app(token):
    app = Application.builder().bot(CoordinatedBot(token)).build()
    app.add_error_handler(error_handler)

    app.add_handler(Cmd("start", start_cmd), group=0)
    app.add_handler(CallbackQueryHandler(start_info_callback, pattern="^start_info$"), group=0)
    app.add_handler(Cmd("help", help_cmd), group=0)
    app.add_handler(CallbackQueryHandler(help_callback, pattern="^help_"), group=0)
    app.add_handler(Cmd("arise", arise_cmd), group=0)
    app.add_handler(CallbackQueryHandler(arise_callback, pattern="^arise\\|"), group=0)

    app.add_handler(Cmd("ping", ping_cmd), group=0)
    app.add_handler(Cmd("myid", myid), group=0)
    app.add_handler(Cmd("uptime", uptime_cmd), group=0)
    app.add_handler(Cmd("activebots", activebots_cmd), group=0)
    app.add_handler(Cmd("status", status_cmd), group=0)
    app.add_handler(Cmd("missing", missing_cmd), group=0)
    app.add_handler(Cmd("promotebots", promotebots_cmd), group=0)
    app.add_handler(Cmd("getallactivelinks", getallactivelinks_cmd), group=0)
    app.add_handler(CallbackQueryHandler(pagination_handler, pattern="^page_"), group=0)

    app.add_handler(Cmd("gcnc", gcnc), group=0)
    app.add_handler(Cmd("ncemo", ncemo), group=0)
    app.add_handler(Cmd(["rnc", "rnc1", "rnc2", "rnc3", "rnc4", "rnc5", "rnc6"], rnc_cmd), group=0)
    app.add_handler(Cmd("kengnc", kengnc_cmd), group=0)
    app.add_handler(Cmd("rxsnc", rxsnc_cmd), group=0)
    app.add_handler(Cmd("spmnc", spmnc_cmd), group=0)
    app.add_handler(Cmd("pfpnc", pfpnc_cmd), group=0)
    app.add_handler(Cmd("rdesc", rdesc_cmd), group=0)
    app.add_handler(Cmd("startpfp", startpfp_cmd), group=0)

    app.add_handler(Cmd("stopgcnc", stopgcnc_cmd), group=0)
    app.add_handler(Cmd("stopncemo", stopncemo_cmd), group=0)
    app.add_handler(Cmd("stoprnc", stoprnc_cmd), group=0)
    app.add_handler(Cmd("stopkengnc", stopkengnc_cmd), group=0)
    app.add_handler(Cmd("stoprxsnc", stoprxsnc_cmd), group=0)
    app.add_handler(Cmd("stopspmnc", stopspmnc_cmd), group=0)
    app.add_handler(Cmd("stoppfpnc", stoppfpnc_cmd), group=0)
    app.add_handler(Cmd("stoprdesc", stoprdesc_cmd), group=0)
    app.add_handler(Cmd("delaync", delaync_cmd), group=0)
    app.add_handler(Cmd("descdelay", descdelay_cmd), group=0)

    app.add_handler(Cmd("spm", spm_cmd), group=0)
    app.add_handler(Cmd(["rstyle", "rstyle1", "rstyle2", "rstyle3", "rstyle4", "rstyle5", "rstyle6"], rstyle_cmd), group=0)
    app.add_handler(Cmd("rspm", rspm_cmd), group=0)
    app.add_handler(Cmd("rxsspam", rxsspam_cmd), group=0)
    app.add_handler(Cmd("slidespam", slidespam_cmd), group=0)
    app.add_handler(Cmd("poll", poll_cmd), group=0)
    app.add_handler(Cmd("rfrwd", rfrwd_cmd), group=0)
    app.add_handler(Cmd("rpin", rpin_cmd), group=0)

    app.add_handler(Cmd("stopspm", stopspm_cmd), group=0)
    app.add_handler(Cmd("stoprstyle", stoprstyle_cmd), group=0)
    app.add_handler(Cmd("stoprspm", stoprspm_cmd), group=0)
    app.add_handler(Cmd("stoprxsspam", stoprxsspam_cmd), group=0)
    app.add_handler(Cmd("stopslidespam", stopslidespam_cmd), group=0)
    app.add_handler(Cmd("stoppoll", stoppoll_cmd), group=0)
    app.add_handler(Cmd("stoprfrwd", stoprfrwd_cmd), group=0)
    app.add_handler(Cmd("stoprpin", stoprpin_cmd), group=0)
    app.add_handler(Cmd("stoprightsspam", stoprightsspam_cmd), group=0)
    app.add_handler(Cmd("delayspm", delayspm_cmd), group=0)
    app.add_handler(Cmd("delayrspm", delayrspm_cmd), group=0)
    app.add_handler(Cmd("delayrfrwd", delayrfrwd_cmd), group=0)
    app.add_handler(Cmd("delaypoll", delaypoll_cmd), group=0)
    app.add_handler(Cmd("delayrxsspam", delayrxsspam_cmd), group=0)
    app.add_handler(Cmd("delayrpin", delayrpin_cmd), group=0)
    app.add_handler(Cmd("delayrstyle", delayrstyle_cmd), group=0)

    app.add_handler(Cmd("stopall", stopall), group=0)
    app.add_handler(Cmd("addsudo", addsudo), group=0)
    app.add_handler(Cmd("delsudo", delsudo), group=0)
    app.add_handler(Cmd("listsudo", listsudo), group=0)
    app.add_handler(Cmd("swipe", swipe_cmd), group=0)
    app.add_handler(Cmd("stopswipe", stopswipe_cmd), group=0)
    app.add_handler(Cmd("replyrxs", replyrxs_cmd), group=0)
    app.add_handler(Cmd("stopreplyrxs", stopreplyrxs_cmd), group=0)
    app.add_handler(Cmd("react", react_cmd), group=0)
    app.add_handler(Cmd("stopreact", stopreact_cmd), group=0)
    app.add_handler(Cmd("getlink", getlink_cmd), group=0)
    app.add_handler(Cmd("assign", assign_cmd), group=0)
    app.add_handler(Cmd("leave", leave_cmd), group=0)
    app.add_handler(Cmd("checkrights", checkrights_cmd), group=0)

    app.add_handler(MessageHandler(filters.ALL, auto_replies), group=1)
    return app

async def _start_single_bot(token, idx):
    """Start one bot. Returns (app, me) or None on failure."""
    app = build_app(token)
    initialized = False

    async def close_partial_app():
        try:
            if app.updater and app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
            if initialized:
                await app.shutdown()
        except Exception as cleanup_error:
            console_print(f"{C_GRAY}[CLEANUP ERROR] bot startup cleanup failed ({type(cleanup_error).__name__}){C_RESET}")

    try:
        try:
            await app.initialize()
        except telegram.error.RetryAfter as error:
            await wait_for_bot_floodwait(app.bot, error.retry_after, "startup", already_recorded=True)
            await app.initialize()
        except Exception as error:
            if not _is_transient_api_error(error):
                raise
            _, state = _bot_api_state(app.bot)
            await _wait_for_bot_deadline(state)
            await app.initialize()
        initialized = True
        me = await app.bot.get_me()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
        console_print(f"{C_GRAY}[SUCCESS] Bot running: @{me.username}{C_RESET}")
        return (app, me)
    except telegram.error.Conflict:
        await close_partial_app()
        uname = getattr(app.bot, "username", None) or "Unknown"
        console_print(f"{C_GRAY}[CONFLICT] Double instance detected for @{uname}!{C_RESET}")
        return ("CONFLICT", uname)
    except asyncio.CancelledError:
        await close_partial_app()
        raise
    except Exception as e:
        await close_partial_app()
        console_print(
            f"{C_GRAY}[FAILED] Bot startup failed ({type(e).__name__}: {e}); "
            f"no further retry{C_RESET}"
        )
        return None


# ============================================================
# HEALTH CHECK HTTP SERVER (Inhank Cloud, Render, UptimeRobot, etc.)
# Lightweight native asyncio HTTP server — responds 200 OK to ANY GET/HEAD route.
# ============================================================
async def _health_server():
    async def _handle(reader, writer):
        try:
            data = await asyncio.wait_for(reader.read(2048), timeout=5.0)
            if not data:
                return
            line = data.split(b"\r\n", 1)[0].decode(errors="ignore")
            parts = line.split(" ") if line else []
            method = parts[0].upper() if parts else ""
            path = parts[1] if len(parts) > 1 else "/"

            # Respond 200 OK to all GET or HEAD requests (/, /health, /healthz, /ping, /live, etc.)
            if method in ("GET", "HEAD"):
                body_dict = {
                    "status": "healthy",
                    "engine": "Rxs Multi-Bot Engine",
                    "bots_online": len(bots),
                    "uptime_seconds": max(0, int(time.time() - BOT_START_TIME))
                }
                body = (json.dumps(body_dict, indent=2) + "\n").encode("utf-8")
                head = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(head.encode("ascii"))
                if method == "GET":
                    writer.write(body)
            else:
                writer.write(
                    b"HTTP/1.1 405 Method Not Allowed\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            except Exception:
                pass

    candidate_ports = []
    env_port = os.environ.get("PORT")
    if env_port and env_port.isdigit():
        candidate_ports.append(int(env_port))
    for p in (8080, 80, 3000, 5000, 8000):
        if p not in candidate_ports:
            candidate_ports.append(p)

    servers = []
    for port in candidate_ports:
        try:
            server = await asyncio.start_server(_handle, "0.0.0.0", port)
            servers.append(server)
            console_print(f"{C_GRAY}[HEALTH] Listening on 0.0.0.0:{port} (All GET/HEAD routes -> 200 OK){C_RESET}")
        except Exception:
            pass

    if servers:
        await asyncio.gather(*(s.serve_forever() for s in servers))


async def run_all_bots():
    global apps, bots, bot_usernames, TOKENS
    conflicts = []

    # 1. Start the health server FIRST and yield briefly so ports are open before Telegram polling begins
    asyncio.create_task(_health_server())
    await asyncio.sleep(0.15)

    if not _console_dashboard.enabled:
        console_print(
            "[INFO] Non-interactive log mode active. Live dashboard disabled; standard log streaming enabled."
        )

    # 2. Load tokens from environment or Tokens.txt
    TOKENS = _load_bot_tokens()

    # 3. If no tokens found yet, keep health server alive and poll for tokens
    if not TOKENS:
        console_print(
            f"\n{C_GRAY}"
            f"====================================================================\n"
            f"[ACTION REQUIRED] No Telegram bot tokens found in Tokens.txt or ENV!\n"
            f"The health server is active to pass deployment checks. Waiting...\n"
            f"====================================================================\n"
            f"{C_RESET}"
        )
        while not TOKENS:
            await asyncio.sleep(15)
            TOKENS = _load_bot_tokens()

    valid_tokens = [t for t in TOKENS if TOKEN_PATTERN.fullmatch(t.strip())]
    console_print(f"{C_GRAY}[INFO] Loaded {len(valid_tokens)} bot token(s) from configuration.{C_RESET}")

    while True:
        results = await asyncio.gather(*[_start_single_bot(t, i) for i, t in enumerate(valid_tokens)])

        for res in results:
            if res is None:
                continue
            if isinstance(res, tuple) and len(res) == 2 and res[0] == "CONFLICT":
                conflicts.append(res[1])
                continue
            app, me = res
            bot_usernames.append(me.username)
            apps.append(app)
            bots.append(app.bot)
            bot_floodwait[app.bot.id] = 0

        if apps and bots:
            break

        console_print(f"{C_GRAY}[WARN] No bots started yet (conflicts or network error). Retrying in 20s while keeping health check alive...{C_RESET}")
        conflicts.clear()
        await asyncio.sleep(20)

    if conflicts and bots:
        try:
            msg = f"⚠️ {font('Double Instance Detected!')}\n{font('These bots are running on another server/process. Please revoke their tokens from @BotFather:')}\n\n"
            for uname in conflicts:
                msg += f"@{uname}\n\n"
            await bots[0].send_message(chat_id=OWNER_ID, text=msg, parse_mode="Markdown")
            console_print(f"{C_DARK}[INFO] Sent conflict report to OWNER_ID via @{bots[0].username}{C_RESET}")
        except Exception as e:
            console_print(f"{C_GRAY}[ERROR] Could not send conflict report: {e}{C_RESET}")

    console_print(f"\n{C_GRAY}[START] Rxs Multi-Bot Engine running with {len(bots)} bots!{C_RESET}")
    console_print(f"{C_GRAY}[INFO] NC delay: {DELAYS['nc']}s (all bots staggered - no floodwait deaths){C_RESET}")
    console_print(f"{C_GRAY}[OK] FloodWait recovery: bots wait & resume, never quit{C_RESET}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_all_bots())
    except KeyboardInterrupt:
        console_print(f"\n{C_DARK}[STOP] Shutting down gracefully...{C_RESET}")
    except Exception as e:
        console_print(f"\n{C_GRAY}[FATAL] Fatal error: {e}{C_RESET}")
