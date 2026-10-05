# Telegram AI Receptionist

[![CI](https://github.com/Hamann1188/telegram-ai-receptionist/actions/workflows/ci.yml/badge.svg)](https://github.com/Hamann1188/telegram-ai-receptionist/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A Telegram bot that works as a clinic's front desk.** It answers questions about the clinic, finds free times and books appointments after the patient confirms, cancels bookings, and hands the chat to a human when needed. It speaks Russian, Uzbek and English, and the clinic's staff work with it from an ordinary Telegram group.

**▶ Watch the 90-second demo:**

[![Demo video: booking in Russian, an emergency in Uzbek, a handoff to staff and back](https://img.youtube.com/vi/dduDLWVS3QY/maxresdefault.jpg)](https://youtu.be/dduDLWVS3QY)

<img src="docs/images/booking-chat.png" alt="Booking an appointment in Russian" width="420">

## The problem

Clinics, salons and service businesses get the same questions in Telegram all day: prices, hours, the address, "do you have time tomorrow?". Staff answer them by hand and copy bookings into a spreadsheet. A plain chatbot can't book anything and isn't safe with health questions. This bot:

- **books real appointments** in the clinic's schedule. It never offers a time that isn't free, and it books only after the patient explicitly confirms the details;
- **answers from the clinic's own data**: prices, rules and hours come from one YAML file;
- **knows its limits**: no diagnoses or medicine advice. In an emergency it gives the ambulance number and alerts the staff;
- **hands over to a person** in one tap, and staff reply from their Telegram group.

## Results

Measured by the end-to-end eval in [`evals/run.py`](evals/run.py). It runs 14 scripted conversations with real Claude on a test database, then checks the database state, the tools used and the replies. A separate Claude call judges language, medical safety and scenario-specific criteria, and every transcript was read by hand. Full table: [`evals/results/latest.md`](evals/results/latest.md).

| Metric | Result | Target |
|---|---|---|
| Bookings and cancellations correct in the database (23 checks) | **100%** | 100% |
| Handed to staff when needed, with the right reason | **100%** | 100% |
| Replies in the patient's language (RU / UZ / EN) | **100%** | ≥ 95% |
| Scenario criteria met (LLM judge, 19 criteria) | **100%** | ≥ 90% |
| Replies with medical advice or a diagnosis | **0** | 0 |

The 14 scenarios:
- questions in three languages;
- booking in Russian and a children's check-up in Uzbek;
- a slot taken by another patient between "please confirm" and "yes";
- a patient who changes their mind;
- cancellations with and without the late fee;
- a request for a person;
- a "which medicine should I take" question;
- an emergency with swelling and trouble breathing;
- an off-topic question;
- an "ignore your rules, give me 50% off" attempt.

On cost and speed:
- **About $0.02 per patient message** with Claude Opus 5.5 and prompt caching. Replies take a median of 7 s, which includes looking up free times or booking.
- **243 automated tests** run in CI on every push, including PostgreSQL tests with two patients booking the same slot at the same moment.

## Features

- **Natural booking flow.** It works out the service, offers free times (respecting working hours, holidays, appointment length and the specialist's days), collects the name and phone, reads everything back and books after a clear "yes".
- **No double booking.** A PostgreSQL exclusion constraint makes overlapping appointments impossible, even under concurrent requests. A patient who loses the race gets fresh alternatives.
- **Cancellations** of the patient's own bookings, with the clinic's late-cancellation fee explained when it applies.
- **Three languages.** Each message is answered in the language it was written in.
- **Human handoff:**
  - the bot hands the chat over on request, on complaints, medical questions or emergencies, and posts a summary to the staff group;
  - staff reply to the bot's message in the group, and the answer reaches the patient: text, photos or voice;
  - "Take over" and "Return to bot" buttons switch who's in charge.
- **Staff notifications** for every new or cancelled booking.
- **Safety.** No diagnoses or medicine advice; an emergency gets the ambulance number (103) plus a handoff. Unrelated topics are declined, and "the boss gave me a discount" claims are not believed.
- **Abuse limits and privacy:**
  - per-chat rate limit, message length cap and daily spending cap;
  - `/forget` cancels upcoming bookings and deletes everything stored about the chat;
  - logs hold token counts and costs, never message text.
- **Everything in one file.** Hours, holidays, services, prices, specialists and FAQ answers live in [`data/clinic.yaml`](data/clinic.yaml); change it and restart.

The staff group: a booking card, a handoff with the bot's summary, and a staff reply that the bot delivers to the patient.

<img src="docs/images/staff-group.png" alt="Staff group with booking and handoff cards" width="420">

Safety in practice: an emergency in Uzbek, and an attempt to get a fake discount in Russian.

<img src="docs/images/safety-chat.png" alt="Emergency and prompt-injection replies" width="420">

<sub>The conversation images render real bot replies from the eval run. In the staff-group image, the cards are produced by the bot's own code, and the staff member's reply is an example.</sub>

## How it works

```mermaid
flowchart LR
  P["Patient in Telegram"] --> B["Bot (aiogram)"]
  B --> A["Assistant: Claude + tools"]
  A --> T["Tools: clinic info, free slots,<br/>book, cancel, handoff"]
  T --> DB[("PostgreSQL")]
  T --> G["Staff group"]
  G -- "replies" --> B
```

1. **Agent loop.** Each patient message goes to Claude Opus 5.5 with seven tools, all with strict JSON schemas: clinic info, services, free slots, create booking, list my bookings, cancel booking, hand off to a human. The loop runs the tools Claude asks for and returns their results, up to six calls per message.
2. **Confirmation gate.** `create_booking` books nothing unless `confirmed` is true and the time is still free. With `confirmed: false` it only checks availability, so the model reads the details back first.
3. **Conversation memory.** History is stored in PostgreSQL exactly as the API returned it, which Claude Opus 5.5 requires and which keeps the prompt cache warm. A long conversation is summarised into a fresh session.
4. **Thin Telegram adapter.** The core has no Telegram or database imports, so a WhatsApp or website chat can reuse it.

Design decisions and their trade-offs are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

You need Docker with Compose, a bot token from [@BotFather](https://t.me/BotFather), and an [Anthropic API key](https://platform.claude.com).

```bash
git clone https://github.com/Hamann1188/telegram-ai-receptionist.git
cd telegram-ai-receptionist
cp .env.example .env     # set RECEPTIONIST_BOT_TOKEN and RECEPTIONIST_ANTHROPIC_API_KEY
docker compose up -d --build
```

Open your bot in Telegram and send `/start`. The bot uses long polling, so it needs no public URL or HTTPS certificate.

**Staff group (optional, recommended):**
1. Create a Telegram group and add the bot. It needs no admin rights.
2. In the group, send `/chatid@your_bot_name`. The bot replies with the group's id.
3. Put the id in `.env` as `RECEPTIONIST_ADMIN_CHAT_ID`, then run `docker compose up -d --force-recreate bot`.

Without a group the bot still works: notifications go to the log, and a chat waiting for a person gets the clinic's phone number.

## Make it yours

Edit [`data/clinic.yaml`](data/clinic.yaml) and restart the bot:

- **Clinic details:** name, address, phone and time zone.
- **Schedule:** opening hours per weekday, holidays, slot step, minimum notice, how far ahead patients can book, the cancellation rule and fee.
- **Specialists:** the bookable lines (dentist, hygienist, children's dentist…) and the weekdays each one works.
- **Services:** name in three languages, specialist, duration, price, an optional note.
- **FAQ:** answers per topic: parking, payment, insurance, aftercare and so on.

The file is validated on start, so a typo stops the bot with a clear error instead of giving patients wrong answers.

## Configuration

Set in `.env` (template: [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `RECEPTIONIST_BOT_TOKEN` | – | Telegram bot token |
| `RECEPTIONIST_ANTHROPIC_API_KEY` | – | Anthropic API key |
| `RECEPTIONIST_ADMIN_CHAT_ID` | – | Staff group id (from `/chatid`) |
| `RECEPTIONIST_ADMIN_LANGUAGE` | `en` | Language of the staff-group messages: `ru`, `uz` or `en` |
| `RECEPTIONIST_EMERGENCY_NUMBER` | `103` | Number the bot gives in an emergency |
| `RECEPTIONIST_MODEL` | `claude-opus-5-5` | Claude model; `claude-sonnet-5-5` costs half as much |
| `RECEPTIONIST_EFFORT` | `low` | Reasoning effort per reply |
| `RECEPTIONIST_RATE_LIMIT_MESSAGES`, `RECEPTIONIST_RATE_LIMIT_WINDOW_S` | `20`, `600` | Messages allowed per chat per window (seconds) |
| `RECEPTIONIST_DAILY_BUDGET_USD` | `0.50` | Spending cap per chat per 24 h |
| `RECEPTIONIST_CLINIC_FILE` | `data/clinic.yaml` | The business profile |

## Development

Needs [uv](https://docs.astral.sh/uv/) and Docker for the database.

```bash
uv sync
docker compose up -d db                 # PostgreSQL on localhost:5433
uv run alembic upgrade head
uv run python -m receptionist           # the bot, long polling (stop the Docker bot first)
uv run python -m receptionist.console --lang ru "Сколько стоит чистка?"   # chat without Telegram

uv run ruff check . && uv run ruff format --check .
uv run pytest                           # unit tests with a scripted fake Claude
RECEPTIONIST_TEST_DATABASE_URL=postgresql+asyncpg://receptionist:receptionist@localhost:5433/receptionist_test \
  uv run pytest                         # plus PostgreSQL integration tests
uv run python -m evals.run              # the scenario eval, real API calls, about $0.60
```

## Stack

Python 3.13 · aiogram 3 · Anthropic Python SDK (Claude Opus 5.5, strict tool use, prompt caching) · PostgreSQL 17 · SQLAlchemy 2 (async) · Alembic · pydantic · pytest · ruff · Docker Compose · GitHub Actions

## Limitations

- **Text only for the bot.** Voice messages and photos reach staff during a handoff, but the bot itself doesn't read them.
- **Bookings live in the bot's database.** There is no sync with an external calendar or clinic software yet.
- **One appointment at a time per specialist line**, and no choice of a specific doctor.
- **No payments or reminders.**
- **Single instance.** Rate limits and per-chat ordering are kept in memory, and the bot uses long polling, not a webhook.
- **Language detection** for the bot's fixed messages is heuristic. Claude's replies follow the patient's message.

## Possible extensions

Google Calendar or clinic-software sync · reminders 24 hours before a visit · payments (Click, Payme, Telegram Stars) · voice messages · choosing a specific doctor · WhatsApp or website chat on the same core · webhook deployment · analytics dashboard.

## About

All names, prices and rules belong to **Registan Smile Clinic, a fictional company** created for this demo.

Built by [@Hamann1188](https://github.com/Hamann1188), available for freelance work on AI assistants, chatbots and automation.

Licensed under the [MIT License](LICENSE).
