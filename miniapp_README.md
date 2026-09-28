# TempMailer Mini App (Render)

Premium animated Mini App + mail.tm proxy (CORS fixed).

## Deploy on Render (free)

1. Push the `miniapp` folder to a GitHub repo (or use Render Blueprint).
2. Render → **New → Web Service**
3. Connect repo, set:
   - **Root Directory:** `miniapp` (if repo root has other files)
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Deploy → copy the HTTPS URL (e.g. `https://tempmailer-xxxx.onrender.com`)

## Connect to Telegram bot

In the bot environment (or top of `tempmailer_bot.py`):

```bash
export WEBAPP_URL="https://YOUR-APP.onrender.com"
```

Restart the bot. `/start` will show **Open Mini App**.

## Local test

```bash
cd miniapp
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
# open http://127.0.0.1:8080
```

## Notes

- Mini App creates its own mailboxes (independent of bot inboxes).
- Bot still works as before for chat OTP push.
- Both can be used at the same time.
- Free Render spins down after idle — first open may take ~30s cold start.
