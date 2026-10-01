# Telegram AI Receptionist

A Telegram bot for a (fictional) clinic. It answers FAQs, finds free slots, books appointments after explicit confirmation and hands the chat to a human operator, in Russian, Uzbek or English. Portfolio demo 2 of 3; shared rules and environment notes are in `../CLAUDE.md`.

The target architecture is in `docs/ARCHITECTURE.md`. Read it before changing the conversation design, tools or data model, and record every deviation there as a decision (section 11).

## Stack

Python 3.13 (uv), aiogram 3, `anthropic` SDK, SQLAlchemy 2 (async) + asyncpg, Alembic, PostgreSQL 17, pydantic-settings, PyYAML, pytest, ruff, Docker Compose.

## Layout

```
src/receptionist/  config.py · core/ (agent, tools, prompts, ports) · telegram/ · db/ · __main__.py
alembic/           migrations
data/clinic.yaml   fictional clinic profile
evals/             scenarios/*.yaml · run.py · results/
tests/
```

## Commands (PowerShell, from the repo root)

| Task | Command |
|---|---|
| Install deps | `uv sync` |
| Database only (dev) | `wsl -d Ubuntu -- docker compose up -d db` |
| Migrate | `uv run alembic upgrade head` |
| Run bot (dev, long polling) | `uv run python -m receptionist` |
| Full stack | `wsl -d Ubuntu -- docker compose up -d --build` |
| Lint / format | `uv run ruff check .` · `uv run ruff format .` |
| Tests | `uv run pytest` |
| Eval (real API, costs money) | `uv run python -m evals.run` |

## Repo rules

- `core/` never imports aiogram or SQLAlchemy; it talks to the outside through `core/ports.py`.
- Create the Claude client only through the factory, which passes `api_key` and `base_url` explicitly. Never rely on `ANTHROPIC_*` environment variables (see `../CLAUDE.md`, Headroom).
- History is append-only:
  - store assistant content blocks verbatim (thinking included) and replay them unchanged;
  - never trim, edit or reorder past messages;
  - never change the system prompt or tool list inside a session;
  - to shrink context, rotate the session with a summary (ARCHITECTURE §4).
- Tools are `strict: true` and `tool_choice` is `auto`. Never send forced `tool_choice` (400 on Opus 5.5).
- `create_booking` must refuse unless `confirmed` is true and the slot is free; a unit test covers both cases.
- Handle `stop_reason == "refusal"` before reading content. Cap the loop at 6 iterations.
- Unit tests use a scripted fake Claude client (responses with thinking, tool_use and text blocks); only `evals/` calls the real API.
- No medical advice in prompts or replies; the emergency number comes from settings.

## Build plan

Each step is one commit; tick it off in Status.

1. **Scaffold:** uv project, settings, compose (`bot` + `db`), `.env.example`, CI. *Accept:* pytest passes; the bot answers `/start` with a static text.
2. **Clinic data and DB:** `clinic.yaml`, models, migrations, slot generator (pure function). *Accept:* slot tests pass (working hours, durations, existing bookings, past times excluded).
3. **Tools:** schemas and handlers with repositories. *Accept:* handler tests pass, including the booking confirmation gate and a double-booking race.
4. **Agent core:** loop with append-only persistence, session rotation, refusal handling, iteration cap. *Accept:* tests with the fake client prove that history is replayed byte-identical and tool results are paired by id.
5. **Telegram adapter:** routers, language detection, typing indicator, rate limit, `/forget`. *Accept:* manual chat in RU, UZ and EN; a booking is made end to end.
6. **Handoff and admin group:** operator mode, reply relay, "Take over" and "Return to bot" buttons, booking notifications. *Accept:* a full handoff round trip works manually.
7. **Evals:** scripted scenarios. *Accept:* the targets in ARCHITECTURE §9 are met; results are saved.
8. **README:** problem, demo video, diagram, quickstart (BotFather token, admin group id), eval table, extensions; script for a 60–90 s video.

## Status

- [x] Target architecture and CLAUDE.md (2026-10-01)
- [ ] 1 Scaffold
- [ ] 2 Clinic data and DB
- [ ] 3 Tools
- [ ] 4 Agent core
- [ ] 5 Telegram adapter
- [ ] 6 Handoff and admin group
- [ ] 7 Evals
- [ ] 8 README and video
