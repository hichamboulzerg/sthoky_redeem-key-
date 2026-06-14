"""
Stocky Auto-Redeem Agent
========================

Watches one or more Telegram channels for dropped keys (e.g. SKCHK-XXXX-XXXX-XXXX)
and automatically redeems them by messaging the channel's bot from a USER account.

Why a user account? Telegram does NOT allow a bot to message another bot, and bots
cannot freely read arbitrary channels. So:
  - a USER account (Telethon) watches the channel + sends the key to @TheBot
  - a CONTROL BOT gives you the command interface (/listbots, /focus, /status, ...)

Both clients run in the same process.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time

from dotenv import load_dotenv
from telethon import TelegramClient, events

# Windows consoles default to cp1252 and crash on emoji / non-ASCII names.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# --------------------------------------------------------------------------- #
# Config / env
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

load_dotenv(os.path.join(BASE_DIR, ".env"))

API_ID = int(os.getenv("API_ID") or 0)
API_HASH = os.getenv("API_HASH") or ""
PHONE = os.getenv("PHONE") or ""
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
OWNER_ID = int(os.getenv("OWNER_ID") or 0)

START_TIME = time.time()
REDEEMED = set()  # in-memory dedupe of keys already sent this run


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


state = load_config()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def normalize_channel(value):
    """Accept '1002556821070', '-1002556821070' or '@name'. Return int or str."""
    value = str(value).strip()
    if value.startswith("@"):
        return value
    n = int(value)
    # Telegram channels/supergroups use the -100... marked form.
    if n > 0:
        n = -n
    return n


def extract_keys(text, prefix):
    """Find all KEY tokens that start with `prefix` (e.g. SKCHK-2GND-W2GV-RUG6)."""
    if not text:
        return []
    pat = re.compile(r"\b" + re.escape(prefix) + r"[-A-Z0-9]+", re.IGNORECASE)
    return [m.group(0).upper() for m in pat.finditer(text)]


def profile_for_channel(chat_id):
    for p in state["profiles"]:
        if p["channel"] == chat_id:
            return p
    return None


def profile_for_bot(bot_username):
    b = bot_username.lower().lstrip("@")
    for p in state["profiles"]:
        if p["bot"].lower().lstrip("@") == b:
            return p
    return None


def profile_for_sender(sender):
    """Match an incoming DM sender against a profile's bot username."""
    if not sender:
        return None
    uname = (getattr(sender, "username", None) or "").lower()
    if not uname:
        return None
    for p in state["profiles"]:
        if p["bot"].lower().lstrip("@") == uname:
            return p
    return None


def fmt_uptime():
    s = int(time.time() - START_TIME)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}h {m}m {s}s"


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #
user = TelegramClient(os.path.join(BASE_DIR, "user_session"), API_ID, API_HASH)
bot = TelegramClient(os.path.join(BASE_DIR, "bot_session"), API_ID, API_HASH)


async def notify_owner(text):
    try:
        await bot.send_message(OWNER_ID, text, parse_mode="md", link_preview=False)
    except Exception as e:
        print(f"[notify_owner] {e}")


async def do_redeem(profile, key, source, dedupe=True):
    """Send a key to a profile's bot, wait for its reply, and send ONE summary."""
    if dedupe:
        if key in REDEEMED:
            return False, "duplicate (already redeemed this run)"
        REDEEMED.add(key)

    botname = profile["bot"]
    msg = state.get("redeem_format", "{key}").format(key=key)
    result_text = ""
    media_msg = None

    try:
        async with user.conversation(botname, timeout=45, exclusive=False) as conv:
            await conv.send_message(msg)
            try:
                resp = await conv.get_response(timeout=35)
                result_text = (resp.raw_text or "").strip()
                if resp.media:
                    media_msg = resp
                # Many bots send "Checking..." then EDIT it into the final result.
                try:
                    ed = await conv.get_edit(timeout=20)
                    if ed:
                        result_text = (ed.raw_text or "").strip() or result_text
                        if ed.media:
                            media_msg = ed
                except asyncio.TimeoutError:
                    pass
            except asyncio.TimeoutError:
                result_text = "(no reply from bot)"
    except Exception as e:
        await notify_owner(f"⚠️ `{key}` → {botname}\n{e}")
        return False, str(e)

    state["last"] = {"bot": botname, "key": key}
    save_config(state)

    # Single consolidated message: key -> bot + result (+ file if any).
    head = f"🔑 `{key}` → {botname}"
    body = f"\n```\n{result_text}\n```" if result_text else ""
    if media_msg is not None and media_msg.media:
        path = None
        try:
            path = await media_msg.download_media(file=tempfile.gettempdir())
            await bot.send_file(OWNER_ID, path, caption=head + body, parse_mode="md")
        except Exception as e:
            await notify_owner(head + body + f"\n(⚠️ file: {e})")
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
    else:
        await notify_owner(head + body)

    return True, "ok"


# --------------------------------------------------------------------------- #
# USER client: channel watcher
# --------------------------------------------------------------------------- #
@user.on(events.NewMessage)
async def on_channel_message(event):
    if state.get("paused"):
        return
    profile = profile_for_channel(event.chat_id)
    if not profile:
        return

    keys = extract_keys(event.raw_text, profile["prefix"])
    if state.get("debug"):
        await notify_owner(
            f"🔎 *debug* {profile['bot']} ch `{event.chat_id}`\n"
            f"raw: `{event.raw_text}`\n"
            f"keys: {keys}"
        )
    if not keys:
        return

    for key in keys:
        if state.get("focus"):
            await notify_owner(f"🚨 *KEY DETECTED* `{key}` in {profile['bot']} channel")
        ok, info = await do_redeem(profile, key, source="auto")
        if ok:
            await notify_owner(f"✅ Redeemed `{key}` → {profile['bot']}")
        else:
            await notify_owner(f"⚠️ `{key}` → {profile['bot']}: {info}")


async def _forward_bot_reply(event, edited=False):
    """Forward whatever a profile bot replies: text (token), AND any file/document."""
    if not event.is_private:
        return
    sender = await event.get_sender()
    p = profile_for_sender(sender)
    if not p:
        return

    text = (event.raw_text or "").strip()
    tag = "✏️ (updated)" if edited else "📩"
    header = f"{tag} {p['bot']} says:"

    # If the bot sent a file/document/photo, download it and re-send via control bot.
    if event.media:
        path = None
        try:
            path = await event.download_media(file=tempfile.gettempdir())
            cap = header + (f"\n```\n{text}\n```" if text else "")
            await bot.send_file(OWNER_ID, path, caption=cap, parse_mode="md")
        except Exception as e:
            await notify_owner(f"{header}\n{text}\n(⚠️ couldn't forward file: {e})")
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        return

    # Text-only reply (e.g. the bot token, or "Key already used").
    if text:
        await bot.send_message(
            OWNER_ID, f"{header}\n```\n{text}\n```", parse_mode="md"
        )


@user.on(events.NewMessage)
async def on_bot_reply(event):
    await _forward_bot_reply(event, edited=False)


@user.on(events.MessageEdited)
async def on_bot_reply_edited(event):
    await _forward_bot_reply(event, edited=True)


# --------------------------------------------------------------------------- #
# CONTROL bot: command interface
# --------------------------------------------------------------------------- #
def owner_only(handler):
    async def wrapper(event):
        if event.sender_id != OWNER_ID:
            return
        await handler(event)
    return wrapper


HELP_TEXT = (
    "🤖 *Stocky Auto-Redeem Agent*\n\n"
    "*Profiles* (bot + channel + prefix)\n"
    "`/listbots` — show all profiles\n"
    "`/addbot BOT CHANNEL PREFIX` — add a profile\n"
    "`/removebot BOT` — remove a profile\n\n"
    "*Redeem*\n"
    "`/setformat /redeem {key}` — set what gets sent\n"
    "`/focus` — high alert mode (toggle)\n"
    "`/last` — redeem last key again\n"
    "`/test BOT KEY` — test a specific key\n"
    "`/pause` /  `/resume` — toggle agent\n\n"
    "*Info*\n"
    "`/debug` — show raw channel text + keys (toggle)\n"
    "`/status` — agent status\n"
    "`/ping` — connection check"
)


@bot.on(events.NewMessage(pattern=r"^/(start|help)\b"))
@owner_only
async def cmd_help(event):
    await event.reply(HELP_TEXT, parse_mode="md")


@bot.on(events.NewMessage(pattern=r"^/listbots\b"))
@owner_only
async def cmd_listbots(event):
    if not state["profiles"]:
        await event.reply("No profiles yet. Add one with `/addbot BOT CHANNEL PREFIX`.", parse_mode="md")
        return
    lines = ["*Profiles:*"]
    for i, p in enumerate(state["profiles"], 1):
        lines.append(f"{i}. {p['bot']}  •  `{p['channel']}`  •  prefix `{p['prefix']}`")
    await event.reply("\n".join(lines), parse_mode="md")


@bot.on(events.NewMessage(pattern=r"^/addbot\s+(\S+)\s+(\S+)\s+(\S+)"))
@owner_only
async def cmd_addbot(event):
    botname, channel, prefix = event.pattern_match.group(1, 2, 3)
    if not botname.startswith("@"):
        botname = "@" + botname
    try:
        chan = normalize_channel(channel)
    except ValueError:
        await event.reply("❌ CHANNEL must be a numeric id or @username.")
        return
    if profile_for_bot(botname):
        await event.reply(f"❌ Profile for {botname} already exists. Remove it first.")
        return
    state["profiles"].append({"bot": botname, "channel": chan, "prefix": prefix.upper()})
    save_config(state)
    await event.reply(
        f"✅ Added profile\n{botname}  •  `{chan}`  •  prefix `{prefix.upper()}`",
        parse_mode="md",
    )


@bot.on(events.NewMessage(pattern=r"^/removebot\s+(\S+)"))
@owner_only
async def cmd_removebot(event):
    target = event.pattern_match.group(1)
    p = profile_for_bot(target)
    if not p:
        await event.reply(f"❌ No profile found for {target}.")
        return
    state["profiles"].remove(p)
    save_config(state)
    await event.reply(f"🗑️ Removed profile for {p['bot']}.")


@bot.on(events.NewMessage(pattern=r"^/setformat\s+(.+)"))
@owner_only
async def cmd_setformat(event):
    fmt = event.pattern_match.group(1).strip()
    if "{key}" not in fmt:
        await event.reply(
            "Format must contain `{key}`.\nExample: `/setformat /redeem {key}`",
            parse_mode="md",
        )
        return
    state["redeem_format"] = fmt
    save_config(state)
    sample = fmt.format(key="SKCHK-G9IC-1OVC-ZB5Z")
    await event.reply(f"✅ Redeem format set.\nWill send: `{sample}`", parse_mode="md")


@bot.on(events.NewMessage(pattern=r"^/debug\b"))
@owner_only
async def cmd_debug(event):
    state["debug"] = not state.get("debug")
    save_config(state)
    await event.reply(
        "🔎 Debug *ON* — forwarding raw channel text + detected keys."
        if state["debug"] else "Debug off.",
        parse_mode="md",
    )


@bot.on(events.NewMessage(pattern=r"^/focus\b"))
@owner_only
async def cmd_focus(event):
    state["focus"] = not state.get("focus")
    save_config(state)
    await event.reply("🚨 Focus mode *ON*" if state["focus"] else "Focus mode off", parse_mode="md")


@bot.on(events.NewMessage(pattern=r"^/last\b"))
@owner_only
async def cmd_last(event):
    last = state.get("last") or {}
    if not last.get("key"):
        await event.reply("No key has been captured yet.")
        return
    p = profile_for_bot(last["bot"]) or {"bot": last["bot"]}
    ok, info = await do_redeem(p, last["key"], source="manual", dedupe=False)
    if ok:
        await event.reply(f"♻️ Re-sent `{last['key']}` → {last['bot']}", parse_mode="md")
    else:
        await event.reply(f"⚠️ {info}")


@bot.on(events.NewMessage(pattern=r"^/test\s+(\S+)\s+(\S+)"))
@owner_only
async def cmd_test(event):
    botname, key = event.pattern_match.group(1, 2)
    if not botname.startswith("@"):
        botname = "@" + botname
    ok, info = await do_redeem({"bot": botname}, key.upper(), source="test", dedupe=False)
    if ok:
        await event.reply(f"🧪 Sent test `{key.upper()}` → {botname}", parse_mode="md")
    else:
        await event.reply(f"⚠️ {info}")


@bot.on(events.NewMessage(pattern=r"^/pause\b"))
@owner_only
async def cmd_pause(event):
    state["paused"] = True
    save_config(state)
    await event.reply("⏸️ Agent paused. Auto-redeem is OFF.")


@bot.on(events.NewMessage(pattern=r"^/resume\b"))
@owner_only
async def cmd_resume(event):
    state["paused"] = False
    save_config(state)
    await event.reply("▶️ Agent resumed. Auto-redeem is ON.")


@bot.on(events.NewMessage(pattern=r"^/status\b"))
@owner_only
async def cmd_status(event):
    last = state.get("last") or {}
    user_ok = user.is_connected()
    txt = (
        "*Agent status*\n"
        f"State: {'⏸️ paused' if state.get('paused') else '▶️ running'}\n"
        f"Focus: {'🚨 on' if state.get('focus') else 'off'}\n"
        f"User session: {'🟢 connected' if user_ok else '🔴 down'}\n"
        f"Profiles: {len(state['profiles'])}\n"
        f"Keys redeemed (this run): {len(REDEEMED)}\n"
        f"Last key: `{last.get('key', '—')}` → {last.get('bot', '—')}\n"
        f"Uptime: {fmt_uptime()}"
    )
    await event.reply(txt, parse_mode="md")


@bot.on(events.NewMessage(pattern=r"^/ping\b"))
@owner_only
async def cmd_ping(event):
    t0 = time.time()
    m = await event.reply("🏓 pinging...")
    dt = (time.time() - t0) * 1000
    await m.edit(
        f"🏓 pong • {dt:.0f} ms • user session: "
        f"{'🟢' if user.is_connected() else '🔴'}"
    )


# --------------------------------------------------------------------------- #
# Boot
# --------------------------------------------------------------------------- #
async def main():
    missing = [k for k, v in {"API_ID": API_ID, "API_HASH": API_HASH,
                              "PHONE": PHONE, "BOT_TOKEN": BOT_TOKEN,
                              "OWNER_ID": OWNER_ID}.items() if not v]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")

    await user.start(phone=PHONE)
    await bot.start(bot_token=BOT_TOKEN)

    me = await user.get_me()
    print(f"User account: {me.first_name} (@{me.username})")
    print(f"Watching {len(state['profiles'])} profile(s). Bot command interface live.")
    await notify_owner(
        f"🟢 *Agent online*\nWatching {len(state['profiles'])} profile(s). "
        f"Send /help for commands."
    )

    await asyncio.gather(
        user.run_until_disconnected(),
        bot.run_until_disconnected(),
    )


if __name__ == "__main__":
    asyncio.run(main())
