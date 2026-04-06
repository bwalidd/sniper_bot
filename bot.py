#!/usr/bin/env python3
"""
Telegram Channel Monitor Bot
- Sends an Apple-style ringtone audio + inline ACK button
- Repeats until you tap the button or send /ack
"""

import asyncio
import io
import logging
import math
import os
import struct
import wave
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
API_ID         = int(os.environ["API_ID"])
API_HASH       =     os.environ["API_HASH"]
BOT_TOKEN      =     os.environ["BOT_TOKEN"]
CHANNEL        =     os.environ.get("CHANNEL", "")
MY_USER_ID     = int(os.environ["MY_USER_ID"])
STRING_SESSION =     os.environ.get("STRING_SESSION", "")

ALERT_INTERVAL = 20  # seconds between ringtone repeats
# ─────────────────────────────────────────────────────────────────────────────

try:
    CHANNEL = int(CHANNEL)
except ValueError:
    pass

# ── RINGTONE GENERATOR (Apple "Opening" melody approximation) ─────────────────
def make_ringtone() -> bytes:
    sample_rate = 44100

    def tone(freq: float, dur: float, vol: float = 0.75) -> list[bytes]:
        n     = int(sample_rate * dur)
        fade  = int(sample_rate * 0.012)
        out   = []
        for i in range(n):
            t = i / sample_rate
            v = vol * math.sin(2 * math.pi * freq * t)
            if i < fade:
                v *= i / fade
            elif i > n - fade:
                v *= (n - i) / fade
            out.append(struct.pack("<h", int(v * 32767)))
        return out

    def silence(dur: float) -> list[bytes]:
        return [struct.pack("<h", 0)] * int(sample_rate * dur)

    # Notes: E5 D5 A4 / E5 D5 G4 / E5 D5 A4 G4 E4
    E5, D5, A4, G4, E4 = 659.25, 587.33, 440.00, 392.00, 329.63
    frames: list[bytes] = []
    for seq in [
        (E5, 0.13), None, (D5, 0.13), None, (A4, 0.28), None,
        (E5, 0.13), None, (D5, 0.13), None, (G4, 0.28), None,
        (E5, 0.13), None, (D5, 0.13), None, (A4, 0.13), None,
        (G4, 0.13), None, (E4, 0.38),
    ]:
        if seq is None:
            frames += silence(0.04)
        else:
            frames += tone(*seq)

    buf = io.BytesIO()
    with wave.open(buf, "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(frames))
    return buf.getvalue()

RINGTONE_BYTES = make_ringtone()
log.info("Ringtone generated: %d bytes", len(RINGTONE_BYTES))

# ── STATE ─────────────────────────────────────────────────────────────────────
is_listening   = False
pending_alerts: list[dict] = []
alert_task: asyncio.Task | None = None

# ── CLIENTS ───────────────────────────────────────────────────────────────────
if STRING_SESSION:
    user_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
else:
    user_client = TelegramClient("user_session", API_ID, API_HASH)

bot = TelegramClient("bot_session", API_ID, API_HASH)

# ── ALERT LOOP ────────────────────────────────────────────────────────────────
async def alert_loop() -> None:
    global pending_alerts
    while True:
        active = [a for a in pending_alerts if not a["acked"]]
        if not active:
            pending_alerts.clear()
            break

        for alert in active:
            try:
                ring = io.BytesIO(RINGTONE_BYTES)
                ring.name = "ringtone.wav"
                await bot.send_file(
                    MY_USER_ID,
                    ring,
                    caption=(
                        f"🔔 **NEW MESSAGE**\n\n"
                        f"👤 {alert['sender']}\n"
                        f"💬 {alert['text']}"
                    ),
                    buttons=[
                        [Button.inline("✅  Stop alert", b"ack")],
                    ],
                )
            except Exception as e:
                log.error("Failed to send ringtone alert: %s", e)

        await asyncio.sleep(ALERT_INTERVAL)


async def queue_alert(text: str, sender: str) -> None:
    global alert_task
    pending_alerts.append({"text": text, "sender": sender, "acked": False})
    if alert_task is None or alert_task.done():
        alert_task = asyncio.create_task(alert_loop())


# ── INLINE BUTTON: tap "Stop alert" on the notification ──────────────────────
@bot.on(events.CallbackQuery(data=b"ack"))
async def cb_ack(event):
    if event.sender_id != MY_USER_ID:
        await event.answer("Not authorised.")
        return
    for a in pending_alerts:
        a["acked"] = True
    await event.answer("✅ Alert stopped!")
    await event.edit(
        (await event.get_message()).text + "\n\n✅ _Acknowledged_",
        buttons=None,
    )


# ── BOT COMMANDS ──────────────────────────────────────────────────────────────
def only_me(event) -> bool:
    return event.sender_id == MY_USER_ID


@bot.on(events.NewMessage(pattern=r"^/start$"))
async def cmd_start(event):
    if not only_me(event):
        return
    global is_listening
    is_listening = True
    await event.respond(
        "✅ **Monitoring started**\n\n"
        "/stop — stop monitoring\n"
        "/ack  — silence current alerts\n"
        "/status — current state"
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


# ── CHANNEL WATCHER ───────────────────────────────────────────────────────────
@user_client.on(events.NewMessage(chats=CHANNEL))
async def on_channel_message(event):
    if not is_listening:
        return
    try:
        sender      = await event.get_sender()
        sender_name = (
            getattr(sender, "username",   None)
            or getattr(sender, "title",   None)
            or getattr(sender, "first_name", None)
            or "Unknown"
        )
    except Exception:
        sender_name = "Unknown"

    text = event.text or "[media / non-text message]"
    log.info("Channel message from %s: %s", sender_name, text[:80])
    await queue_alert(text, sender_name)


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    await user_client.start()
    me = await user_client.get_me()
    log.info("User session: %s (@%s)", me.first_name, me.username)

    await bot.start(bot_token=BOT_TOKEN)
    log.info("Bot ready. Send /start to begin monitoring.")

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
