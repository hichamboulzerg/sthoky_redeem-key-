# Stocky Auto-Redeem Agent

Watches Telegram channel(s) for dropped keys (e.g. `SKCHK-2GND-W2GV-RUG6`) and
auto-redeems them by messaging the channel's bot from a **user account**.
A separate **control bot** gives you the command interface.

> ⚠️ A bot can't message another bot, so the redeeming is done by your *user
> account* (Telethon). The control-bot token is only for commands.

## 1. Get your API credentials
1. Go to https://my.telegram.org → **API development tools**
2. Create an app, copy `api_id` and `api_hash`
3. Put them in `.env` along with your phone number.

## 2. Install
```powershell
cd stocky-redeem-agent
py -m pip install -r requirements.txt
```

## 3. First run (one-time login)
```powershell
py agent.py
```
You'll be asked for the Telegram code (and 2FA password if set). A
`user_session` file is created so you won't have to log in again.

> The user account must be a **member of the channel** it watches, and must be
> able to message the target bot (open it once with /start in Telegram).

## 4. Use it
Open your control bot in Telegram (owner `975954498`) and send `/help`.

| Command | What it does |
|---|---|
| `/listbots` | show all profiles |
| `/addbot BOT CHANNEL PREFIX` | add a profile, e.g. `/addbot @Arshxchk2_bot 1002556821070 SKCHK` |
| `/removebot BOT` | remove a profile |
| `/focus` | high-alert mode (extra ping on every detected key) |
| `/last` | redeem the last captured key again |
| `/test BOT KEY` | send a specific key manually |
| `/pause` `/resume` | toggle auto-redeem |
| `/status` | agent status |
| `/ping` | connection check |

## Configuration
`config.json` is the live state (profiles, paused/focus, last key). It's seeded
with the `@Arshxchk2_bot` / channel `1002556821070` / `SKCHK` profile.

**`redeem_format`** controls what gets sent to the bot. Default is `"{key}"`
(just the raw key). If the bot expects a command, change it to e.g.
`"/redeem {key}"`.

## Run 24/7
On the VPS, run under a process manager so it restarts on reboot/crash, e.g.
`pm2 start agent.py --interpreter python3`, a `systemd` unit, or `nohup`.

## Security
The bot token was shared in chat — consider regenerating it via @BotFather.
Keep `.env` and `*_session` files private; the session file = full access to
your account.
