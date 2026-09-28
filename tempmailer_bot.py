#!/usr/bin/env python3
"""
TempMailer V2 — Premium Auto-OTP Telegram Bot (single file)
=========================================================

Features
--------
* /newmail  -> instantly creates a fresh disposable email on a real domain
* Domain picker (all domains available on the mail provider)
* INSTANT OTP push: a background watcher polls the mailbox every 1.0s and
  forwards the mail the moment it lands, with the OTP auto-extracted and
  shown as a tap-to-copy code. No delay, no manual refresh needed.
* Inline buttons: Copy / Inbox / New mail / Change domain / Delete
* Multiple mailboxes per user, full mail body reader
* Premium HTML UI, zero database (in-memory + optional JSON persistence)
* Smooth non-blocking create (runs in thread so bot never freezes)

Setup
-----
    pip install requests
    export BOT_TOKEN="..."       # or keep the one below
    python tempmailer_bot.py

Provider: mail.tm (free public API, no key needed).
"""

import os
import re
import json
import time
import random
import string
import logging
import threading
from typing import Dict, List, Optional

import requests

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8752892246:AAHaFU1quP2KLL2n0eTE3nyX5RzesMoWtds")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Mini App URL (Render HTTPS link). Leave empty to hide Open App button.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()

MAIL_API = "https://api.mail.tm"
POLL_INTERVAL = 0.4          # ultra-fast OTP detect
STATE_FILE = os.environ.get("TEMPMAIL_STATE", "tempmail_state.json")

OTP_PATTERNS = [
    r"\b(\d{4,8})\b",
    r"\b([A-Z0-9]{5,8})\b",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("tempmailer")

session = requests.Session()
session.headers.update({"User-Agent": "TempMailerBot/2.1", "Accept": "application/json"})

# Cache domains so create is fast
_domains_cache: List[str] = []
_domains_ts = 0.0
_DOMAINS_TTL = 300  # 5 min


# ----------------------------------------------------------------------------
# TELEGRAM HELPERS
# ----------------------------------------------------------------------------
def tg(method: str, **payload):
    try:
        r = session.post(f"{API}/{method}", json=payload, timeout=25)
        data = r.json()
        if not data.get("ok"):
            log.warning("TG %s failed: %s", method, data.get("description"))
        return data
    except Exception as e:
        log.warning("TG %s error: %s", method, e)
        return {"ok": False}


def send(chat_id: int, text: str, keyboard=None, preview=False):
    return tg(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=not preview,
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


def edit(chat_id: int, message_id: int, text: str, keyboard=None):
    return tg(
        "editMessageText",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup={"inline_keyboard": keyboard} if keyboard else None,
    )


def answer_cb(cb_id: str, text: str = "", alert: bool = False):
    tg("answerCallbackQuery", callback_query_id=cb_id, text=text, show_alert=alert)


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ----------------------------------------------------------------------------
# MAIL PROVIDER (mail.tm)
# ----------------------------------------------------------------------------
class MailError(Exception):
    pass


def mail_domains() -> List[str]:
    global _domains_cache, _domains_ts
    now = time.time()
    if _domains_cache and (now - _domains_ts) < _DOMAINS_TTL:
        return _domains_cache
    try:
        r = session.get(f"{MAIL_API}/domains?page=1", timeout=10)
        r.raise_for_status()
        data = r.json()
        # mail.tm sometimes returns plain list, sometimes hydra collection
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("hydra:member") or data.get("member") or []
        else:
            items = []
        domains = [d["domain"] for d in items if isinstance(d, dict) and d.get("isActive", True)]
        if domains:
            _domains_cache = domains
            _domains_ts = now
        return domains or _domains_cache
    except Exception as e:
        log.warning("domains fetch failed: %s", e)
        if _domains_cache:
            return _domains_cache
        raise MailError("Could not load domains. Try again in a moment.")


def rand_name() -> str:
    first = random.choice(
        "alex aria bella cody dora echo finn gia hugo iris jazz kiro luna maya nova "
        "orin pixi quin ruby sage tara umi vera wren xeno yuki zane vanessa pink "
        "leo mia noah elia ria sam kai zoey".split()
    )
    return f"{first}{random.randint(100, 9999)}"


def rand_pass(n: int = 14) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def create_mailbox(domain: Optional[str] = None) -> Dict:
    domains = mail_domains()
    if not domains:
        raise MailError("No mail domains available right now.")
    domain = domain if domain in domains else random.choice(domains)

    last_err = "unknown"
    for attempt in range(6):
        address = f"{rand_name()}@{domain}"
        password = rand_pass()
        try:
            r = session.post(
                f"{MAIL_API}/accounts",
                json={"address": address, "password": password},
                timeout=8,
            )
            if r.status_code in (200, 201):
                acc = r.json()
                token = mail_login(address, password)
                return {
                    "id": acc["id"],
                    "address": address,
                    "password": password,
                    "token": token,
                    "created": time.time(),
                    "seen": set(),
                }
            if r.status_code == 422:  # name taken
                last_err = "address taken"
                continue
            if r.status_code == 429:
                last_err = "rate limited"
                time.sleep(0.8 + attempt * 0.4)
                continue
            last_err = f"HTTP {r.status_code}"
            try:
                last_err += " " + str(r.json())[:100]
            except Exception:
                pass
            time.sleep(0.4)
        except requests.Timeout:
            last_err = "timeout"
            time.sleep(0.5)
        except Exception as e:
            last_err = str(e)[:80]
            time.sleep(0.5)
    raise MailError(f"Could not create mailbox ({last_err}). Try again.")


def mail_login(address: str, password: str) -> str:
    r = session.post(
        f"{MAIL_API}/token",
        json={"address": address, "password": password},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()["token"]


def auth(box: Dict) -> Dict:
    return {"Authorization": f"Bearer {box['token']}"}


def list_messages(box: Dict) -> List[Dict]:
    r = session.get(f"{MAIL_API}/messages?page=1", headers=auth(box), timeout=10)
    if r.status_code == 401:
        box["token"] = mail_login(box["address"], box["password"])
        r = session.get(f"{MAIL_API}/messages?page=1", headers=auth(box), timeout=10)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("hydra:member") or data.get("member") or []
    return []


def get_message(box: Dict, mid: str) -> Dict:
    r = session.get(f"{MAIL_API}/messages/{mid}", headers=auth(box), timeout=10)
    if r.status_code == 401:
        box["token"] = mail_login(box["address"], box["password"])
        r = session.get(f"{MAIL_API}/messages/{mid}", headers=auth(box), timeout=10)
    r.raise_for_status()
    return r.json()


def delete_mailbox(box: Dict):
    try:
        session.delete(f"{MAIL_API}/accounts/{box['id']}", headers=auth(box), timeout=10)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# OTP EXTRACTION
# ----------------------------------------------------------------------------
NOISE = {"2024", "2025", "2026", "0000", "1111", "1234", "9999"}


def extract_otp(subject: str, body: str) -> Optional[str]:
    blob = f"{subject}\n{body}"
    hinted = re.search(
        r"(?:verification\s+code|otp|code|pin|passcode|token)(?:\s+(?:is|number))?\s*[:#-]?\s*([A-Z0-9]{4,8})\b",
        blob,
        re.I,
    )
    if hinted:
        return hinted.group(1).strip()
    for pat in OTP_PATTERNS:
        for m in re.findall(pat, blob):
            if m not in NOISE and not m.isalpha():
                return m
    return None


def clean_body(msg: Dict) -> str:
    text = msg.get("text") or ""
    if not text and msg.get("html"):
        html = msg["html"][0] if isinstance(msg["html"], list) else msg["html"]
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:1500]


# ----------------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------------
users: Dict[int, Dict] = {}  # chat_id -> {"boxes": [box], "active": idx}
lock = threading.Lock()


def user(chat_id: int) -> Dict:
    with lock:
        return users.setdefault(chat_id, {"boxes": [], "active": 0})


def active_box(chat_id: int) -> Optional[Dict]:
    u = user(chat_id)
    if not u["boxes"]:
        return None
    u["active"] = min(u["active"], len(u["boxes"]) - 1)
    return u["boxes"][u["active"]]


def save_state():
    try:
        dump = {
            str(cid): {
                "active": u["active"],
                "boxes": [
                    {k: v for k, v in b.items() if k != "seen"} | {"seen": list(b["seen"])}
                    for b in u["boxes"]
                ],
            }
            for cid, u in users.items()
        }
        with open(STATE_FILE, "w") as f:
            json.dump(dump, f)
    except Exception as e:
        log.debug("state save failed: %s", e)


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            raw = json.load(f)
        for cid, u in raw.items():
            boxes = []
            for b in u["boxes"]:
                b["seen"] = set(b.get("seen", []))
                boxes.append(b)
            users[int(cid)] = {"boxes": boxes, "active": u.get("active", 0)}
        log.info("Restored %d users", len(users))
    except Exception as e:
        log.warning("state load failed: %s", e)


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
def webapp_row() -> List[Dict]:
    if not WEBAPP_URL:
        return []
    return [{"text": "🖥  Open Mini App", "web_app": {"url": WEBAPP_URL}}]


def mail_keyboard(idx: int, address: str) -> List[List[Dict]]:
    rows = [
        [{"text": "📋  Copy email", "copy_text": {"text": address}}],
        [
            {"text": "📨  Inbox", "callback_data": f"inbox:{idx}"},
            {"text": "⚡  New email", "callback_data": "new"},
        ],
        [
            {"text": "🌐  Domains", "callback_data": "domains"},
            {"text": "🗂  My inboxes", "callback_data": "boxes"},
        ],
        [{"text": "🗑  Delete this inbox", "callback_data": f"del:{idx}"}],
    ]
    wa = webapp_row()
    if wa:
        rows.insert(1, wa)
    return rows


def otp_keyboard(otp: str) -> List[List[Dict]]:
    rows = [
        [{"text": f"📋  Copy OTP  •  {otp}", "copy_text": {"text": otp}}],
        [
            {"text": "⚡  New email", "callback_data": "new"},
            {"text": "🗂  My inboxes", "callback_data": "boxes"},
        ],
    ]
    wa = webapp_row()
    if wa:
        rows.append(wa)
    return rows


def welcome_keyboard() -> List[List[Dict]]:
    rows = [[{"text": "✨ Generate email", "callback_data": "new"}]]
    wa = webapp_row()
    if wa:
        rows.append(wa)
    return rows


def mail_card(box: Dict) -> str:
    return (
        "<b>✦ TEMPMAILER  •  LIVE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Your private inbox is ready</b>\n"
        "Messages and OTPs will appear here automatically.\n\n"
        "📮 <b>EMAIL ADDRESS</b>\n"
        f"<code>{esc(box['address'])}</code>\n\n"
        "🟢 <b>Auto-delivery active</b>\n"
        "<i>No fetch. No refresh. Just wait here.</i>"
    )


WELCOME = (
    "<b>✦ TEMPMAILER</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "<b>Fast, private temporary email</b>\n"
    "Create an inbox and receive new messages plus OTP codes automatically.\n\n"
    "⚡ Auto OTP delivery\n"
    "📋 One-tap copy\n"
    "🌐 Multiple live domains\n"
    "🔒 Disposable private inboxes\n\n"
    "<i>Create your first inbox below.</i>"
)


# ----------------------------------------------------------------------------
# WATCHER — instant OTP push
# ----------------------------------------------------------------------------
def watcher(chat_id: int, box: Dict):
    log.info("watcher started for %s", box["address"])
    while True:
        with lock:
            u = users.get(chat_id)
            alive = bool(u and any(b["address"] == box["address"] for b in u["boxes"]))
        if not alive:
            log.info("watcher stopped for %s", box["address"])
            return
        try:
            for msg in reversed(list_messages(box)):
                mid = msg["id"]
                if mid in box["seen"]:
                    continue
                if push_mail(chat_id, box, mid):
                    box["seen"].add(mid)
                    save_state()
        except Exception as e:
            log.debug("poll error %s: %s", box["address"], e)
            time.sleep(0.8)
        time.sleep(POLL_INTERVAL)


def push_mail(chat_id: int, box: Dict, mid: str) -> bool:
    try:
        full = get_message(box, mid)
    except Exception as e:
        log.warning("fetch mail failed: %s", e)
        return False
    subject = full.get("subject") or "(no subject)"
    sender = (full.get("from") or {}).get("address", "unknown")
    body = clean_body(full)
    otp = extract_otp(subject, body)

    if otp:
        text = (
            "<b>⚡ OTP ARRIVED  •  AUTO-DETECTED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{esc(otp)}</code>\n"
            "<i>Tap the button below to copy instantly.</i>\n\n"
            f"<b>From</b>  {esc(sender)}\n"
            f"<b>Subject</b>  {esc(subject)}"
        )
        result = send(chat_id, text, otp_keyboard(otp))
    else:
        text = (
            "<b>✦ NEW MESSAGE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>From</b>  <code>{esc(sender)}</code>\n"
            f"<b>Subject</b>  {esc(subject)}\n\n"
            f"<blockquote>{esc(body) or '(empty body)'}</blockquote>"
        )
        result = send(chat_id, text, [[{"text": "⚡  New email", "callback_data": "new"}]])
    return bool(result.get("ok"))


def ensure_watcher(chat_id: int, box: Dict):
    t = threading.Thread(target=watcher, args=(chat_id, box), daemon=True)
    t.start()


# ----------------------------------------------------------------------------
# ACTIONS (create runs in thread → bot never freezes)
# ----------------------------------------------------------------------------
def _do_create(chat_id: int, domain: Optional[str], note_mid: Optional[int]):
    try:
        box = create_mailbox(domain)
    except Exception as e:
        msg = f"⚠️ <b>Could not create a mailbox.</b>\n<code>{esc(str(e))}</code>\nTry /newmail again."
        if note_mid:
            edit(chat_id, note_mid, msg)
        else:
            send(chat_id, msg)
        return
    u = user(chat_id)
    with lock:
        u["boxes"].append(box)
        u["active"] = len(u["boxes"]) - 1
    idx = u["active"]
    if note_mid:
        edit(chat_id, note_mid, mail_card(box), mail_keyboard(idx, box["address"]))
    else:
        send(chat_id, mail_card(box), mail_keyboard(idx, box["address"]))
    ensure_watcher(chat_id, box)
    save_state()


def action_new(chat_id: int, domain: Optional[str] = None):
    note = send(chat_id, "⏳ <i>Creating your inbox…</i>")
    note_mid = note.get("result", {}).get("message_id")
    t = threading.Thread(
        target=_do_create, args=(chat_id, domain, note_mid), daemon=True
    )
    t.start()


def action_inbox(chat_id: int):
    box = active_box(chat_id)
    if not box:
        send(chat_id, "📭 No inbox yet. Use /newmail to create one.")
        return
    try:
        msgs = list_messages(box)
    except Exception as e:
        send(chat_id, f"⚠️ Inbox unavailable: <code>{esc(str(e))}</code>")
        return
    if not msgs:
        send(
            chat_id,
            f"📭 <b>Inbox empty</b>\n<code>{esc(box['address'])}</code>\n\n"
            "⚡️ Relax — new mail lands here automatically the second it arrives.",
            mail_keyboard(user(chat_id)["active"], box["address"]),
        )
        return
    rows = []
    lines = [f"📥 <b>Inbox</b> — <code>{esc(box['address'])}</code>\n"]
    for i, m in enumerate(msgs[:10], 1):
        sender = (m.get("from") or {}).get("address", "unknown")
        lines.append(
            f"<b>{i}.</b> {esc(m.get('subject') or '(no subject)')}\n   ↳ <code>{esc(sender)}</code>"
        )
        rows.append([{"text": f"Open {i}", "callback_data": f"open:{m['id']}"}])
    rows.append([{"text": "🟢  Auto-delivery is active", "callback_data": "status"}])
    send(chat_id, "\n".join(lines), rows)


def action_domains(chat_id: int):
    try:
        domains = mail_domains()
    except Exception as e:
        send(chat_id, f"⚠️ Domain list unavailable: <code>{esc(str(e))}</code>")
        return
    rows = [[{"text": f"@{d}", "callback_data": f"dom:{d}"}] for d in domains[:20]]
    send(chat_id, "🌐 <b>Pick a domain</b>\nYour new email will use it.", rows)


def action_boxes(chat_id: int):
    u = user(chat_id)
    if not u["boxes"]:
        send(chat_id, "📭 No inbox yet. Use /newmail.")
        return
    rows, lines = [], ["🗂 <b>Your inboxes</b>\n"]
    for i, b in enumerate(u["boxes"]):
        mark = "▶️" if i == u["active"] else "•"
        lines.append(f"{mark} <code>{esc(b['address'])}</code>")
        rows.append([{"text": f"Use {b['address']}", "callback_data": f"use:{i}"}])
    send(chat_id, "\n".join(lines), rows)


def action_delete(chat_id: int):
    u = user(chat_id)
    box = active_box(chat_id)
    if not box:
        send(chat_id, "Nothing to delete.")
        return
    with lock:
        u["boxes"].remove(box)
        u["active"] = 0
    delete_mailbox(box)
    save_state()
    send(chat_id, f"🗑 Deleted <code>{esc(box['address'])}</code>\n\nUse /newmail for a fresh one.")


# ----------------------------------------------------------------------------
# DISPATCH
# ----------------------------------------------------------------------------
def handle_message(msg: Dict):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip().lower()
    if text.startswith("/start"):
        send(chat_id, WELCOME, welcome_keyboard())
    elif text.startswith("/newmail") or text.startswith("/new"):
        action_new(chat_id)
    elif text.startswith("/inbox"):
        action_inbox(chat_id)
    elif text.startswith("/domains"):
        action_domains(chat_id)
    elif text.startswith("/mails") or text.startswith("/my"):
        action_boxes(chat_id)
    elif text.startswith("/delete"):
        action_delete(chat_id)
    elif text.startswith("/help"):
        send(chat_id, WELCOME, welcome_keyboard())
    else:
        send(chat_id, "Use /newmail for a fresh email or /inbox to read mail.")


def handle_callback(cb: Dict):
    chat_id = cb["message"]["chat"]["id"]
    data = cb.get("data") or ""
    cid = cb["id"]

    if data == "new":
        answer_cb(cid, "Creating…")
        action_new(chat_id)
    elif data.startswith("dom:"):
        answer_cb(cid, "Creating…")
        action_new(chat_id, data.split(":", 1)[1])
    elif data.startswith("copy"):
        box = active_box(chat_id)
        answer_cb(cid, box["address"] if box else "No inbox", alert=True)
        if box:
            send(chat_id, f"<code>{esc(box['address'])}</code>\n<i>Tap to copy.</i>")
    elif data.startswith("inbox"):
        answer_cb(cid, "Opening inbox…")
        action_inbox(chat_id)
    elif data == "domains":
        answer_cb(cid)
        action_domains(chat_id)
    elif data == "boxes":
        answer_cb(cid)
        action_boxes(chat_id)
    elif data == "status":
        answer_cb(cid, "Auto-delivery is active — no refresh needed.", alert=True)
    elif data.startswith("use:"):
        idx = int(data.split(":", 1)[1])
        u = user(chat_id)
        if 0 <= idx < len(u["boxes"]):
            u["active"] = idx
            box = u["boxes"][idx]
            answer_cb(cid, f"Switched to {box['address']}")
            send(chat_id, mail_card(box), mail_keyboard(idx, box["address"]))
        else:
            answer_cb(cid, "Gone")
    elif data.startswith("open:"):
        answer_cb(cid, "Opening…")
        box = active_box(chat_id)
        if box:
            push_mail(chat_id, box, data.split(":", 1)[1])
    elif data.startswith("del:"):
        answer_cb(cid, "Deleted")
        action_delete(chat_id)
    else:
        answer_cb(cid)


# ----------------------------------------------------------------------------
# MAIN LOOP (long polling)
# ----------------------------------------------------------------------------
def main():
    if "PASTE_YOUR_BOT_TOKEN" in BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN env var (or edit the file) with your @BotFather token.")

    me = tg("getMe").get("result", {})
    log.info("Bot online: @%s", me.get("username"))

    load_state()
    for chat_id, u in users.items():
        for box in u["boxes"]:
            ensure_watcher(chat_id, box)

    # Menu (3-dots) — start + newmail first
    tg(
        "setMyCommands",
        commands=[
            {"command": "start", "description": "Start the bot"},
            {"command": "newmail", "description": "Create a new temp email"},
            {"command": "inbox", "description": "Read your inbox"},
            {"command": "mails", "description": "Switch between inboxes"},
            {"command": "domains", "description": "Choose a domain"},
            {"command": "delete", "description": "Delete current inbox"},
        ],
    )

    # Warm domain cache on startup
    try:
        mail_domains()
        log.info("Domains ready: %s", _domains_cache)
    except Exception as e:
        log.warning("Initial domain fetch: %s", e)

    offset = 0
    while True:
        try:
            r = session.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            ).json()
        except Exception as e:
            log.debug("getUpdates: %s", e)
            time.sleep(2)
            continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            try:
                if "message" in upd:
                    handle_message(upd["message"])
                elif "callback_query" in upd:
                    handle_callback(upd["callback_query"])
            except Exception as e:
                log.exception("handler error: %s", e)


if __name__ == "__main__":
    main()
