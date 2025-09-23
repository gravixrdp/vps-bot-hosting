#!/usr/bin/env python3
# HostBot Pro — Multi‑App hosting + Smart auto‑detect (AST+regex, aiogram v2/v3 aware)
# Dynamic base image for aiogram v2, build-essential when needed
# Linux shell + Premium gate + Admin tools + Polished UI with Inline Copy (with fallback)

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import ast
from typing import Dict, Any, List, Tuple

import docker
from docker.errors import APIError, NotFound
from telegram import (
    Update, InputFile, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    CallbackQueryHandler, ContextTypes, Defaults, filters
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("hostbot")

# =========================
# Config
# =========================
BOT_TOKEN = "8318430595:AAFtbJVxIbHIQxtmNwZPgXx68wnhVJuDuhk"  # <-- replace

OWNERS = {5610858626}  # Owner IDs

ALLOW_FILE = os.getenv("ALLOW_FILE", "allowlist.json")
PREMIUM_FILE = os.getenv("PREMIUM_FILE", "premium.json")
USERS_FILE = os.getenv("USERS_FILE", "users.json")

DEFAULT_CPUS = float(os.getenv("DEFAULT_CPUS", "0.5"))
DEFAULT_MEM = os.getenv("DEFAULT_MEM", "512m")
DEFAULT_PIDS = int(os.getenv("DEFAULT_PIDS", "256"))
DEFAULT_RESTART = os.getenv("DEFAULT_RESTART", "unless-stopped")

BASE_IMAGE_DEFAULT = "python:3.12-slim"  # may switch to 3.11 for aiogram v2

SHELL_IMAGE = os.getenv("SHELL_IMAGE", "ubuntu:24.04")
SHELL_CPUS = float(os.getenv("SHELL_CPUS", "0.25"))
SHELL_MEM = os.getenv("SHELL_MEM", "256m")
SHELL_PIDS = int(os.getenv("SHELL_PIDS", "128"))
SHELL_IDLE_SECS = int(os.getenv("SHELL_IDLE_SECS", "1200"))
SHELL_EXEC_TIMEOUT = int(os.getenv("SHELL_EXEC_TIMEOUT", "15"))

UNAUTH_MSG = (
    "⚠️ Premium Access Only\n"
    "This bot is restricted to approved users.\n"
    "Tap My ID to copy your numeric ID and share it with the owner."
)

HELP_TEXT = (
    "🚀 Welcome to the Pro Hosting Panel\n"
    "• Upload a single-file bot.py to host 24/7 (auto‑detects requirements)\n"
    "• Use Apps to manage deployments, view logs, stop, or remove\n"
    "• Open a Linux shell for quick tasks\n\n"
    "Commands\n"
    "/host_py • /apps • /app_logs <id> • /app_stop <id> • /app_rm <id>\n"
    "/premium_status • /shell_start • /sh <cmd> • /shell_end • /help • /contact\n\n"
    "Admin\n"
    "/allow_add • /allow_remove • /allowed • /set_premium • /upgrade"
)

# In‑memory
APPS: Dict[str, Dict[str, Any]] = {}
USER_APPS: Dict[int, List[str]] = {}
SHELLS: Dict[int, Dict[str, Any]] = {}

DOCKER: docker.DockerClient = docker.from_env()

# States
(HOST_WAIT_FILE, HOST_WAIT_REQS, HOST_WAIT_ENVS, HOST_CONFIRM) = range(4)
(ALLOW_WAIT_ID_ADD, ALLOW_WAIT_ID_REMOVE) = range(200, 202)
(PREM_WAIT_ID, PREM_WAIT_DAYS) = range(210, 212)

# ---------- Persistence ----------
def load_json(path: str, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning("load %s error: %s", path, e)
        return default

def save_json(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning("save %s error: %s", path, e)

def load_allowlist() -> set[int]:
    s = set(OWNERS)
    d = load_json(ALLOW_FILE, {"allow": []})
    for x in d.get("allow", []):
        if isinstance(x, int):
            s.add(x)
    return s

def save_allowlist(s: set[int]) -> None:
    save_json(ALLOW_FILE, {"allow": sorted(list(s))})

ALLOW = load_allowlist()

def load_premium_data() -> dict:
    return load_json(PREMIUM_FILE, {"premium": {}})

def save_premium_data(d: dict) -> None:
    save_json(PREMIUM_FILE, d)

PREMIUM = load_premium_data()  # {"premium": {str(user_id): expires_ts}}

USERS = load_json(USERS_FILE, {"users": {}})

def save_users():
    save_json(USERS_FILE, USERS)

def record_user(update: Update):
    u = update.effective_user
    if not u:
        return
    USERS["users"][str(u.id)] = {
        "name": getattr(u, "full_name", None) or f"{u.first_name or ''} {u.last_name or ''}".strip() or u.first_name or "-",
        "username": u.username or ""
    }
    save_users()

# ---------- Access ----------
def is_owner(uid: int) -> bool:
    return uid in OWNERS

def is_premium(uid: int) -> bool:
    exp = float(PREMIUM["premium"].get(str(uid), 0))
    return exp > time.time()

def is_allowed(uid: int) -> bool:
    return is_owner(uid) or uid in ALLOW or is_premium(uid)

# ---------- Keyboards ----------
def make_home_keyboard(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("🚀 Host Python"), KeyboardButton("📦 Apps"), KeyboardButton("📜 Logs")],
        [KeyboardButton("🖥️ Shell Start"), KeyboardButton("🛑 Shell End"), KeyboardButton("🆘 Help")],
        [KeyboardButton("⭐ Premium Status"), KeyboardButton("👤 Contact Admin")]
    ]
    if is_owner(uid):
        rows.append([KeyboardButton("🛠️ Admin Panel")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

def make_shell_keyboard(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("⚡ Run Common Cmds"), KeyboardButton("📘 All Linux Commands")],
        [KeyboardButton("🛑 Shell End"), KeyboardButton("🆘 Help"), KeyboardButton("⭐ Premium Status")]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

def make_unauth_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("📇 My ID")]], resize_keyboard=True, is_persistent=True)

# ---------- Utils ----------
def _safe_nano_cpus(cpus: float) -> int:
    try:
        return max(100_000_000, int(cpus * 1_000_000_000))
    except Exception:
        return 500_000_000

def expiry_str(ts: float) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))

# ---------- Smart auto‑detect (AST + regex + aiogram v2/v3 aware) ----------
STD_LIB = {
    "os","sys","re","json","time","asyncio","typing","pathlib","subprocess","base64",
    "random","logging","datetime","math","itertools","functools","hashlib","hmac","uuid",
    "argparse","shutil","tempfile","gzip","zipfile","io","enum","dataclasses","collections",
    "threading","concurrent","http","urllib","email","statistics","decimal","fractions",
    "traceback","inspect","pprint","socket","selectors","signal","platform"
}

PIP_MAP = {
    "PIL":"Pillow","yaml":"PyYAML","bs4":"beautifulsoup4","cv2":"opencv-python",
    "sklearn":"scikit-learn","Crypto":"pycryptodome","dotenv":"python-dotenv",
    "telebot":"pyTelegramBotAPI","telegram":"python-telegram-bot","pyrogram":"pyrogram",
    "aiogram":"aiogram","requests":"requests","aiohttp":"aiohttp","httpx":"httpx",
    "pydantic":"pydantic","numpy":"numpy","pandas":"pandas","fastapi":"fastapi","starlette":"starlette",
    "pycountry":"pycountry","tenacity":"tenacity","telethon":"telethon"
}

REGEX_HINTS = {
    r"\baiogram\.contrib\b": "aiogram==2.25.1",
    r"\baiogram\.fsm\.storage\b": "aiogram",
    r"\bpyrogram\b": "pyrogram",
    r"\btelethon\b": "telethon",
    r"\brequests\b": "requests",
    r"\bbs4\b|\bBeautifulSoup\b": "beautifulsoup4",
    r"\bpycountry\b": "pycountry",
    r"\bhttpx\b": "httpx",
    r"\btenacity\b": "tenacity",
    r"\bfrom telegram\b|\bimport telegram\b": "python-telegram-bot",
}

def _root_of(name: str) -> str | None:
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name:
        return None
    return name.split(".", 1)

def detect_requirements(py_text: str) -> list[str]:
    ast_found: set[str] = set()
    try:
        tree = ast.parse(py_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in getattr(node, "names", []) or []:
                    root = _root_of(getattr(a, "name", None))
                    if root:
                        ast_found.add(root)
            elif isinstance(node, ast.ImportFrom):
                root = _root_of(getattr(node, "module", None))
                if root:
                    ast_found.add(root)
                for a in getattr(node, "names", []) or []:
                    nroot = _root_of(getattr(a, "name", None))
                    if nroot:
                        ast_found.add(nroot)
    except Exception:
        pass

    reqs = []
    for m in sorted(ast_found):
        if m in STD_LIB:
            continue
        reqs.append(PIP_MAP.get(m, m))

    hinted = []
    for pat, pkg in REGEX_HINTS.items():
        if re.search(pat, py_text, flags=re.IGNORECASE):
            hinted.append(pkg)

    pinned_aiogram = any(h.startswith("aiogram==") for h in hinted)
    out = []
    seen = set()
    for p in reqs + hinted:
        if pinned_aiogram and p.lower() == "aiogram":
            continue
        key = p.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

def choose_base_image(detected_reqs: List[str]) -> str:
    if any(x.startswith("aiogram==2.") for x in detected_reqs):
        return "python:3.11-slim"
    return BASE_IMAGE_DEFAULT

# ---------- Inline builder helpers (Copy support + fallback) ----------
def make_inline_copy(text_to_copy: str) -> InlineKeyboardButton:
    """
    Prefer native inline copy via copy_text parameter; if not supported by installed PTB,
    fall back to a callback that prints the ID for manual copy.
    """
    try:
        # PTB 21.7+ supports copy_text for InlineKeyboardButton
        # https://docs.python-telegram-bot.org/en/v21.9/telegram.inlinekeyboardbutton.html
        return InlineKeyboardButton("📋 Copy", copy_text={"text": text_to_copy})
    except TypeError:
        # Older PTB: fallback to callback
        return InlineKeyboardButton("📋 Copy", callback_data=f"app:copy:{text_to_copy}")

# ---------- Basics ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return
    await update.message.reply_text(
        "✨ Welcome to HostBot Pro\nEffortless 24/7 Python bot hosting, premium UX, and powerful management tools.",
        reply_markup=make_home_keyboard(uid),
        parse_mode=None
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return
    await update.message.reply_text(HELP_TEXT, reply_markup=make_home_keyboard(uid), parse_mode=None)

async def contact_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    kb = make_home_keyboard(uid) if is_allowed(uid) else make_unauth_keyboard()
    await update.message.reply_text("🆘 Contact admin: @Dravonnbot", reply_markup=kb, parse_mode=None)

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    await update.message.reply_text(str(uid), parse_mode=None)

# ---------- Premium ----------
def expiry_str_local(ts: float) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))

async def premium_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    exp = float(PREMIUM["premium"].get(str(uid), 0))
    if exp > time.time():
        await update.message.reply_text(f"⭐ Premium active until: {expiry_str_local(exp)}", reply_markup=make_home_keyboard(uid), parse_mode=None)
    else:
        await update.message.reply_text("⭐ Premium is not active. Use /upgrade to get access.", reply_markup=make_home_keyboard(uid), parse_mode=None)

async def upgrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    kb = make_home_keyboard(uid) if is_allowed(uid) else make_unauth_keyboard()
    await update.message.reply_text("To upgrade to Premium, DM @Dravonnbot.", reply_markup=kb, parse_mode=None)

# ---------- Admin helpers ----------
async def resolve_user_meta(uid: int, context: ContextTypes.DEFAULT_TYPE) -> dict:
    meta = USERS["users"].get(str(uid))
    if not meta or not meta.get("name"):
        try:
            chat = await context.bot.get_chat(uid)
            name = getattr(chat, "full_name", None) or getattr(chat, "first_name", None) or "-"
            username = getattr(chat, "username", "") or ""
            USERS["users"][str(uid)] = {"name": name, "username": username}
            save_users()
            meta = USERS["users"][str(uid)]
        except Exception:
            meta = {"name": "not started", "username": ""}
    return meta

async def allowed_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # works from command or from admin callback
    caller_id = update.effective_user.id if update.effective_user else None
    if caller_id is None or not is_owner(caller_id):
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("Only owners can access admin functions.", reply_markup=make_home_keyboard(caller_id or 0), parse_mode=None)
        return
    users = sorted(list(ALLOW | OWNERS))
    lines = []
    for u in users:
        meta = await resolve_user_meta(u, context)
        name = meta.get("name", "-")
        uname = meta.get("username", "")
        tag = f" — {name}{(' @'+uname) if uname else ''}"
        lines.append(f"{u}{tag}")
    target = update.callback_query.message if update.callback_query else update.message
    await target.reply_text("👥 Allowed Users\n" + "\n".join(lines), reply_markup=make_home_keyboard(caller_id), parse_mode=None)

# ---------- Allowlist conversations ----------
async def allow_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can modify access.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    args = context.args or []
    if args:
        try:
            new_id = int(args)
        except ValueError:
            await update.message.reply_text("Usage: /allow_add <user_id>", reply_markup=make_home_keyboard(uid), parse_mode=None)
            return ConversationHandler.END
        ALLOW.add(new_id); save_allowlist(ALLOW)
        await update.message.reply_text(f"✅ Added: {new_id}", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    await update.message.reply_text("Send the user ID to allow:", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return ALLOW_WAIT_ID_ADD

async def allow_add_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can modify access.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    try:
        new_id = int(txt)
    except ValueError:
        await update.message.reply_text("Please send a numeric user ID.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ALLOW_WAIT_ID_ADD
    ALLOW.add(new_id); save_allowlist(ALLOW)
    await update.message.reply_text(f"✅ Added: {new_id}", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return ConversationHandler.END

async def allow_remove_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can modify access.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    args = context.args or []
    if args:
        try:
            rem = int(args)
        except ValueError:
            await update.message.reply_text("Usage: /allow_remove <user_id>", reply_markup=make_home_keyboard(uid), parse_mode=None)
            return ConversationHandler.END
        if rem in OWNERS:
            await update.message.reply_text("Cannot remove an owner.", reply_markup=make_home_keyboard(uid), parse_mode=None)
            return ConversationHandler.END
        if rem in ALLOW:
            ALLOW.remove(rem); save_allowlist(ALLOW)
            await update.message.reply_text(f"✅ Removed: {rem}", reply_markup=make_home_keyboard(uid), parse_mode=None)
            return ConversationHandler.END
        await update.message.reply_text("User was not in allowlist.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    await update.message.reply_text("Send the user ID to remove:", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return ALLOW_WAIT_ID_REMOVE

async def allow_remove_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can modify access.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    try:
        rem = int(txt)
    except ValueError:
        await update.message.reply_text("Please send a numeric user ID.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ALLOW_WAIT_ID_REMOVE
    if rem in OWNERS:
        await update.message.reply_text("Cannot remove an owner.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    if rem in ALLOW:
        ALLOW.remove(rem); save_allowlist(ALLOW)
        await update.message.reply_text(f"✅ Removed: {rem}", reply_markup=make_home_keyboard(uid), parse_mode=None)
    else:
        await update.message.reply_text("User was not in allowlist.", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return ConversationHandler.END

# ---------- Premium conversations ----------
async def set_premium_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can set premium.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    args = context.args or []
    if len(args) >= 2:
        try:
            target = int(args); days = int(args[4])
        except ValueError:
            await update.message.reply_text("Usage: /set_premium <user_id> <days>", reply_markup=make_home_keyboard(uid), parse_mode=None)
            return ConversationHandler.END
        exp = time.time() + days * 86400
        PREMIUM["premium"][str(target)] = exp; save_premium_data(PREMIUM)
        await update.message.reply_text(f"✅ Premium for {target} until {expiry_str_local(exp)}", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    await update.message.reply_text("Send the target user ID:", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return PREM_WAIT_ID

async def set_premium_collect_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can set premium.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    try:
        target = int(txt)
    except ValueError:
        await update.message.reply_text("Please send a numeric user ID.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return PREM_WAIT_ID
    context.user_data["prem_target"] = target
    await update.message.reply_text("Send premium days (e.g., 30):", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return PREM_WAIT_DAYS

async def set_premium_collect_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("Only owners can set premium.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    txt = (update.message.text or "").strip()
    try:
        days = int(txt)
    except ValueError:
        await update.message.reply_text("Please send days as a number.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return PREM_WAIT_DAYS
    target = context.user_data.get("prem_target")
    if target is None:
        await update.message.reply_text("Session expired. Use /set_premium again.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    exp = time.time() + days * 86400
    PREMIUM["premium"][str(target)] = exp; save_premium_data(PREMIUM)
    await update.message.reply_text(f"✅ Premium for {target} until {expiry_str_local(exp)}", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return ConversationHandler.END

# ---------- Deploy (auto‑detect, multi‑app) ----------
def dockerfile_text(base_image: str, has_requirements: bool) -> str:
    lines = [
        f"FROM {base_image}",
        "WORKDIR /app",
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1",
        "RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*" if has_requirements else "RUN true",
        "COPY bot.py /app/bot.py",
    ]
    if has_requirements:
        lines += [
            "COPY requirements.txt /app/requirements.txt",
            "RUN pip install --no-cache-dir -r /app/requirements.txt"
        ]
    lines.append('CMD ["python","/app/bot.py"]')
    return "\n".join(lines)

async def host_py_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return
    await update.message.reply_text("📥 Please upload your bot.py file (as a document).", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return HOST_WAIT_FILE

async def host_py_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith("bot.py"):
        await update.message.reply_text("Please upload a file named bot.py (single file only).", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return HOST_WAIT_FILE

    tmpdir = tempfile.mkdtemp(prefix=f"pybot_{uid}_")
    tgfile = await doc.get_file()
    dest = os.path.join(tmpdir, "bot.py")
    await tgfile.download_to_drive(dest)
    context.user_data["tmpdir"] = tmpdir

    try:
        with open(dest, "r", encoding="utf-8") as f:
            src = f.read()
    except Exception:
        src = ""
    detected = detect_requirements(src)
    context.user_data["detected_requirements"] = detected
    context.user_data["base_image"] = choose_base_image(detected)

    if detected:
        preview = "🔎 Detected requirements:\n• " + "\n• ".join(detected)
        instr = "\n\nReply yes to accept, or send a custom list (one per line), or '-' to skip."
    else:
        preview = "🔎 No external packages detected."
        instr = "\n\nSend requirements (one per line), or '-' to skip."
    await update.message.reply_text(preview + instr, reply_markup=make_home_keyboard(uid), parse_mode=None)
    return HOST_WAIT_REQS

async def host_py_requirements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    txt = (update.message.text or "").strip()
    tmpdir = context.user_data.get("tmpdir")
    if not tmpdir:
        await update.message.reply_text("Session expired. Use /host_py again.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END

    detected = context.user_data.get("detected_requirements", [])
    to_write: List[str] = []
    has_reqs = False

    if txt.lower() == "yes" and detected:
        to_write = detected
        has_reqs = True
    elif txt == "-":
        has_reqs = False
    else:
        custom = [line.strip() for line in txt.splitlines() if line.strip()]
        if custom:
            to_write = custom
            has_reqs = True
        else:
            has_reqs = False

    context.user_data["has_requirements"] = has_reqs
    if has_reqs:
        with open(os.path.join(tmpdir, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(to_write) + "\n")

    await update.message.reply_text("🔐 Send environment variables (KEY=VALUE per line), or '-' to skip.", reply_markup=make_home_keyboard(uid), parse_mode=None)
    return HOST_WAIT_ENVS

def parse_env(text: str) -> Dict[str, str]:
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env

async def host_py_envs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    txt = (update.message.text or "-").strip()
    env = {} if txt == "-" else parse_env(txt)
    context.user_data["env"] = env
    tmpdir = context.user_data["tmpdir"]
    has_reqs = context.user_data.get("has_requirements", False)
    base_image = context.user_data.get("base_image") or BASE_IMAGE_DEFAULT
    with open(os.path.join(tmpdir, "Dockerfile"), "w", encoding="utf-8") as f:
        f.write(dockerfile_text(base_image, has_reqs))
    review = (
        "🧾 Review\n"
        f"• Base Image: {base_image}\n"
        f"• Requirements: {'yes' if has_reqs else 'no'}\n"
        f"• Env Vars: {json.dumps(env) if env else '-'}\n\n"
        "Reply yes to confirm, anything else to cancel."
    )
    await update.message.reply_text(review, reply_markup=make_home_keyboard(uid), parse_mode=None)
    return HOST_CONFIRM

def run_user_container(uid: int, tmpdir: str, env: dict) -> Tuple[str, str]:
    img_tag = f"pyimg_{uid}_{uuid.uuid4().hex[:6]}"
    DOCKER.images.build(path=tmpdir, tag=img_tag, rm=True, pull=False)
    extra_kwargs = {}
    try:
        extra_kwargs["pids_limit"] = DEFAULT_PIDS
    except Exception:
        pass
    app_id = uuid.uuid4().hex[:10]
    container = DOCKER.containers.run(
        image=img_tag,
        name=f"pybot_{uid}_{app_id}",
        detach=True,
        environment=env,
        mem_limit=DEFAULT_MEM,
        nano_cpus=_safe_nano_cpus(DEFAULT_CPUS),
        restart_policy={"Name": DEFAULT_RESTART},
        tty=False,
        stdin_open=False,
        privileged=False,
        cap_drop=["ALL"],
        **extra_kwargs,
    )
    APPS[app_id] = {
        "id": app_id,
        "name": f"pybot_{uid}_{app_id}",
        "image": img_tag,
        "container_id": container.id,
        "env": env,
        "created_at": time.time(),
        "restart": DEFAULT_RESTART,
        "cpus": DEFAULT_CPUS,
        "mem": DEFAULT_MEM,
        "owner": uid,
    }
    USER_APPS.setdefault(uid, []).append(app_id)
    return app_id, container.id

async def host_py_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return ConversationHandler.END
    if (update.message.text or "").strip().lower() != "yes":
        tmpdir = context.user_data.get("tmpdir")
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        await update.message.reply_text("Cancelled.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    tmpdir = context.user_data.get("tmpdir")
    env = context.user_data.get("env", {})
    if not tmpdir:
        await update.message.reply_text("Session expired. Use /host_py again.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return ConversationHandler.END
    await update.message.reply_text("🛠️ Building and starting your bot... Please wait.", reply_markup=make_home_keyboard(uid), parse_mode=None)
    try:
        app_id, cid = await asyncio.to_thread(run_user_container, uid, tmpdir, env)
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        await update.message.reply_text(f"Deploy failed: {e}", parse_mode=None, reply_markup=make_home_keyboard(uid))
        return ConversationHandler.END
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    await update.message.reply_text(
        f"✅ Deployed\n• App ID: {app_id}\n• Container: {cid[:12]}\nTip: Use /app_logs {app_id} for logs.",
        reply_markup=make_home_keyboard(uid),
        parse_mode=None
    )
    return ConversationHandler.END

# ---------- App helpers ----------
def parse_app_id(args: List[str], uid: int) -> Tuple[str | None, str]:
    if not args:
        mine = USER_APPS.get(uid, [])
        return None, f"Usage: /app_logs <app_id>\nYour apps: {', '.join(mine) if mine else '-'}"
    raw = " ".join(args).strip()
    if raw.isdigit():
        mine = USER_APPS.get(uid, [])
        idx = int(raw) - 1
        if 0 <= idx < len(mine):
            return mine[idx], ""
        return None, f"Invalid index. Your apps: {', '.join(mine) if mine else '-'}"
    cleaned = re.sub(r"[`\\[\\]\\(\\)\\{\\}'\",]", " ", raw)
    m = re.search(r"[0-9a-fA-F]{8,16}", cleaned)
    if m:
        return m.group(0).lower(), ""
    return None, "Could not read app_id. Copy from Apps view or use numeric index."

async def _get_app_for_user(uid: int, app_id: str) -> Dict[str, Any] | None:
    app = APPS.get(str(app_id))
    if not app:
        return None
    if app["owner"] != uid and not is_owner(uid):
        return None
    return app

# ---------- Inline Apps UI ----------
def apps_inline_markup(uid: int) -> InlineKeyboardMarkup:
    rows = []
    my_ids = [aid for aid in USER_APPS.get(uid, []) if aid in APPS]
    if not my_ids:
        return InlineKeyboardMarkup([[InlineKeyboardButton("No apps yet", callback_data="noop")]])
    for aid in my_ids:
        rows.append([
            InlineKeyboardButton(f"🟢 {aid[:6]}…", callback_data=f"app:info:{aid}"),
            make_inline_copy(aid),
            InlineKeyboardButton("🧾 Logs", callback_data=f"app:logs:{aid}"),
        ])
        rows.append([
            InlineKeyboardButton("⏹ Stop", callback_data=f"app:stop:{aid}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"app:rm:{aid}")
        ])
    return InlineKeyboardMarkup(rows)

async def apps_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return
    if not USER_APPS.get(uid):
        await update.message.reply_text("No deployments yet. Use “🚀 Host Python” to start.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return
    await update.message.reply_text("📦 Your Deployments", reply_markup=make_home_keyboard(uid), parse_mode=None)
    await update.message.reply_text("Select an action per app:", reply_markup=apps_inline_markup(uid), parse_mode=None)

async def on_app_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    uid = query.from_user.id
    data = query.data or ""
    try:
        _, action, aid = data.split(":", 2)
    except ValueError:
        await query.answer("Invalid action", show_alert=False)
        return
    app = APPS.get(aid)
    if not app or (app.get("owner") != uid and not is_owner(uid)):
        await query.answer("App not found or not permitted", show_alert=True)
        return

    if action == "info":
        spec = APPS.get(aid, {})
        text = (
            "🧩 App Info\n"
            f"• ID: {aid}\n"
            f"• Image: {spec.get('image')}\n"
            f"• Container: {spec.get('container_id','')[:12]}\n"
            f"• CPU: {spec.get('cpus')} | RAM: {spec.get('mem')}\n"
            f"• Restart: {spec.get('restart')} | Owner: {spec.get('owner')}\n"
            f"• Created: {expiry_str(spec.get('created_at', 0))}"
        )
        await query.message.reply_text(text, parse_mode=None)
        await query.answer()
        return

    if action == "logs":
        try:
            c = DOCKER.containers.get(app["container_id"])
            out = c.logs(tail=2000).decode(errors="ignore")
            if len(out) < 3500:
                await query.message.reply_text(f"🧾 Logs for {aid}\n\n{out or '(no logs)'}", parse_mode=None)
            else:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
                tmp.write(out.encode("utf-8")); tmp.flush(); tmp.close()
                with open(tmp.name, "rb") as f:
                    await query.message.reply_document(InputFile(f, filename=f"{aid}.log"))
                os.unlink(tmp.name)
            await query.answer()
        except NotFound:
            await query.answer("Container not found", show_alert=True)
        return

    if action == "stop":
        try:
            c = DOCKER.containers.get(app["container_id"])
            c.stop(timeout=10)
            await query.message.reply_text(f"⏹ Stopped {aid}", parse_mode=None)
            await query.answer()
        except NotFound:
            await query.answer("Container not found", show_alert=True)
        return

    if action == "rm":
        try:
            c = DOCKER.containers.get(app["container_id"])
            try:
                c.stop(timeout=8)
            except Exception:
                pass
            c.remove(force=True)
        except NotFound:
            pass
        APPS.pop(aid, None)
        if uid in USER_APPS and aid in USER_APPS[uid]:
            USER_APPS[uid].remove(aid)
        await query.message.reply_text(f"🗑 Removed {aid}", parse_mode=None)
        await query.answer()
        return

    # Fallback copy handler (when copy_text is unsupported)
    if action == "copy":
        await query.message.reply_text(f"App ID: {aid}\nLong-press to copy.", parse_mode=None)
        await query.answer("Displayed App ID", show_alert=False)
        return

# ---------- Shell ----------
COMMON_CMDS = [
    ("pwd", "pwd"),
    ("List files", "ls -la"),
    ("CPU/Memory", "free -m && df -h"),
    ("OS Info", "uname -a"),
    ("Processes", "ps aux | head -n 25"),
    ("Tail syslog", "tail -n 100 /var/log/syslog || true"),
]

ALL_LINUX_TEXT = (
    "📘 Linux quick guide:\n"
    "• Files: ls, cd, cp, mv, rm, mkdir, cat, head, tail, find, grep\n"
    "• System: uname -a, df -h, free -m, top/htop, id, env, date\n"
    "• Network: ping, curl, dig, ip addr, netstat\n"
    "• Packages: apt update, apt install <pkg>\n"
    "• Archives: tar (czf/xzf), unzip\n"
)

async def shell_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None)
        return
    if uid in SHELLS:
        try:
            c = DOCKER.containers.get(SHELLS[uid].get("container_id", ""))
            try:
                c.stop(timeout=5)
            except Exception:
                pass
            c.remove(force=True)
        except Exception:
            pass
        SHELLS.pop(uid, None)
    try:
        DOCKER.images.pull(SHELL_IMAGE)
    except Exception:
        pass
    extra_kwargs = {}
    try:
        extra_kwargs["pids_limit"] = SHELL_PIDS
    except Exception:
        pass
    container = DOCKER.containers.run(
        image=SHELL_IMAGE,
        name=f"shell_{uid}_{uuid.uuid4().hex[:6]}",
        command=["sleep", "infinity"],
        tty=True,
        stdin_open=True,
        detach=True,
        mem_limit=SHELL_MEM,
        nano_cpus=_safe_nano_cpus(SHELL_CPUS),
        restart_policy={"Name": "no"},
        privileged=False,
        cap_drop=["ALL"],
        **extra_kwargs,
    )
    SHELLS[uid] = {"container_id": container.id, "last_used": time.time()}
    await update.message.reply_text(
        "🖥️ Shell ready. Send /sh <command> or use quick buttons below.",
        reply_markup=make_shell_keyboard(uid), parse_mode=None
    )

async def shell_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if uid not in SHELLS:
        kb = make_home_keyboard(uid) if is_allowed(uid) else make_unauth_keyboard()
        await update.message.reply_text("No active shell. Use /shell_start.", reply_markup=kb, parse_mode=None)
        return
    entry = SHELLS[uid]
    if time.time() - entry["last_used"] > SHELL_IDLE_SECS:
        try:
            c = DOCKER.containers.get(entry.get("container_id", ""))
            try:
                c.stop(timeout=5)
            except Exception:
                pass
            c.remove(force=True)
        except Exception:
            pass
        SHELLS.pop(uid, None)
        await update.message.reply_text("Shell expired due to inactivity. Start again.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return
    if not context.args:
        await update.message.reply_text("Usage: /sh <command>", reply_markup=make_shell_keyboard(uid), parse_mode=None)
        return
    cmd = " ".join(context.args)
    try:
        c = DOCKER.containers.get(entry["container_id"])
    except NotFound:
        SHELLS.pop(uid, None)
        await update.message.reply_text("Shell missing. Start again.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return
    try:
        exec_id = DOCKER.api.exec_create(c.id, cmd=["bash", "-lc", cmd])
        output = DOCKER.api.exec_start(exec_id, stream=True)
        start_t = time.time()
        buf = []
        for chunk in output:
            try:
                buf.append(chunk.decode(errors="ignore"))
            except Exception:
                pass
            if time.time() - start_t > SHELL_EXEC_TIMEOUT:
                break
        text = "".join(buf).strip() or "(no output)"
        entry["last_used"] = time.time()
        if len(text) < 3500:
            await update.message.reply_text(text, parse_mode=None, reply_markup=make_shell_keyboard(uid))
            return
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.write(text.encode("utf-8")); tmp.flush(); tmp.close()
        with open(tmp.name, "rb") as f:
            await update.message.reply_document(InputFile(f, filename="output.txt"), reply_markup=make_shell_keyboard(uid))
        os.unlink(tmp.name)
    except APIError as e:
        await update.message.reply_text(f"Exec error: {getattr(e, 'explanation', str(e))}", parse_mode=None, reply_markup=make_shell_keyboard(uid))

async def shell_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    if uid in SHELLS:
        try:
            c = DOCKER.containers.get(SHELLS[uid].get("container_id", ""))
            try:
                c.stop(timeout=5)
            except Exception:
                pass
            c.remove(force=True)
        except Exception:
            pass
        SHELLS.pop(uid, None)
        await update.message.reply_text("Shell ended.", reply_markup=make_home_keyboard(uid), parse_mode=None)
        return
    await update.message.reply_text("No active shell.", reply_markup=make_home_keyboard(uid), parse_mode=None)

# ---------- Commands for apps (CLI-style) ----------
async def app_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    aid, hint = parse_app_id(context.args or [], uid)
    if not aid:
        await update.message.reply_text(hint, reply_markup=make_home_keyboard(uid), parse_mode=None); return
    app = await _get_app_for_user(uid, aid)
    if not app:
        await update.message.reply_text("App not found or not permitted.", reply_markup=make_home_keyboard(uid), parse_mode=None); return
    try:
        c = DOCKER.containers.get(app["container_id"])
        out = c.logs(tail=2000).decode(errors="ignore")
        if len(out) < 3500:
            await update.message.reply_text(f"🧾 Logs for {aid}\n\n{out or '(no logs)'}", parse_mode=None)
        else:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
            tmp.write(out.encode("utf-8")); tmp.flush(); tmp.close()
            with open(tmp.name, "rb") as f:
                await update.message.reply_document(InputFile(f, filename=f"{aid}.log"))
            os.unlink(tmp.name)
    except NotFound:
        await update.message.reply_text("Container not found.", reply_markup=make_home_keyboard(uid), parse_mode=None)

async def app_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    aid, hint = parse_app_id(context.args or [], uid)
    if not aid:
        await update.message.reply_text("Usage: /app_stop <app_id>\n" + hint, reply_markup=make_home_keyboard(uid), parse_mode=None); return
    app = await _get_app_for_user(uid, aid)
    if not app:
        await update.message.reply_text("App not found or not permitted.", reply_markup=make_home_keyboard(uid), parse_mode=None); return
    try:
        c = DOCKER.containers.get(app["container_id"]); c.stop(timeout=10)
        await update.message.reply_text(f"⏹ Stopped {aid}", parse_mode=None, reply_markup=make_home_keyboard(uid))
    except NotFound:
        await update.message.reply_text("Container not found.", reply_markup=make_home_keyboard(uid), parse_mode=None)

async def app_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    aid, hint = parse_app_id(context.args or [], uid)
    if not aid:
        await update.message.reply_text("Usage: /app_rm <app_id>\n" + hint, reply_markup=make_home_keyboard(uid), parse_mode=None); return
    app = await _get_app_for_user(uid, aid)
    if not app:
        await update.message.reply_text("App not found or not permitted.", reply_markup=make_home_keyboard(uid), parse_mode=None); return
    try:
        c = DOCKER.containers.get(app["container_id"])
        try: c.stop(timeout=8)
        except Exception: pass
        c.remove(force=True)
    except NotFound:
        pass
    APPS.pop(aid, None)
    if uid in USER_APPS and aid in USER_APPS[uid]:
        USER_APPS[uid].remove(aid)
    await update.message.reply_text(f"🗑 Removed {aid}", parse_mode=None, reply_markup=make_home_keyboard(uid))

# ---------- Admin Panel (reply keyboard -> inline) ----------
async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    record_user(update)
    uid = update.effective_user.id
    text = (update.message.text or "").strip().lower()

    if text == "🚀 host python" or text == "host python":
        await host_py_entry(update, context); return
    if text in ("📇 my id", "my id", "id"):
        await cmd_id(update, context); return
    if text == "🆘 help" or text == "help":
        await help_cmd(update, context); return
    if text == "👤 contact admin" or text == "contact admin":
        await contact_cmd(update, context); return
    if text == "⭐ premium status" or text == "premium status":
        await premium_status(update, context); return

    if text == "🛠️ admin panel" or text == "admin panel":
        if not is_owner(uid):
            await update.message.reply_text("Only owners can access Admin Panel.", reply_markup=make_home_keyboard(uid), parse_mode=None); return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Allow Add", callback_data="admin:template:allow_add"),
             InlineKeyboardButton("➖ Allow Remove", callback_data="admin:template:allow_remove")],
            [InlineKeyboardButton("👥 Show Allowed", callback_data="admin:allowed")],
            [InlineKeyboardButton("⭐ Set Premium", callback_data="admin:template:set_premium")]
        ])
        await update.message.reply_text("Admin Quick Actions:\nTap to get command templates or list allowed users.", reply_markup=kb, parse_mode=None)
        return

    if not is_allowed(uid):
        await update.message.reply_text(UNAUTH_MSG, reply_markup=make_unauth_keyboard(), parse_mode=None); return

    if text == "📦 apps" or text == "apps":
        await apps_list(update, context); return
    if text == "📜 logs" or text == "logs":
        mine = USER_APPS.get(uid, [])
        tip = "Use: /app_logs <app_id>, /app_stop <app_id>, /app_rm <app_id>"
        owned = f"Your apps: {', '.join(mine) if mine else '-'}"
        await update.message.reply_text(tip + "\n" + owned, reply_markup=make_home_keyboard(uid), parse_mode=None); return
    if text == "🖥️ shell start" or text == "shell start":
        await shell_start(update, context); return
    if text == "🛑 shell end" or text == "shell end":
        await shell_end(update, context); return
    if text == "⚡ run common cmds" or text == "run common cmds":
        if uid not in SHELLS:
            await update.message.reply_text("No active shell. Use /shell_start.", reply_markup=make_home_keyboard(uid), parse_mode=None); return
        items = [f"• {t}" for t, _ in COMMON_CMDS]
        items.append('Tip: use /sh "<command>" to run')
        await update.message.reply_text("Quick commands:\n" + "\n".join(items), reply_markup=make_shell_keyboard(uid), parse_mode=None); return
    if text == "📘 all linux commands" or text == "all linux commands":
        await update.message.reply_text(ALL_LINUX_TEXT, reply_markup=make_shell_keyboard(uid), parse_mode=None); return

    await update.message.reply_text("Unknown action. Use buttons or /help.", reply_markup=make_home_keyboard(uid), parse_mode=None)

# ---------- Callback routers (admin + apps) ----------
async def on_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    uid = query.from_user.id
    if not is_owner(uid):
        await query.answer("Owners only", show_alert=True)
        return
    data = query.data or ""
    if data == "admin:allowed":
        await allowed_list(update, context)
        await query.answer()
        return
    if data.startswith("admin:template:"):
        kind = data.split(":", 2)[-1]
        if kind == "allow_add":
            # Use inline copy if supported; fallback: just show the template
            kb = InlineKeyboardMarkup([[make_inline_copy("/allow_add <user_id>")]])
            await query.message.reply_text("Template:", reply_markup=kb, parse_mode=None)
        elif kind == "allow_remove":
            kb = InlineKeyboardMarkup([[make_inline_copy("/allow_remove <user_id>")]])
            await query.message.reply_text("Template:", reply_markup=kb, parse_mode=None)
        elif kind == "set_premium":
            kb = InlineKeyboardMarkup([[make_inline_copy("/set_premium <user_id> <days>")]])
            await query.message.reply_text("Template:", reply_markup=kb, parse_mode=None)
        await query.answer()
        return
    await query.answer()

# ---------- Conversations ----------
def build_host_py_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("host_py", host_py_entry),
            MessageHandler(filters.Regex(r"^(🚀 )?Host Python$"), host_py_entry),
        ],
        states={
            HOST_WAIT_FILE: [MessageHandler(filters.Document.ALL, host_py_file)],
            HOST_WAIT_REQS: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_py_requirements)],
            HOST_WAIT_ENVS: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_py_envs)],
            HOST_CONFIRM:   [MessageHandler(filters.TEXT & ~filters.COMMAND, host_py_confirm)],
        ],
        fallbacks=[],
        name="host_py_conv",
        persistent=False,
    )

def build_allow_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("allow_add", allow_add_entry),
            CommandHandler("allow_remove", allow_remove_entry),
        ],
        states={
            ALLOW_WAIT_ID_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, allow_add_collect)],
            ALLOW_WAIT_ID_REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, allow_remove_collect)],
        ],
        fallbacks=[],
        name="allow_conv",
        persistent=False,
    )

def build_premium_conv() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("set_premium", set_premium_entry),
        ],
        states={
            PREM_WAIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_premium_collect_id)],
            PREM_WAIT_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_premium_collect_days)],
        ],
        fallbacks=[],
        name="premium_conv",
        persistent=False,
    )

# ---------- Error handler ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Exception in handler", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, "⚠️ Internal error occurred. Please try again.", parse_mode=None)
    except Exception:
        pass

# ---------- Entry ----------
if __name__ == "__main__":
    if not BOT_TOKEN or "PASTE_TELEGRAM_BOT_TOKEN_HERE" in BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN first.")

    # Use no default parse mode to avoid formatting 400s with brackets/underscores
    defaults = Defaults(parse_mode=None)
    app = Application.builder().token(BOT_TOKEN).defaults(defaults).build()

    # Conversations FIRST
    app.add_handler(build_host_py_conv())
    app.add_handler(build_allow_conv())
    app.add_handler(build_premium_conv())

    # Basics
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("contact", contact_cmd))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("premium_status", premium_status))
    app.add_handler(CommandHandler("upgrade", upgrade_cmd))
    app.add_handler(CommandHandler("allowed", allowed_list))

    # Apps
    app.add_handler(CommandHandler("apps", apps_list))
    app.add_handler(CommandHandler("app_logs", app_logs))
    app.add_handler(CommandHandler("app_stop", app_stop))
    app.add_handler(CommandHandler("app_rm", app_rm))
    app.add_handler(CallbackQueryHandler(on_app_action, pattern=r"^app:(info|logs|stop|rm|copy):"))
    app.add_handler(CallbackQueryHandler(on_admin_action, pattern=r"^admin:"))

    # Shell
    app.add_handler(CommandHandler("shell_start", shell_start))
    app.add_handler(CommandHandler("sh", shell_run))
    app.add_handler(CommandHandler("shell_end", shell_end))

    # Menu router
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    # Error handler
    app.add_error_handler(on_error)

    app.run_polling()
