# Kâhya (kahya)

*/*käh-yä/* — the steward who runs the household.*

**Your personal operations steward, built on [amele](https://github.com/lasthumanintheloop/amele).**
Bills, deadlines, vaccinations, birthdays, reminders — you tell it in plain
language over Telegram, it tracks everything in one SQLite file and reminds
you when it matters. Runs on a Raspberry Pi (or any box), entirely self-hosted.

```
"Water bill 3000 TL arrived, due 19 august"
        │
        ▼
   Telegram bot → amele extract agent → follow-up question
        │
"August 19" → confirmation card → "yes"
        │
        ▼
   SQLite · reminders armed → amele reminder agent spawns on the 17th/18th/19th
```

> **50 configured agents, 0 running.** Kâhya is idle until there is work:
> every reminder, every extraction, every answer is an amele agent that
> spawns, does its job, and exits. The organization exists as files; workers
> exist only while working.

---

## Why amele

Kâhya is deliberately thin: its core (scheduler, bot, web panel, SQLite) is a
small always-on layer, and **every AI step is an amele agent** — a single YAML
file in [`agents/`](agents/). That buys three things:

- **Any LLM, local or cloud.** Point `BASE_URL` at a local Ollama
  (`http://host:11434/v1`) or at OpenAI/OpenRouter — the agents don't care.
  Your data stays on your hardware.
- **Organization as code.** Creating an agent from the web panel or from
  Telegram writes `agents/<slug>.yaml`. Version it, diff it, review it in a
  PR, share it. A PR is literally "what changed in my company".
- **A well-mannered runtime.** Schema-guaranteed JSON output (`output.schema`
  in [`agents/extract.yaml`](agents/extract.yaml)), meaningful exit codes,
  one JSONL log per run — all amele contracts.

## Requirements

- Python 3.9+ (stdlib only — no pip installs; verified on 3.9 and 3.12)
- The amele binary (one static file, ~7.5 MB)
- A Telegram bot token (from @BotFather) and your chat id
- An LLM endpoint: local Ollama or any OpenAI-compatible API

## Install

**One command (any platform — Linux, macOS, Windows, Raspberry Pi):**

```bash
git clone https://github.com/loxigosoftware/kahya
cd kahya
python3 install.py
```

The installer detects your platform (or you pick from a list), downloads
the matching amele binary from GitHub, **verifies it against SHA256SUMS**,
creates `.env`, port-tests the panel port (8080 → next free if taken) and
prints your LAN address + first-login credentials.

On Linux with systemd it also offers to **install auto-start units** for
the three services (`Restart=on-failure` — a crash is brought back
automatically, and everything comes up on boot). Answer `y` and it
generates the units for your user/paths and runs `systemctl enable --now`
(needs sudo). Skip it and run the services manually (below).

> No systemd / prefer to manage services yourself? The old manual path:
> copy the three units from [`deploy/`](deploy/) to
> `/etc/systemd/system/` (adjust `User=`/paths), then
> `sudo systemctl daemon-reload && sudo systemctl enable --now kahya-web kahya-bot kahya-scheduler`.

## Run (three processes, any supervisor)

```bash
python3 -m kahya.scheduler   # reminders — spawns agents on due windows
python3 -m kahya.bot         # Telegram front door
python3 -m kahya.server      # admin panel → http://<host>:8080
```

`python3 -m kahya.scheduler --dry-run` checks what *would* be sent today
without touching Telegram. On the Pi, prefer the installer's systemd step
(or the units in [`deploy/`](deploy/)) over cron + `nohup`.

## First run

1. Open the admin panel (`http://<host>:8080`), sign in with
   **admin / kahya123** — the banner nags you until you change it in
   Settings.
2. Settings → LLM: model + endpoint (Ollama or cloud API), hit
   **Test connection** — this system model powers Kahya only; each
   agent gets its own model from the **Agents** tab.
3. Settings → Telegram: bot token + your chat id, hit **Send test**.
4. Settings → General: panel language (Turkish / English), timezone.
5. Say `/start` to your bot. Done.

Panel tabs (v2): **Overview** (counts + upcoming scheduled tasks),
**Agents** (CRUD + per-agent model + optional schema editor), **Records**
(schema table view or raw JSON, search, add/edit/delete), **Approvals**
(decide pending approvals — approved ones are forwarded to the agent),
**MCP Servers** (Smithery catalog search, manual stdio/http servers,
tool filters, binding agents — the agent's YAML gets its `mcp:` block
written automatically, `amele explain` preview, liability notice before
the first bind), **Settings** (+ Smithery API key, DB backup &
conversation history downloads).

**MCP note:** binding a third-party server means running third-party
code — the panel shows a liability notice that must be accepted before
any server can be added or bound. Data stays on your device; hosted
servers route requests through the provider. OAuth servers
(`auth: oauth`) are logged in from the terminal (`amele mcp login`), the
panel shows the exact command and the credential status.

Every setting lives in the SQLite store and applies **immediately** —
no restarts; if the bot token changes, the bot reconnects on its own.
Forgot the panel password? `KAHYA_ADMIN_PASSWORD` in `.env` always wins.

## Talk to Kâhya

Natural language in, both ways:

- **Record** — "Water bill 3000 TL arrived, due 19 august",
  "Cat Pamuk's rabies shot on september 3", "Remind me of rent on the 20th
  of every month".
  Missing facts are asked one at a time; nothing is saved without your
  explicit confirmation (human in the loop).
- **Ask** — "When was the rabies shot?", "Which bills are due this month?"
  The orchestrator agent ([`agents/kahya.yaml`](agents/kahya.yaml)) reads
  the store and answers from real data.
- **Complete** — reply "paid" to a reminder; repeating records roll to
  the next period automatically.

### Admin commands (mirror of the panel, from Telegram)

```
/agents            list agents
/add-agent         create agent — wizard: name → slug → job → confirm
/edit-agent        edit agent name or job description (wizard)
/delete-agent      delete agent (confirmation required)
/jobs              list open tasks
/add-job           add a task (wizard)
/done              complete the task in the reminder window
/settings          setup summary
/help              this list
/cancel            cancel a running flow
```

## How it works

| Piece | What it does | Lives |
|---|---|---|
| `agents/extract.yaml` | natural language → intent (record/question) + validated JSON | amele agent |
| `agents/kahya.yaml` | the orchestrator — answers questions from the store | amele agent |
| `agents/reminder.yaml` | general reminder delivery | amele agent |
| `agents/fatura.yaml`, `agents/pets.yaml` | example owner-defined agents — the shape every panel-created agent gets | amele agents |
| `tools/db_get.py`, `tools/db_put.py` | read / write the SQLite store (agents can never touch `agents`/`reminders` tables) | agent tools |
| `tools/telegram_send.py` | deliver a message to the owner | agent tool |
| `kahya/scheduler.py` | every 60 s: items inside their reminder window → spawn owning agent → one reminder per item per day | core |
| `kahya/bot.py` | Telegram long-polling: records, questions, admin commands, confirmation flows | core |
| `kahya/server.py` + `web/` | authenticated admin panel: agents, tasks, settings, guide, history, license | core |
| `lang/tr.json`, `lang/en.json` | UI + bot translations — add a language by copying one | i18n |

### Reminder windows

A bill due on the 20th with `remind_before_days: 2` is reminded on the 18th,
19th and 20th. Overdue items keep getting one reminder per day until you mark
them done — reply **paid** in the bot (or press *complete* in the panel).
Repeating items (monthly `20`, yearly `08-19`, weekly `monday`, daily) roll
their due date forward automatically when completed.

### The confirmation flow (human in the loop)

Extraction is never silently trusted: the bot shows a confirmation card
(*"📋 Save this?"*) and saves only after **yes**. Missing facts are
asked one at a time. Nothing external happens without a human step — bills,
payments and dates are confirmed before they enter the store.

## Security

- Panel auth: PBKDF2-hashed password (default admin/kahya123, change it),
  session cookies, brute-force lockout (5 failed attempts → 5 min),
  env fallback `KAHYA_ADMIN_PASSWORD`.
- Telegram bot only serves the configured chat id — strangers get a polite
  "wrong household".
- Agents read the store through a whitelist; `db_put` allows inserts/updates
  on `items` only, never on agents, reminders or settings.

## Data

Everything lives in one SQLite file, `data/kahya.db`. Backup = copy the file
(or the panel's "Download DB backup" button). No account, no cloud, no
telemetry. (Telegram messages transit Telegram's servers — that is the one
place your data leaves the house; everything else stays local. A cloud LLM
sees what you send it; a local Ollama changes nothing.)

## The bigger picture

Kâhya is a demonstration of the *organization* idea: amele gives you agents
that behave like programs; Kâhya is the layer on top that lets you **build a
small organization out of them** — name a role, write its job description,
get a versioned agent file that spawns when its work is due. The web panel is
a visual *company builder*; the YAML files are the company itself.

## License

Dual license — see [LICENSE](LICENSE): **free for personal use** (MIT-style
terms); **commercial use requires a paid license** from
[Loxigo Software](https://www.loxigo.com). Built on
[amele](https://github.com/lasthumanintheloop/amele) (MIT).
