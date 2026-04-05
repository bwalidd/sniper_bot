#!/usr/bin/env python3
"""
Telegram Channel Monitor Bot
- Listens to any channel (without being admin) using a user session
- Sends persistent alerts to you via bot until you acknowledge
Commands: /start, /stop, /ack, /status
"""

import asyncio
import logging
import os
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIGURATION (from environment variables or hardcoded fallback) ──────────
API_ID       = int(os.environ.get("API_ID",       "10355672"))
API_HASH     =     os.environ.get("API_HASH",     "60c23bf0a07ba95092629d9c03a875bd")
BOT_TOKEN    =     os.environ.get("BOT_TOKEN",    "8576356344:AAHCjFcXN6ldSCVXYTafh1JjMkEv6rJRkCE")
CHANNEL      =     os.environ.get("CHANNEL",      "-1002783527346")
MY_USER_ID   = int(os.environ.get("MY_USER_ID",   "424011232"))
STRING_SESSION = os.environ.get("STRING_SESSION", "")  # Set this on Railway

ALERT_INTERVAL = 30  # seconds between repeated alerts
# ─────────────────────────────────────────────────────────────────────────────

# Convert CHANNEL to int if it's a numeric ID
try:
    CHANNEL = int(CHANNEL)
except ValueError:
    pass  # keep as string username like "@channelname"

# State
is_listening  = False
pending_alerts: list[dict] = []
alert_task: asyncio.Task | None = None

# Use StringSession if available (cloud), otherwise file session (local)
if STRING_SESSION:
    user_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
else:
    user_client = TelegramClient("user_session", API_ID, API_HASH)

bot = TelegramClient("bot_session", API_ID, API_HASH)


# ── ALERT LOOP ───────────────────────────────────────────────────────────────

async def alert_loop() -> None:
    global pending_alerts
    while True:
        active = [a for a in pending_alerts if not a["acked"]]
        if not active:
            pending_alerts.clear()
            break
        for alert in active:
            try:
                await bot.send_message(
                    MY_USER_ID,
                    f"🔔 **NEW MESSAGE in channel**\n\n"
                    f"👤 From: {alert['sender']}\n"
                    f"💬 {alert['text']}\n\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"Send /ack to silence · /stop to quit",
                )
            except Exception as e:
                log.error("Failed to send alert: %s", e)
        await asyncio.sleep(ALERT_INTERVAL)


async def queue_alert(text: str, sender: str) -> None:
    global alert_task
    pending_alerts.append({"text": text, "sender": sender, "acked": False})
    if alert_task is None or alert_task.done():
        alert_task = asyncio.create_task(alert_loop())


# ── BOT COMMANDS ─────────────────────────────────────────────────────────────

def only_me(event) -> bool:
    return event.sender_id == MY_USER_ID


@bot.on(events.NewMessage(pattern=r"^/start$"))
async def cmd_start(event):
    if not only_me(event):
        return
    global is_listening
    is_listening = True
    await event.respond(
        f"✅ **Monitoring started**\n\n"
        f"/stop — stop monitoring\n"
        f"/ack  — silence current alerts\n"
        f"/status — current state"
    )


@bot.on(events.NewMessage(pattern=r"^/stop$"))
async def cmd_stop(event):
    if not only_me(event):
        return
    global is_listening, pending_alerts
    is_listening = False
    for a in pending_alerts:
        a["acked"] = True
    await event.respond("⛔ **Monitoring stopped.** All alerts cleared.")


@bot.on(events.NewMessage(pattern=r"^/ack$"))
async def cmd_ack(event):
    if not only_me(event):
        return
    if not pending_alerts:
        await event.respond("✅ No active alerts.")
        return
    for a in pending_alerts:
        a["acked"] = True
    await event.respond("✅ **Alerts acknowledged.**")


@bot.on(events.NewMessage(pattern=r"^/status$"))
async def cmd_status(event):
    if not only_me(event):
        return
    unacked = sum(1 for a in pending_alerts if not a["acked"])
    state   = "🟢 Listening" if is_listening else "🔴 Stopped"
    await event.respond(
        f"**Status:** {state}\n"
        f"**Unacknowledged alerts:** {unacked}"
    )


# ── USER SESSION: watch the channel ─────────────────────────────────────────

@user_client.on(events.NewMessage(chats=CHANNEL))
async def on_channel_message(event):
    if not is_listening:
        return
    try:
        sender = await event.get_sender()
        sender_name = (
            getattr(sender, "username", None)
            or getattr(sender, "title", None)
            or getattr(sender, "first_name", None)
            or "Unknown"
        )
    except Exception:
        sender_name = "Unknown"

    text = event.text or "[media / non-text message]"
    log.info("New message from %s: %s", sender_name, text[:80])
    await queue_alert(text, sender_name)


# ── MAIN ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    await user_client.start()
    me = await user_client.get_me()
    log.info("User session: logged in as %s (@%s)", me.first_name, me.username)

    await bot.start(bot_token=BOT_TOKEN)
    log.info("Bot started. Send /start to begin monitoring.")

    try:
        await bot.send_message(MY_USER_ID, "🤖 Bot is online. Send /start to begin.")
    except Exception:
        pass

    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot.run_until_disconnected(),
    )


if __name__ == "__main__":
    asyncio.run(main())
