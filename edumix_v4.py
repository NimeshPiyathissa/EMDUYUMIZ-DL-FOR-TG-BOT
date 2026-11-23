#!/usr/bin/env python3

import os
import sys
import re
import json
import time
import shlex
import shutil
import asyncio
import tempfile
import base64
import html
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit, urljoin
from typing import Optional, List, Tuple, Dict, Any

if sys.version_info >= (3, 12):
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(DATA_DIR, ".env")
AUTH_STORE_FILE = os.path.join(DATA_DIR, "auth_users.json")

try:
    from dotenv import load_dotenv
    if os.path.isfile(ENV_FILE):
        load_dotenv(dotenv_path=ENV_FILE)
    else:
        load_dotenv()
except ImportError:
    pass

try:
    from pyrogram import Client, filters
    from pyrogram.types import (
        Message,
        CallbackQuery,
        InlineKeyboardButton,
        InlineKeyboardMarkup,
    )
    from pyrogram.errors import FloodWait, MessageNotModified
except ImportError:
    print(
        "ERROR: Required library 'pyrogram' / 'pyrofork' is not installed.\n"
        "Please install it using:\n"
        "    pip install pyrofork tgcrypto\n"
        "or:\n"
        "    pip install pyrogram tgcrypto\n"
    )
    sys.exit(1)

def resolve_tool_path(name: str, preferred: str) -> str:
    if shutil.which(preferred) or (os.path.isfile(preferred) and os.access(preferred, os.X_OK)):
        return preferred
    venv_tool = os.path.join(sys.prefix, "bin", name)
    if os.path.isfile(venv_tool) and os.access(venv_tool, os.X_OK):
        return venv_tool
    local_bin = os.path.join("/usr/local/bin", name)
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    bento_bin = os.path.join("/opt/bento4", name)
    if os.path.isfile(bento_bin) and os.access(bento_bin, os.X_OK):
        return bento_bin
    return preferred

API_ID_RAW = (os.environ.get("API_ID") or "").strip()
API_ID = int(API_ID_RAW) if API_ID_RAW.isdigit() else 0
API_HASH = (os.environ.get("API_HASH") or "").strip()
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
ADMIN_IDS_RAW = (os.environ.get("ADMIN_IDS") or os.environ.get("ADMIN_ID") or "").strip()
ADMIN_IDS: List[int] = [
    int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()
]
AUTH_USERS_RAW = (os.environ.get("AUTH_USERS") or "").strip()

YTDLP_PATH = resolve_tool_path("yt-dlp", os.environ.get("YTDLP_PATH", "yt-dlp"))
MP4DECRYPT_PATH = resolve_tool_path("mp4decrypt", os.environ.get("MP4DECRYPT_PATH", "mp4decrypt"))

BROWSER_UA = os.environ.get(
    "BROWSER_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

def load_auth_users() -> set[int]:
    users: set[int] = set()
    if AUTH_USERS_RAW:
        for x in AUTH_USERS_RAW.split(","):
            if x.strip().isdigit():
                users.add(int(x.strip()))
    users.update(ADMIN_IDS)
    if os.path.isfile(AUTH_STORE_FILE):
        try:
            with open(AUTH_STORE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for u in data:
                        if str(u).isdigit():
                            users.add(int(u))
        except Exception:
            pass
    return users

def save_auth_users(users: set[int]):
    try:
        with open(AUTH_STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(users)), f, indent=2)
    except Exception:
            pass

AUTH_USERS_SET: set[int] = load_auth_users()
save_auth_users(AUTH_USERS_SET)

def is_admin(user_id: Optional[int]) -> bool:
    return bool(user_id and user_id in ADMIN_IDS)

def is_authorized(user_id: Optional[int]) -> bool:
    return bool(user_id and (user_id in AUTH_USERS_SET or user_id in ADMIN_IDS))

auth_filter = filters.create(
    lambda _, __, m: bool(m.from_user and is_authorized(m.from_user.id))
)
unauth_filter = filters.create(
    lambda _, __, m: bool(m.from_user and not is_authorized(m.from_user.id))
)
admin_filter = filters.create(
    lambda _, __, m: bool(m.from_user and is_admin(m.from_user.id))
)

USER_STATES: Dict[int, Dict[str, Any]] = {}

def sanitize_filename(name: str) -> str:
    name = re.sub(r"\.(mp4|mkv|ts|mov|m4v|webm)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip() or "video"

def human_readable_size(size_bytes: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def parse_ytdlp_command(cmd_str: str) -> Tuple[Optional[str], List[str]]:
    cleaned = cmd_str.replace("\\\n", " ").replace("\\\r\n", " ").strip()
    try:
        tokens = shlex.split(cleaned)
    except Exception:
        tokens = cleaned.split()

    headers: List[str] = []
    url: Optional[str] = None

    skip_arg_flags = {
        "-o", "--output", "-f", "--format",
        "--downloader", "--downloader-args", "--external-downloader",
        "--external-downloader-args", "--paths", "-P",
        "-N", "--concurrent-fragments", "--batch-file", "-a",
        "--limit-rate", "-r", "--retries", "--fragment-retries"
    }

    ignore_flags = {
        "--allow-u", "--allow-unplayable-formats",
        "--no-part", "--restrict-filenames",
        "-F", "--list-formats", "-v", "--verbose",
        "--no-warnings", "--no-check-certificates",
        "-k", "--keep-video"
    }

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok in ignore_flags:
            i += 1
            continue

        if tok in skip_arg_flags:
            i += 2
            continue

        if "=" in tok and (
            tok.startswith("--add-header=")
            or tok.startswith("-H=")
            or tok.startswith("--header=")
        ):
            val = tok.split("=", 1)[1].strip("\"'")
            headers.append(val)
            i += 1
            continue

        if "=" in tok and (
            tok.startswith("--referer=") or tok.startswith("--referrer=")
        ):
            val = tok.split("=", 1)[1].strip("\"'")
            headers.append(f"Referer: {val}")
            i += 1
            continue

        if "=" in tok and tok.startswith("--user-agent="):
            val = tok.split("=", 1)[1].strip("\"'")
            headers.append(f"User-Agent: {val}")
            i += 1
            continue

        if tok in ("--add-header", "-H", "--header"):
            if i + 1 < len(tokens):
                headers.append(tokens[i + 1].strip("\"'"))
                i += 2
                continue

        if tok in ("--referer", "--referrer"):
            if i + 1 < len(tokens):
                clean_ref = tokens[i + 1].strip("\"'")
                headers.append(f"Referer: {clean_ref}")
                i += 2
                continue

        if tok == "--user-agent":
            if i + 1 < len(tokens):
                clean_ua = tokens[i + 1].strip("\"'")
                headers.append(f"User-Agent: {clean_ua}")
                i += 2
                continue

        if tok.startswith("http://") or tok.startswith("https://"):
            url = tok.strip("\"'")
        elif (
            not tok.startswith("-")
            and tok.lower() not in ("yt-dlp", "yt-dlp.exe")
            and ("m3u8" in tok or "mpd" in tok or "://" in tok)
        ):
            url = tok.strip("\"'")

        i += 1

    if not url:
        for tok in reversed(tokens):
            clean_tok = tok.strip("\"'")
            if clean_tok.startswith("http://") or clean_tok.startswith("https://"):
                url = clean_tok
                break

    return url, headers

SKIP_KEY_WORDS = {
    "/skip", "skip", "none", "no", "n/a", "na",
    "no key", "nokey", "pass", "nil", "null", "false",
    "0", "-", "cancel", "not encrypted", "dont know"
}

def is_valid_hex_or_b64(val: str) -> bool:
    val = val.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", val):
        return True
    if re.fullmatch(r"[A-Za-z0-9+/]{22}(?:==)?", val):
        return True
    return False

def parse_and_validate_keys(raw_text: str) -> Tuple[Optional[List[str]], Optional[str]]:
    text = raw_text.strip()
    if not text or text.lower() in SKIP_KEY_WORDS:
        return [], None

    text = re.sub(r"--key[=\s]*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"-k[=\s]*", " ", text, flags=re.IGNORECASE)

    tokens = re.split(r"[\s,]+", text.strip())
    keys: List[str] = []

    for tok in tokens:
        clean = tok.strip("\"'")
        if not clean or clean.lower() in SKIP_KEY_WORDS:
            continue

        if ":" in clean:
            parts = clean.split(":", 1)
            raw_kid = parts[0].strip()
            raw_key = parts[1].strip()
            kid = raw_kid.replace("-", "")
            key = raw_key.replace("-", "")

            if not is_valid_hex_or_b64(kid):
                return None, f"Invalid KID: `{raw_kid}`. Must be 32-character HEX or 16-byte Base64."
            if not is_valid_hex_or_b64(key):
                return None, f"Invalid Key: `{raw_key}`. Must be 32-character HEX or 16-byte Base64."

            keys.append(f"{kid}:{key}")
        else:
            raw_key = clean.strip()
            key = raw_key.replace("-", "")
            if not is_valid_hex_or_b64(key):
                return None, f"Invalid Key: `{raw_key}`. Must be 32-character HEX or 16-byte Base64 (or `KID:KEY`)."
            keys.append(key)

    return keys, None

def headers_to_dict(headers: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for h in headers:
        if ":" in h:
            k, v = h.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def clean_media_url(raw: str) -> str:
    raw = html.unescape(raw.strip())
    parts = urlsplit(raw)
    path = re.sub(r"/{2,}", "/", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _fetch_bytes(url: str, headers: Dict[str, str]) -> bytes:
    hdrs = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


async def fetch_mpd(url: str, headers: List[str]) -> bytes:
    return await asyncio.to_thread(_fetch_bytes, url, headers_to_dict(headers))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_mpd_media(mpd_bytes: bytes, mpd_url: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(mpd_bytes)
    except ET.ParseError as e:
        raise ValueError(f"Response is not a valid MPD/XML file ({e})")

    if _local(root.tag) != "MPD":
        raise ValueError("Response is not an MPD manifest.")

    parent = {child: par for par in root.iter() for child in par}

    def own_base(el) -> Optional[str]:
        for c in el:
            if _local(c.tag) == "BaseURL" and c.text and c.text.strip():
                return c.text.strip()
        return None

    def resolve(el) -> str:
        chain = []
        node = parent.get(el)
        while node is not None:
            chain.append(node)
            node = parent.get(node)
        base = mpd_url
        for anc in reversed(chain):
            b = own_base(anc)
            if b:
                base = urljoin(base, html.unescape(b))
        return urljoin(base, html.unescape(el.text.strip()))

    media: List[Dict[str, Any]] = []
    seen = set()

    for el in root.iter():
        if _local(el.tag) != "BaseURL" or not el.text or not el.text.strip():
            continue

        final = clean_media_url(resolve(el))
        path = urlsplit(final).path
        ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
        if ext not in ("mp4", "m4a", "m4v") or final in seen:
            continue
        seen.add(final)

        attrs: Dict[str, str] = {}
        node = parent.get(el)
        while node is not None:
            for k, v in node.attrib.items():
                attrs.setdefault(k, v)
            node = parent.get(node)

        mime = (attrs.get("mimeType", "") + attrs.get("contentType", "")).lower()
        kind = "audio" if (ext == "m4a" or "audio" in mime) else "video"

        name = path.rsplit("/", 1)[-1]
        height = int(attrs["height"]) if attrs.get("height", "").isdigit() else 0
        if not height:
            m = re.search(r"(\d{3,4})p", name)
            height = int(m.group(1)) if m else 0
        bandwidth = int(attrs["bandwidth"]) if attrs.get("bandwidth", "").isdigit() else 0

        if height:
            label = f"{height}p"
        elif bandwidth:
            label = f"{bandwidth // 1000} kbps"
        else:
            label = name

        media.append({
            "url": final,
            "kind": kind,
            "label": label,
            "height": height,
            "bandwidth": bandwidth,
            "name": name,
        })

    return media

def tail_text(text: str, limit: int = 800) -> str:
    text = text.strip()
    return text[-limit:] if len(text) > limit else text


async def run_cmd(cmd: List[str]) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")


async def ytdlp_download(url: str, out_path: str) -> Tuple[bool, str]:
    cmd = [
        YTDLP_PATH,
        "--no-playlist",
        "--no-part",
        "--no-warnings",
        "--newline",
        "--retries", "10",
        "--fragment-retries", "10",
        "--user-agent", BROWSER_UA,
        "-o", out_path,
        url,
    ]
    code, output = await run_cmd(cmd)
    if code == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return True, output

    stem = os.path.splitext(os.path.basename(out_path))[0]
    folder = os.path.dirname(out_path)
    if code == 0:
        for f in os.listdir(folder):
            fp = os.path.join(folder, f)
            if f.startswith(stem + ".") and os.path.isfile(fp) and os.path.getsize(fp) > 0:
                os.replace(fp, out_path)
                return True, output

    return False, output


def key_to_hex(val: str) -> str:
    val = val.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", val):
        return val.lower()
    padded = val + "=" * ((4 - len(val) % 4) % 4)
    return base64.b64decode(padded).hex()


def build_mp4decrypt_keys(keys: List[str]) -> List[str]:
    result: List[str] = []
    for k in keys:
        if ":" in k:
            kid, key = k.split(":", 1)
            result.append(f"{key_to_hex(kid)}:{key_to_hex(key)}")
        else:
            result.append(f"1:{key_to_hex(k)}")
    return result


async def mp4decrypt_file(src: str, dst: str, keys: List[str]) -> Tuple[bool, str]:
    cmd = [MP4DECRYPT_PATH]
    for k in build_mp4decrypt_keys(keys):
        cmd.extend(["--key", k])
    cmd.extend([src, dst])
    code, output = await run_cmd(cmd)
    ok = code == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0
    return ok, output


async def ffmpeg_merge(video: str, audio: Optional[str], output: str) -> Tuple[bool, str]:
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", video]
    if audio:
        cmd.extend(["-i", audio])
    cmd.extend(["-map", "0:v:0"])
    if audio:
        cmd.extend(["-map", "1:a:0"])
    cmd.extend(["-c", "copy", "-movflags", "+faststart", output])
    code, out = await run_cmd(cmd)
    ok = code == 0 and os.path.isfile(output) and os.path.getsize(output) > 0
    return ok, out


def missing_tools() -> List[str]:
    tools = (("yt-dlp", YTDLP_PATH), ("mp4decrypt", MP4DECRYPT_PATH), ("ffmpeg", "ffmpeg"), ("ffprobe", "ffprobe"))
    return [n for n, p in tools if not (shutil.which(p) or (os.path.isfile(p) and os.access(p, os.X_OK)))]


async def extract_duration(video_path: str) -> int:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        val = stdout.decode().strip()
        if val:
            return int(round(float(val)))
    except Exception:
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        val = stdout.decode().strip()
        if val:
            return int(round(float(val)))
    except Exception:
        pass

    return 0

async def generate_thumbnail(
    video_path: str, duration: int, output_path: str
) -> Optional[str]:
    seek_time = 5 if duration >= 10 else max(1, duration // 2)

    cmd = [
        "ffmpeg",
        "-ss", str(seek_time),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-q:v", "2",
        "-y",
        output_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception:
        pass

    fallback_cmd = [
        "ffmpeg",
        "-ss", "00:00:01",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        "-y",
        output_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *fallback_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception:
        pass

    return None

async def upload_progress_callback(
    current: int,
    total: int,
    status_msg: Message,
    start_time: float,
    progress_state: List[float],
):
    now = time.time()
    percentage = (current * 100 / total) if total else 0
    current_percent = int(percentage)

    if current != total:
        time_elapsed = now - progress_state[0]
        percent_diff = abs(current_percent - progress_state[1])
        if time_elapsed < 8.0 or percent_diff < 10:
            return

    progress_state[0] = now
    progress_state[1] = current_percent

    elapsed = now - start_time
    speed = (current / elapsed) if elapsed > 0 else 0
    eta = int((total - current) / speed) if speed > 0 else 0

    bar_len = 10
    filled = int(percentage / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)

    text = (
        f"📤 **Uploading Video to Telegram...**\n\n"
        f"**Progress:** `[{bar}]` {percentage:.1f}%\n"
        f"**Uploaded:** `{human_readable_size(current)}` of `{human_readable_size(total)}`\n"
        f"**Speed:** `{human_readable_size(speed)}/s`\n"
        f"**ETA:** `{eta}s`"
    )
    try:
        await status_msg.edit_text(text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except MessageNotModified:
        pass
    except Exception:
        pass

if not BOT_TOKEN or not API_ID or not API_HASH:
    print("\n[!] Please set API_ID, API_HASH, and BOT_TOKEN in .env or bot.py\n")
    sys.exit(1)

app = Client(
    name="m3u8_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=DATA_DIR,
)

@app.on_message(filters.command("auth") & admin_filter)
async def handle_auth_command(client: Client, message: Message):
    args = message.text.split()[1:]

    if not args and message.reply_to_message and message.reply_to_message.from_user:
        args = [str(message.reply_to_message.from_user.id)]

    if not args:
        await message.reply_text(
            "ℹ️ **Usage:**\n"
            "• `/auth <telegram_id>` (e.g. `/auth 123456789`)\n"
            "• Or reply to any user's message with `/auth`"
        )
        return

    added = []
    already = []
    invalid = []

    for item in args:
        clean = item.strip()
        if not clean.isdigit():
            invalid.append(clean)
            continue
        uid = int(clean)
        if uid in AUTH_USERS_SET:
            already.append(str(uid))
        else:
            AUTH_USERS_SET.add(uid)
            added.append(str(uid))

    if added:
        save_auth_users(AUTH_USERS_SET)

    response = "🔐 **Authorization Update:**\n\n"
    if added:
        response += f"✅ **Added:** {', '.join(added)}\n"
    if already:
        response += f"ℹ️ **Already authorized:** {', '.join(already)}\n"
    if invalid:
        response += f"⚠️ **Invalid IDs:** {', '.join(invalid)}\n"

    await message.reply_text(response)

@app.on_message(filters.command("unauth") & admin_filter)
async def handle_unauth_command(client: Client, message: Message):
    """Admin command to revoke authorization for a user."""
    args = message.text.split()[1:]

    if not args and message.reply_to_message and message.reply_to_message.from_user:
        args = [str(message.reply_to_message.from_user.id)]

    if not args:
        await message.reply_text("ℹ️ **Usage:** `/unauth <telegram_id>`")
        return

    removed = []
    not_found = []

    for item in args:
        clean = item.strip()
        if not clean.isdigit():
            continue
        uid = int(clean)
        if uid in ADMIN_IDS:
            await message.reply_text(f"⚠️ Cannot unauthorize administrator `{uid}`.")
            continue
        if uid in AUTH_USERS_SET:
            AUTH_USERS_SET.remove(uid)
            removed.append(str(uid))
        else:
            not_found.append(str(uid))

    if removed:
        save_auth_users(AUTH_USERS_SET)

    response = "🔓 **Authorization Revoked:**\n\n"
    if removed:
        response += f"🚫 **Removed:** {', '.join(removed)}\n"
    if not_found:
        response += f"ℹ️ **Not found in whitelist:** {', '.join(not_found)}\n"

    await message.reply_text(response)

@app.on_message(filters.command(["authlist", "users"]) & admin_filter)
async def handle_authlist_command(_, message: Message):
    admins_str = ", ".join([f"`{a}`" for a in ADMIN_IDS]) or "_None configured_"
    users_str = ", ".join([f"`{u}`" for u in sorted(AUTH_USERS_SET)]) or "_No authorized users_"

    text = (
        f"📋 **Authorized Users List:**\n\n"
        f"👑 **Admins ({len(ADMIN_IDS)}):**\n{admins_str}\n\n"
        f"👥 **Total Authorized ({len(AUTH_USERS_SET)}):**\n{users_str}"
    )
    await message.reply_text(text)

@app.on_message(unauth_filter)
async def handle_unauthorized(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else "Unknown"
    await message.reply_text(
        f"⛔ **Access Denied**\n\n"
        f"You are not authorized to use this bot.\n"
        f"Your Telegram User ID: `{user_id}`\n\n"
        f"Please ask an administrator to authorize you using:\n"
        f"`/auth {user_id}`"
    )

@app.on_message(filters.command(["start", "help"]) & auth_filter)
async def handle_start(client: Client, message: Message):
    user_id = message.from_user.id
    USER_STATES.pop(user_id, None)

    role_badge = "👑 **Admin**" if is_admin(user_id) else "✅ **Authorized User**"

    help_text = (
        f"👋 **Welcome to MPD Stream Downloader Bot!**\n\n"
        f"Status: {role_badge} (ID: `{user_id}`)\n\n"
        f"**Download Workflow:**\n"
        f"1. Send `/download` to start.\n"
        f"2. Send your `yt-dlp` command (with the `.mpd` URL).\n"
        f"3. Pick a quality (if several are found).\n"
        f"4. Send Decryption Key(s) (or `/skip`).\n"
        f"5. Send desired video name.\n"
        f"6. The bot downloads video "
        f"uploadsto Telegram.\n\n"
        f"**Commands:**\n"
        f"• `/download` - Start downloading a stream\n"
        f"• `/cancel` - Abort active operation"
    )

    if is_admin(user_id):
        help_text += (
            f"\n\n**Admin Commands:**\n"
            f"• `/auth <id>` - Authorize user\n"
            f"• `/unauth <id>` - Revoke user authorization\n"
            f"• `/authlist` - List authorized users"
        )

    await message.reply_text(help_text)

@app.on_message(filters.command("cancel") & auth_filter)
async def handle_cancel(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in USER_STATES:
        USER_STATES.pop(user_id, None)
        await message.reply_text("❌ Operation has been cancelled.")
    else:
        await message.reply_text("ℹ️ No active operation to cancel.")

KEY_PROMPT = (
    "🔑 **Step 3/4: Decryption Key(s)**\n\n"
    "Enter the decryption key(s) `.\n"
    "• Format: `KID:KEY` (32 hex chars each)\n"
    "• Multiple keys: separate with newlines or spaces.\n\n"
    "👉 **If no key is needed, reply with `/skip` or `none`.**"
)


@app.on_message(filters.command("download") & auth_filter)
async def handle_download_command(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "PROCESSING":
        await message.reply_text(
            "⚠️ You already have an active download in progress. Please wait for it to finish or use `/cancel`."
        )
        return

    missing = missing_tools()
    if missing:
        await message.reply_text(
            f"❌ **Error:** missing required tool(s): `{', '.join(missing)}`.\n\n"
            f"Install them or set `YTDLP_PATH` / `MP4DECRYPT_PATH`."
        )
        return

    USER_STATES[user_id] = {"step": "AWAIT_COMMAND"}
    await message.reply_text(
        "📥 **Step 1/4: Enter yt-dlp command**\n\n"
        "Send your `yt-dlp` command (or just the URL) containing the `.mpd` manifest URL.\n\n"
        "**Example:**\n"
        "`yt-dlp --add-header \"Referer: https://site.com/\" \"https://site.com/manifest.mpd\"`\n\n"
        "_Send /cancel anytime to abort._"
    )

@app.on_message(filters.text & ~filters.command(["start", "help", "download", "cancel", "auth", "unauth", "authlist", "users"]) & auth_filter)
async def handle_user_input(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id not in USER_STATES:
        return

    current_state = USER_STATES[user_id]
    step = current_state.get("step")

    if step == "AWAIT_COMMAND":
        raw_cmd = message.text.strip()
        url, headers = parse_ytdlp_command(raw_cmd)

        if not url:
            await message.reply_text(
                "❌ **Could not detect a valid URL from your command!**\n\n"
                "Please ensure it contains a valid `http://` or `https://` MPD URL.\n"
                "Send the command again or send `/cancel` to abort."
            )
            return

        status = await message.reply_text("🔎 **Downloading MPD manifest...**")

        try:
            mpd_bytes = await fetch_mpd(url, headers)
            media = parse_mpd_media(mpd_bytes, url)
        except Exception as e:
            await status.edit_text(
                f"❌ **Failed to fetch or parse the MPD:**\n`{str(e)[:300]}`\n\n"
                f"Send another command or `/cancel`."
            )
            return

        videos = [m for m in media if m["kind"] == "video"]
        audios = [m for m in media if m["kind"] == "audio"]

        if not videos:
            await status.edit_text(
                "❌ **No `.mp4` video URLs found in the MPD `<BaseURL>` tags.**\n\n"
                "_(Manifests that only use SegmentTemplate/SegmentList are not supported.)_\n"
                "Send another command or `/cancel`."
            )
            return

        videos.sort(key=lambda m: (m["height"], m["bandwidth"]), reverse=True)
        audio = max(audios, key=lambda m: m["bandwidth"]) if audios else None
        audio_note = f"🔊 **Audio:** `{audio['name']}`" if audio else "⚠️ **No `.m4a` audio found — video only.**"

        base_state = {"url": url, "headers": headers, "raw_cmd": raw_cmd, "audio": audio}

        if len(videos) == 1:
            USER_STATES[user_id] = {**base_state, "step": "AWAIT_KEYS", "video": videos[0]}
            await status.edit_text(
                f"✅ **MPD parsed.** Only one quality found: `{videos[0]['label']}` (auto-selected)\n"
                f"🎞 `{videos[0]['name']}`\n{audio_note}"
            )
            await message.reply_text(KEY_PROMPT)
            return

        USER_STATES[user_id] = {**base_state, "step": "AWAIT_QUALITY", "videos": videos}

        buttons: List[List[InlineKeyboardButton]] = []
        row: List[InlineKeyboardButton] = []
        for i, v in enumerate(videos):
            text = f"🎞 {v['label']}"
            if v["bandwidth"]:
                text += f" • {v['bandwidth'] // 1000} kbps"
            row.append(InlineKeyboardButton(text, callback_data=f"q|{user_id}|{i}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"q|{user_id}|cancel")])

        await status.edit_text(
            f"✅ **MPD parsed — {len(videos)} video qualities found.**\n"
            f"{audio_note}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎚 **Step 2/4: Select quality**",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    elif step == "AWAIT_QUALITY":
        await message.reply_text("👆 Please choose a quality using the buttons above, or send `/cancel`.")
        return

    elif step == "AWAIT_KEYS":
        raw_keys = message.text.strip()
        keys, key_err = parse_and_validate_keys(raw_keys)

        if key_err:
            await message.reply_text(
                f"❌ **Decryption Key Error:**\n{key_err}\n\n"
                f"**Accepted Formats:**\n"
                f"• `KID:KEY` (each 32 hex chars)\n"
                f"• `KEY` (single 32 hex chars key)\n"
                f"• Multiple keys on separate lines or space-separated\n\n"
                f"👉 _If the stream is not encrypted, reply with `/skip` or `none`._"
            )
            return

        USER_STATES[user_id] = {**current_state, "step": "AWAIT_NAME", "keys": keys}

        if keys:
            key_summary = f"✅ **Recorded {len(keys)} decryption key(s):**\n" + "\n".join([f"• `{k}`" for k in keys])
        else:
            key_summary = "ℹ️ **No decryption keys specified (skipped).**"

        await message.reply_text(
            f"{key_summary}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ **Step 4/4: Enter Video Name**\n\n"
            f"Enter the desired name for the final file (without extension, e.g. `Physics_Class_01`):"
        )

        return

    elif step == "AWAIT_NAME":
        raw_name = message.text.strip()
        video_name = sanitize_filename(raw_name)
        video_item = current_state["video"]
        audio_item = current_state.get("audio")
        keys = current_state.get("keys", [])

        USER_STATES[user_id] = {"step": "PROCESSING"}

        status_msg = await message.reply_text(
            f"🚀 **Starting Process for:** `{video_name}`\n\n"
            f"🎞 **Quality:** `{video_item['label']}`\n"
            f"📥 Downloading video & audio with yt-dlp..."
        )

        temp_work_dir = tempfile.mkdtemp(prefix="tg_mpd_")
        out_dir = os.path.join(temp_work_dir, "out")
        os.makedirs(out_dir, exist_ok=True)

        enc_video = os.path.join(temp_work_dir, "video_enc.mp4")
        enc_audio = os.path.join(temp_work_dir, "audio_enc.m4a")
        dec_video = os.path.join(temp_work_dir, "video_dec.mp4")
        dec_audio = os.path.join(temp_work_dir, "audio_dec.m4a")
        video_file = os.path.join(out_dir, f"{video_name}.mp4")

        try:
            jobs = [ytdlp_download(video_item["url"], enc_video)]
            if audio_item:
                jobs.append(ytdlp_download(audio_item["url"], enc_audio))
            results = await asyncio.gather(*jobs)

            labels = ["video"] + (["audio"] if audio_item else [])
            for lbl, (ok, output) in zip(labels, results):
                if not ok:
                    await status_msg.edit_text(
                        f"❌ **yt-dlp {lbl} download failed**\n\n"
                        f"```{tail_text(output)}```"
                    )
                    return

            if keys:
                await status_msg.edit_text(
                    f"🔓 **Decrypting...**\n\n"
                    f"🏷️ **Name:** `{video_name}`\n🔑 **Keys:** `{len(keys)}`"
                )
                dec_jobs = [mp4decrypt_file(enc_video, dec_video, keys)]
                if audio_item:
                    dec_jobs.append(mp4decrypt_file(enc_audio, dec_audio, keys))
                dec_results = await asyncio.gather(*dec_jobs)

                for lbl, (ok, output) in zip(labels, dec_results):
                    if not ok:
                        await status_msg.edit_text(
                            f"❌ **mp4decrypt failed on {lbl}**\n\n"
                            f"```{tail_text(output)}```\n"
                            f"💡 _Check that the key matches this stream._"
                        )
                        return
                merge_video = dec_video
                merge_audio = dec_audio if audio_item else None
            else:
                merge_video = enc_video
                merge_audio = enc_audio if audio_item else None

            await status_msg.edit_text(
                f"🔧 **Merging video & audio ...**\n\n🏷️ **Name:** `{video_name}`"
            )
            ok, output = await ffmpeg_merge(merge_video, merge_audio, video_file)
            if not ok:
                await status_msg.edit_text(f"❌ **ffmpeg merge failed**\n\n```{tail_text(output)}```")
                return

            file_size = os.path.getsize(video_file)
            file_size_readable = human_readable_size(file_size)

            MAX_BYTES = 2000 * 1024 * 1024
            if file_size > MAX_BYTES:
                await status_msg.edit_text(
                    f"⚠️ **File size exceeds Telegram limit!**\n\n"
                    f"Size: `{file_size_readable}`\n"
                    f"Telegram bots can upload files up to 2GB."
                )
                return

            await status_msg.edit_text(
                f"🎬 **Processing Media...**\n\n"
                f"• Extracting duration via FFprobe...\n"
                f"• Generating 1920x1080 thumbnail via FFmpeg...\n"
                f"• Size: `{file_size_readable}`"
            )

            duration = await extract_duration(video_file)
            thumb_path = os.path.join(temp_work_dir, "thumb_1080p.jpg")
            thumbnail = await generate_thumbnail(video_file, duration, thumb_path)

            upload_start = time.time()
            progress_state = [0.0, 0.0]

            await status_msg.edit_text(
                f"📤 **Uploading video to Telegram...**\n\n"
                f"📐 Target Resolution: `1920x1080`\n"
                f"⏱️ Duration: `{duration}s`\n"
                f"💾 Size: `{file_size_readable}`"
            )

            caption = (
                f"🎬 **{video_name}**\n\n"
                f"📐 **Resolution:** `1920x1080`\n"
                f"⏱️ **Duration:** `{duration}s`\n"
                f"💾 **Size:** `{file_size_readable}`"
            )

            sent_video = await client.send_video(
                chat_id=message.chat.id,
                video=video_file,
                caption=caption,
                duration=duration,
                thumb=thumbnail,
                width=1920,
                height=1080,
                supports_streaming=True,
                progress=upload_progress_callback,
                progress_args=(status_msg, upload_start, progress_state),
            )

            await status_msg.edit_text("✅ **Video downloaded and uploaded successfully!**")

        except Exception as e:
            await status_msg.edit_text(f"❌ **An unexpected error occurred:**\n`{str(e)}`")

        finally:
            USER_STATES.pop(user_id, None)
            try:
                shutil.rmtree(temp_work_dir, ignore_errors=True)
            except Exception:
                pass


@app.on_callback_query(filters.regex(r"^q\|"))
async def handle_quality_callback(client: Client, cq: CallbackQuery):
    user_id = cq.from_user.id

    if not is_authorized(user_id):
        await cq.answer("⛔ You are not authorized.", show_alert=True)
        return

    try:
        _, owner, choice = cq.data.split("|")
    except ValueError:
        await cq.answer("Invalid selection.", show_alert=True)
        return

    if int(owner) != user_id:
        await cq.answer("This menu belongs to another user.", show_alert=True)
        return

    state = USER_STATES.get(user_id)
    if not state or state.get("step") != "AWAIT_QUALITY":
        await cq.answer("This selection has expired.", show_alert=True)
        return

    if choice == "cancel":
        USER_STATES.pop(user_id, None)
        await cq.answer("Cancelled")
        await cq.message.edit_text("❌ Operation has been cancelled.")
        return

    videos = state["videos"]
    if not choice.isdigit() or int(choice) >= len(videos):
        await cq.answer("Invalid quality.", show_alert=True)
        return

    selected = videos[int(choice)]
    USER_STATES[user_id] = {
        "step": "AWAIT_KEYS",
        "url": state["url"],
        "headers": state["headers"],
        "raw_cmd": state["raw_cmd"],
        "audio": state["audio"],
        "video": selected,
    }

    await cq.answer(f"Selected {selected['label']}")
    await cq.message.edit_text(
        f"✅ **Quality selected:** `{selected['label']}`\n🎞 `{selected['name']}`",
        reply_markup=None,
    )
    await cq.message.reply_text(KEY_PROMPT)


if __name__ == "__main__":
    for tool in missing_tools():
        print(f"WARNING: '{tool}' was not found. Downloads will fail until it is installed.")

    print(
        "\n"
        "=====================================================\n"
        "🤖 Telegram MPD Downloader Bot is starting...\n"
        f"• Authorized Users Count: {len(AUTH_USERS_SET)}\n"
        f"• yt-dlp: {YTDLP_PATH} | mp4decrypt: {MP4DECRYPT_PATH}\n"
        "=====================================================\n"
    )

    app.run()
