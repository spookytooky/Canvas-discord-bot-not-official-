# Canvas Discord Bot

A small personal Discord bot that shows your Canvas info as slash commands:

- `/grades` – your current score in each active course
- `/urgent` – assignments due in the next 72 hours
- `/links` – quick links to each course's Modules page

While it is running it can also post a daily digest of assignments due in the next 72 hours — the same list `/urgent` shows — to a channel you pick, at 12:00 PM Eastern.

Everyone who runs the bot uses their **own** Discord bot token and their **own** Canvas access token. Everything you need to change lives in one file: `.env`.

---

## What you need

- A computer (Windows, macOS, or Linux)
- Python 3.14 or newer – <https://www.python.org/downloads/> (on Windows, tick **"Add python.exe to PATH"** in the installer)
- A Discord account
- A Canvas account

---

## Step 1 – Get the project files

Put the project folder somewhere convenient, for example `Documents\CanvasBot`. If someone sent you a `.zip`, unzip it first. You don't need to edit any of the code – only the `.env` file you create in Step 6.

## Step 2 – Install the packages

Open a terminal in the project folder (the folder that contains this `README.md`).

- **Windows:** open the folder in File Explorer, type `cmd` in the address bar, and press Enter.
- **macOS:** right-click the folder → Services → New Terminal at Folder.
- **Linux:** open a terminal and `cd` into the folder.

Then run these commands, one at a time:

```
python -m venv .venv
```

(If `python` is not found on Windows, try `py -3.14 -m venv .venv`.)

Activate the virtual environment:

- **Windows (cmd):** `.venv\Scripts\activate.bat`
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1` – if you get a "running scripts is disabled" error, use `cmd` instead.
- **macOS/Linux:** `source .venv/bin/activate`

You should now see `(.venv)` at the start of your prompt. Install the packages:

```
pip install -r requirements.txt
```

(If you use [uv](https://docs.astral.sh/uv/) instead: `uv sync` installs everything, and `uv run python MainFile/discordBot.py` runs the bot.)

## Step 3 – Create your own Discord bot

1. Go to the Discord Developer Portal: <https://discord.com/developers/applications> and sign in.
2. Click **New Application**, give it a name (for example "My Canvas Bot"), and create it.
3. In the left sidebar, open the **Bot** tab, click **Reset Token**, confirm, then click **Copy** – this is your `DISCORD_TOKEN`.
4. You don't need to turn on any of the "Privileged Gateway Intents" toggles.

Now find your server ID:

5. In Discord, open **User Settings → Advanced** and turn on **Developer Mode**.
6. Right-click your server's icon in the left sidebar and click **Copy Server ID** – this is your `SERVER_ID`.

## Step 4 – Invite the bot to your server

1. Back in the Developer Portal, open **OAuth2 → URL Generator**.
2. Under **Scopes** tick `bot` and `applications.commands`.
3. Under **Bot Permissions** tick **View Channels**, **Send Messages**, and **Embed Links**.
4. Copy the generated URL at the bottom, paste it into your browser, choose your server, and click **Authorize**.

Shorthand: on the **General Information** page copy your **Application ID**, then open this URL with it:

```
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot%20applications.commands&permissions=19456
```

## Step 5 – Create a Canvas access token

1. Log in to Canvas (<https://canvas.unf.edu>, or your school's Canvas address).
2. Click **Account** in the left sidebar, then **Settings**.
3. Scroll to **Approved Integrations** and click **+ New Access Token**.
4. For "Purpose" enter something like "Discord bot", leave the expiry blank, and click **Generate Token**.
5. Copy the token immediately – Canvas only shows it once. This is your `CANVAS_TOKEN`.

> Treat this token like a password: it gives full access to your Canvas account. Never share it or post it anywhere. If you ever leak it, delete it on the same Settings page and generate a new one.
>
> Some schools disable student-created tokens. If you don't see "New Access Token", your school's Canvas admins have turned the feature off.

## Step 6 – Create your `.env` file

1. Make a copy of `.env.example` in the project folder.
2. Rename the copy to exactly `.env` (no other extension). On Windows you may need to name it `".env"` (with quotes) in Explorer so it doesn't become `.env.txt`.
3. Open it in Notepad (or any text editor) and fill in the values you collected:

```
DISCORD_TOKEN=paste-your-discord-bot-token
SERVER_ID=paste-your-server-id
CANVAS_TOKEN=paste-your-canvas-token
```

`ALERT_CHANNEL_ID` is optional: put a channel ID there to get the daily digest at 12:00 PM Eastern, or leave it blank to turn the daily message off.

Save the file. Never send your `.env` file to anyone – it contains your secrets.

## Step 7 – Run the bot

With the virtual environment active, from the project folder:

```
python MainFile/discordBot.py
```

You should see:

```
Logged in as YourBotName and synced tree to guild 1234567890
```

Go to your Discord server and type `/grades`, `/urgent`, or `/links`. If the commands don't appear right away, wait a minute and restart Discord.

To stop the bot, press Ctrl+C in the terminal. The bot only runs while this window is open.

While it is running, the bot posts a daily digest at 12:00 PM Eastern to the channel you set as `ALERT_CHANNEL_ID` — the same list `/urgent` shows. On days when nothing is due it still posts the `🎉 All Caught Up!` message. If you left the channel blank it posts nothing, and if you start the bot after 12:00 PM the next digest arrives the following morning.

Some schools are not UNF: if you go to another school, set `CANVAS_BASE_URL` in `.env` to your school's Canvas address (see the table below).

## Local database (SQLite)

The bot keeps a small SQLite database at `MainFile/canvas.db`. It is created automatically the first time the bot starts, it stays on the computer running the bot, and `.gitignore` keeps it out of git.

| Table | What it holds |
|---|---|
| `courses` | The active courses the bot has seen |
| `assignments` | Each assignment's due date, points, score, and whether it was submitted |
| `grade_snapshots` | One grade reading per course, per day |

Two things use it:

- **`/grades`** shows how each course's score moved since the last recorded day, for example `▲ 1.50 since the last recorded day`. One snapshot is saved per course per day, so running the command again the same day updates that reading instead of adding another.
- **`/urgent`** and the daily digest add a footer counting how much is due in each course, worked out by the database.

You can delete `MainFile/canvas.db` at any time — the bot recreates it on the next start and simply shows no history until it has recorded two days. Because it holds your real grades, never share it or commit it, exactly like your `.env` file.

### Looking at the data yourself

```python
import sqlite3

conn = sqlite3.connect("MainFile/canvas.db")
for course_id, score, day in conn.execute(
    "SELECT course_id, score, snapshot_date FROM grade_snapshots ORDER BY snapshot_date"
):
    print(course_id, score, day)
```

The storage code lives in `MainFile/storage.py`, and you can run its tests with:

```
python -m unittest discover -s tests -v
```

## Settings reference

| Setting in `.env` | Required? | Default | What it does |
|---|---|---|---|
| `DISCORD_TOKEN` | Yes | – | Your bot's token from the Developer Portal |
| `SERVER_ID` | Yes | – | The server where the slash commands are created |
| `ALERT_CHANNEL_ID` | No | blank | Channel that receives the daily digest at 12:00 PM Eastern. Blank = no daily message |
| `CANVAS_TOKEN` | Yes | – | Your Canvas access token |
| `CANVAS_BASE_URL` | No | `https://canvas.unf.edu` | Your school's Canvas address (no `/api/v1` at the end) |
| `CANVAS_TERM` | No | auto | Only courses whose name contains this code are shown, e.g. `202680` for UNF Fall 2026 |

## Troubleshooting

- **"Missing required setting: ..."** – your `.env` file is missing a value. Open it and fill in what the message names.
- **The bot starts but no slash commands appear** – make sure you invited it with both the `bot` and `applications.commands` scopes, and that `SERVER_ID` is the server you invited it to. Give Discord a minute, then restart the app.
- **"Couldn't reach Canvas" or HTTP 401** – your Canvas token is invalid or expired. Create a new one (Step 5) and update `CANVAS_TOKEN`.
- **Courses or grades look wrong** – if the wrong term's courses show up, set `CANVAS_TERM` (UNF: Fall 2026 = `202680`, Spring 2026 = `202610`, Summer 2026 = `202650`). Grades only appear once your instructor posts them.
- **"The application did not respond" in Discord** – usually the Canvas token problem above. Details are written to `MainFile/discord.log` (overwritten every time the bot starts).
- **Bot is offline** – check that `DISCORD_TOKEN` is correct and that the terminal window is still running.
- **The daily message didn't arrive** – the bot has to be running at 12:00 PM Eastern; it doesn't catch up later. It also needs **View Channels** and **Send Messages** in that channel. The next scheduled send is written to `MainFile/discord.log` every time the bot starts.

## Sharing this bot with a friend

Send them the folder without your secrets or your installed environment. Exclude:

- `.env` (contains your tokens)
- `.venv` (large, and your friend creates their own)
- `MainFile/discord.log`
- `MainFile/canvas.db` (contains your grades)
- `__pycache__` folders

Each person who runs the bot needs their own Discord bot token and their own Canvas token, and runs their own copy. One running copy only ever sees the Canvas account of the person whose token is in its `.env`.
