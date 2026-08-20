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
   Telegram bot → amele (extract) → follow-up question
        │
"August 19" → confirmation card → "yes"
        │
        ▼
   SQLite · reminders armed → amele (reminder) spawns on the 17th/18th/19th
```

> **50 configured ameles, 0 running.** Kâhya is idle until there is work:
> every reminder, every extraction, every answer is an amele that
> spawns, does its job, and exits. The organization exists as files; workers
> exist only while working.

---

## Why amele

Kâhya is deliberately thin: its core (scheduler, bot, web panel, SQLite) is a
small always-on layer, and **every AI step is an amele** — a single YAML
file in [`ameles/`](ameles/). That buys three things:

- **Any LLM, local or cloud.** Point `BASE_URL` at a local Ollama
  (`http://host:11434/v1`) or at OpenAI/OpenRouter — the ameles don't care.
  Your data stays on your hardware.
- **Organization as code.** Creating an amele from the web panel or from
  Telegram writes `ameles/<slug>.yaml`. Version it, diff it, review it in a
  PR, share it. A PR is literally "what changed in my company".
- **A well-mannered runtime.** Schema-guaranteed JSON output (`output.schema`
  in [`ameles/extract-amele.yaml`](ameles/extract-amele.yaml)), meaningful
  exit codes, one JSONL log per run — all amele contracts.

## Requirements

- Python 3.9+ (stdlib only — no pip installs; verified on 3.9 and 3.12)
- The amele binary (MCP-capable; fetched by the installer from
  [`loxigosoftware/amele-builds`](https://github.com/loxigosoftware/amele-builds),
  SHA256-verified)
- A Telegram bot token (from @BotFather) and your chat id
- An LLM endpoint: local Ollama or any OpenAI-compatible API
  (development-tested with **Qwen3 27B** locally — see *Model strategy*)

## Install

**One command (any platform — Linux, macOS, Windows, Raspberry Pi):**

```bash
git clone https://github.com/loxigosoftware/kahya
cd kahya
python3 install.py
```

The installer **scans your machine first** — Python, the amele binary
(**MCP rule: no MCP, no amele** — an MCP-less binary is rejected outright),
`.env`, Node.js (MCP stdio servers), ffmpeg, leftover temp files — then
shows an **automatic proposal list** and asks for **your approval item by
item**. Nothing is installed, replaced or removed without it. The web panel
port is picked automatically (8080, next free if busy); systemd auto-start
is a single `[y/N]` question at the end.

Useful flags: `--dry-run` (show the proposal list, change nothing),
`--yes` (approve everything explicitly), `--force` (re-install amele).
On Linux with systemd, the final question installs units for the three
services (`Restart=on-failure` — a crash is brought back automatically,
and everything comes up on boot; needs sudo). Skip it and run the
services manually (below).

> No systemd / prefer to manage services yourself? The old manual path:
> copy the three units from [`deploy/`](deploy/) to
> `/etc/systemd/system/` (adjust `User=`/paths), then
> `sudo systemctl daemon-reload && sudo systemctl enable --now kahya-web kahya-bot kahya-scheduler`.

## Run (three processes, any supervisor)

```bash
python3 -m kahya.scheduler   # reminders — spawns ameles on due windows
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
   amele gets its own model from the **Ameles** tab.
3. Settings → Telegram: bot token + your chat id, hit **Send test**.
4. Settings → General: panel language (Turkish / English), timezone.
5. Say `/start` to your bot. Done.

Panel tabs (v2): **Overview** (counts + upcoming scheduled tasks),
**Ameles** (CRUD + per-amele model + optional schema editor), **Records**
(schema table view or raw JSON, search, add/edit/delete), **Approvals**
(decide pending approvals — approved ones are forwarded to the amele),
**MCP Servers** (Smithery catalog search, manual stdio/http servers,
tool filters, binding ameles — the amele's YAML gets its `mcp:` block
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

## Model strategy

Every amele picks its own model (REDESIGN §2.4):

- **`model_kind: local`** → local endpoint (Ollama / custom), e.g.
  `qwen3:27b` for general text work or `qwen3-vl:8b` for vision ameles.
- **`model_kind: api`** → external provider; the key is referenced as
  `${VAR}` from the environment, never stored in plain text.

The LLM setting in **Settings** powers **Kahya (the orchestrator) only**;
all other ameles use their own assignment from the Ameles tab.
Development is tested with **Qwen3 27B locally** — a capable default for
the orchestrator on a home server.

## Talk to Kâhya

Natural language in, both ways:

- **Record** — "Water bill 3000 TL arrived, due 19 august",
  "Cat Pamuk's rabies shot on september 3", "Remind me of rent on the 20th
  of every month".
  Missing facts are asked one at a time; nothing is saved without your
  explicit confirmation (human in the loop).
- **Ask** — "When was the rabies shot?", "Which bills are due this month?"
  The orchestrator amele ([`ameles/kahya.yaml`](ameles/kahya.yaml))
  reads the store and answers from real data.
- **Complete** — reply "paid" to a reminder; repeating records roll to
  the next period automatically.

### Bot commands (v2)

```
/amele            list ameles
/help             this list
/iptal            cancel the current chat session
/<slug>           talk to that amele directly (e.g. /mail-amele mailleri oku)
                  — no arguments: chat mode until /iptal
```

Any message without a `/` command is answered by Kahya, who either answers
itself, asks a question, or forwards the job to the right amele
("… send to amele x? yes / no").

## How it works

| Piece | What it does | Lives |
|---|---|---|
| `ameles/extract-amele.yaml` | natural language → intent (record/question) + validated JSON | amele |
| `ameles/kahya.yaml` | the orchestrator — answers questions from the store | amele |
| `ameles/reminder-amele.yaml` | general reminder delivery | amele |
| `ameles/invoice-amele.yaml`, `ameles/pets-amele.yaml`, … | example owner-defined ameles — the shape every panel-created amele gets | ameles |
| `tools/db_get.py`, `tools/db_put.py` | read / write the SQLite store (ameles can never touch `ameles`/`settings` tables) | amele tools |
| `tools/telegram_send.py` | deliver a message to the owner | amele tool |
| `kahya/scheduler.py` | every 60 s: records inside their reminder window → spawn owning amele → one reminder per record per day | core |
| `kahya/bot.py` | Telegram long-polling: records, questions, approvals, chat sessions | core |
| `kahya/server.py` + `web/` | authenticated admin panel: ameles, records, approvals, MCP, settings | core |
| `lang/tr.json`, `lang/en.json` | UI + bot translations — "amele" is never translated (proper noun) | i18n |

### Reminder windows

A bill due on the 20th with `remind_before_days: 2` is reminded on the 18th,
19th and 20th. Overdue records keep getting one reminder per day until you
mark them done — reply **paid** in the bot (or press *complete* in the
panel). Repeating records (monthly `20`, yearly `08-19`, weekly `monday`,
daily) roll their due date forward automatically when completed.

### The confirmation flow (human in the loop)

Extraction is never silently trusted: the bot shows a confirmation card
(*"📋 Save this?"*) and saves only after **yes**. Missing facts are
asked one at a time. Nothing external happens without a human step — bills,
payments and dates are confirmed before they enter the store. Ameles may
also request approvals during a job (pending_actions); the approval prompt
names the amele, and the answer is matched to the most recent pending
action.

## Security

- Panel auth: PBKDF2-hashed password (default admin/kahya123, change it),
  session cookies, brute-force lockout (5 failed attempts → 5 min),
  env fallback `KAHYA_ADMIN_PASSWORD`.
- Telegram bot only serves the configured chat id — strangers get a polite
  "wrong household".
- Ameles read the store through a whitelist; `db_put` allows inserts/updates
  on records only, never on ameles, reminders or settings.

## Data & backups

Everything lives in one SQLite file, `data/kahya.db`. No account, no cloud,
no telemetry. (Telegram messages transit Telegram's servers — that is the
one place your data leaves the house; everything else stays local. A cloud
LLM sees what you send it; a local Ollama changes nothing.)

Backups (two buttons in the panel, or `scripts/backup.sh`):
- **Download DB** — `data/kahya.db` snapshot
- **Download History** — conversation archive dump (conversation_messages)

Restore = put the DB file back in place; the migration is one-way
(see `scripts/migrate_v2.py`) — back up before any upgrade.

## The bigger picture

Kâhya is a demonstration of the *organization* idea: amele gives you workers
that behave like programs; Kâhya is the layer on top that lets you **build a
small organization out of them** — name a role, write its job description,
get a versioned amele file that spawns when its work is due. The web panel
is a visual *company builder*; the YAML files are the company itself.

## License

Dual license — see [LICENSE](LICENSE): **free for personal use** (MIT-style
terms); **commercial use requires a paid license** from
[Loxigo Software](https://www.loxigo.com). Built on
[amele](https://github.com/lasthumanintheloop/amele) (MIT); MCP-capable
binaries are published by us at
[loxigosoftware/amele-builds](https://github.com/loxigosoftware/amele-builds).
