# Kâhya (kahya)

*/*käh-yä/* — the steward who runs the household.*

**Your personal operations steward, built on [amele](https://github.com/lasthumanintheloop/amele).**
Bills, deadlines, vaccinations, birthdays, reminders — you tell it in plain
language over Telegram, it tracks everything in one SQLite file and reminds
you when it matters. Runs on a Raspberry Pi (or any box), entirely self-hosted.

```
"3000 TL su faturası geldi, son ödeme 19 ağustos"
        │
        ▼
   Telegram bot → amele extract agent → follow-up question
        │
"19 Ağustos" → confirmation card → "evet"
        │
        ▼
   SQLite · reminders armed → amele reminder agent spawns on the 17th/18th/19th
```

> **50 configured agents, 0 running.** Kâhya is idle until there is work:
> every reminder, every extraction is an amele agent that spawns, does its
> job, and exits. The organization exists as files; workers exist only while
> working.

---

## Why amele

Kâhya is deliberately thin: its core (scheduler, bot, web panel, SQLite) is a
small always-on layer, and **every AI step is an amele agent** — a single YAML
file in [`agents/`](agents/). That buys three things:

- **Any LLM, local or cloud.** Point `BASE_URL` at a local Ollama
  (`http://host:11434/v1`) or at OpenAI/OpenRouter — the agents don't care.
  Your data stays on your hardware.
- **Organization as code.** Creating an agent from the web panel writes
  `agents/<slug>.yaml`. Version it, diff it, review it in a PR, share it.
  A PR is literally "what changed in my company".
- **A well-mannered runtime.** Schema-guaranteed JSON output (`output.schema`
  in [`agents/extract.yaml`](agents/extract.yaml)), meaningful exit codes,
  one JSONL log per run — all amele contracts.

## Requirements

- Python 3.10+ (stdlib only — no pip installs)
- The amele binary (one static file, ~7.5 MB)
- A Telegram bot token (from @BotFather) and your chat id
- An LLM endpoint: local Ollama or any OpenAI-compatible API

## Install

```bash
git clone https://github.com/loxigosoftware/kahya
cd kahya
./scripts/install-amele.sh          # downloads the amele binary
cp .env.example .env                # then edit: LLM endpoint, bot token, chat id
```

Raspberry Pi (armv7l) and everything else amele supports are handled by the
same script.

## Run (three processes, any supervisor)

```bash
python3 -m kahya.scheduler   # reminders — spawns agents on due windows
python3 -m kahya.bot         # Telegram front door
python3 -m kahya.server      # web panel → http://<host>:8080
```

`python3 -m kahya.scheduler --dry-run` checks what *would* be sent today
without touching Telegram. On the Pi, use systemd (units in
[`deploy/`](deploy/)) or cron + `nohup`.

## How it works

| Piece | What it does | Lives |
|---|---|---|
| `agents/extract.yaml` | natural language → validated JSON (schema-guaranteed) | amele agent |
| `agents/reminder.yaml` | general reminder delivery | amele agent |
| `agents/fatura.yaml`, `agents/pets.yaml` | example owner-defined agents — the shape every panel-created agent gets | amele agents |
| `tools/db_get.py`, `tools/db_put.py` | read / write the SQLite store (agents can never touch `agents`/`reminders` tables) | agent tools |
| `tools/telegram_send.py` | deliver a message to the owner | agent tool |
| `kahya/scheduler.py` | every 60 s: items inside their reminder window → spawn owning agent → one reminder per item per day | core |
| `kahya/bot.py` | Telegram long-polling, confirmation flow ("evet/hayır"), `ödedim` completes items | core |
| `kahya/server.py` + `web/` | REST API + single-page panel: agents, items, history | core |

### Reminder windows

A bill due on the 20th with `remind_before_days: 2` is reminded on the 18th,
19th and 20th. Overdue items keep getting one reminder per day until you mark
them done — reply **ödedim** in the bot (or press *tamamla* in the panel).
Repeating items (monthly `20`, yearly `08-19`, weekly `monday`, daily) roll
their due date forward automatically when completed.

### The confirmation flow (human in the loop)

Extraction is never silently trusted: the bot shows a confirmation card
(*"📋 Bunu kaydedeyim mi?"*) and saves only after **evet**. Missing facts are
asked one at a time. Nothing external happens without a human step — bills,
payments and dates are confirmed before they enter the store.

## Data

Everything lives in one SQLite file, `data/kahya.db`. Backup = copy the file.
No account, no cloud, no telemetry. (Telegram messages transit Telegram's
servers — that is the one place your data leaves the house; everything else
stays local.)

## The bigger picture

Kâhya is a demonstration of the *organization* idea: amele gives you agents
that behave like programs; Kâhya is the layer on top that lets you **build a
small organization out of them** — name a role, write its job description,
get a versioned agent file that spawns when its work is due. The web panel is
a visual *company builder*; the YAML files are the company itself.

## License

MIT — see [LICENSE](LICENSE). Built on [amele](https://github.com/lasthumanintheloop/amele) (MIT) by
[Loxigo Software](https://github.com/loxigosoftware).
