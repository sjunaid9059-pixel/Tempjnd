# TempMailer — Fast Temp Email + OTP Bot & Mini App

Telegram bot + premium Mini App for disposable email and instant OTP delivery.

## Features

- `/newmail` — create temp email in ~0.3s
- **Ultra-fast OTP** — polls every **0.4s**, pushes to chat the moment mail arrives
- One-tap copy (email + OTP)
- Multiple domains & inboxes
- Premium **Telegram Mini App** (glass UI, animations)
- Zero database (optional JSON state)

## Quick start (bot only)

```bash
pip install requests
export BOT_TOKEN="YOUR_BOT_TOKEN"
python tempmailer_bot.py
```

Optional Mini App button:

```bash
export WEBAPP_URL="https://your-miniapp.onrender.com"
python tempmailer_bot.py
```

## Mini App (Render)

See `miniapp/README.md`.

```bash
cd miniapp
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Commands

| Command   | Description            |
|-----------|------------------------|
| /start    | Welcome + buttons      |
| /newmail  | Create new inbox       |
| /inbox    | List messages          |
| /mails    | Switch inboxes         |
| /domains  | Pick domain            |
| /delete   | Delete current inbox   |

## Provider

[mail.tm](https://mail.tm) public API (no key).

## Note on OTP speed

Polling is **0.4s**. Actual delivery still depends on the sender + mail.tm.
As soon as the message is on mail.tm, the bot pushes it almost immediately.
