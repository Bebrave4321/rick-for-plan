# Study Assistant MVP

Telegram-based study schedule assistant built with FastAPI, APScheduler, SQLAlchemy, and OpenAI-backed intent/planning adapters.

## What is implemented

- FastAPI app with health and admin endpoints
- Telegram webhook endpoint and outgoing message adapter
- Async SQLAlchemy models for users, weekly plans, tasks, responses, changes, and daily conversation state
- APScheduler jobs for:
  - due-task scans
  - 7:00 daily summaries
  - Sunday 19:00 weekly planning prompts
- Weekly planning flow with:
  - OpenAI planning path
  - heuristic fallback planner when no API key is configured
- Task lifecycle support for:
  - prep reminder
  - check-in
  - one recheck after 10 minutes
  - hourly progress check for long sessions
  - completion prompt
  - reschedule / cancel actions
- Rule-based natural-language fallback for common Korean status updates

## Project layout

```text
src/study_assistant/
  api/            FastAPI routes and dependencies
  core/           settings
  db/             SQLAlchemy engine/session
  models/         ORM entities
  repositories/   persistence helpers
  schemas/        Pydantic contracts
  services/       planning, Telegram, OpenAI, orchestration
  main.py         app factory and scheduler startup
```

## Quick start

1. Create a virtual environment.
2. Install dependencies:

```bash
python -m pip install -e .[dev]
```

3. Copy `.env.example` to `.env` and fill in the values you have.

4. Start the API:

```bash
uvicorn study_assistant.main:app --reload --app-dir src
```

## Recommended environment variables

```env
DATABASE_URL=sqlite+aiosqlite:///./study_assistant.db
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
DATA_RETENTION_WEEKS=1
```

If `OPENAI_API_KEY` is missing, the app still works with the heuristic planner and rule-based message interpreter.

## Core API endpoints

- `GET /health`
- `POST /api/users/bootstrap`
- `POST /api/plans/weekly`
- `POST /api/plans/{plan_id}/confirm`
- `GET /api/users/{telegram_user_id}/dashboard`
- `POST /api/tasks/run-due-scan`
- `POST /api/jobs/daily-summary`
- `POST /api/jobs/weekly-prompt`
- `POST /api/jobs/prune-history`
- `POST /api/telegram/webhook`

## Example user bootstrap

```json
POST /api/users/bootstrap
{
  "telegram_user_id": 123456789,
  "telegram_chat_id": 123456789,
  "display_name": "LG"
}
```

## Example weekly plan submission

```json
POST /api/plans/weekly
{
  "telegram_user_id": 123456789,
  "planning_request": {
    "week_start_date": "2026-03-30",
    "unavailable_blocks": [
      {
        "day_of_week": "monday",
        "start_time": "13:00:00",
        "end_time": "18:00:00",
        "label": "classes"
      }
    ],
    "goals": [
      {
        "title": "English reading",
        "topic": "vocabulary",
        "target_hours": 3,
        "priority": 4,
        "preferred_session_minutes": 90
      },
      {
        "title": "Calculus problem set",
        "target_hours": 4,
        "priority": 5,
        "deadline": "2026-04-03"
      }
    ],
    "deadlines": [],
    "busy_days": [
      {
        "date": "2026-04-02",
        "note": "midterm prep elsewhere",
        "max_study_minutes": 90
      }
    ]
  }
}
```

## Telegram notes

- Set your webhook to `https://YOUR_DOMAIN/api/telegram/webhook`.
- If you use `TELEGRAM_WEBHOOK_SECRET`, Telegram must send the same secret in `X-Telegram-Bot-Api-Secret-Token`.
- If the bot token is missing, outbound Telegram messages are logged instead of sent.

## Render deploy

Files added for Render:

- `render.yaml`
- `.python-version`

Recommended first deploy settings:

- Runtime: Python
- Build command: `pip install -e .`
- Start command: `python -m uvicorn study_assistant.main:app --host 0.0.0.0 --port $PORT --app-dir src`
- Health check path: `/health`

Required Render environment values:

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `BASE_URL`

The included `render.yaml` also provisions a free Render Postgres database and maps its connection string to `DATABASE_URL`.

Important limitation:

- A free Render web service spins down after inactivity, so scheduled reminders and morning summaries are not reliable on the free tier.
- For real always-on assistant behavior, upgrade the web service to a paid instance.

## Railway deploy

Files added for Railway:

- `railway.toml`

Recommended first deploy settings:

- Mount a volume at `/data`
- Set `DATABASE_URL=sqlite+aiosqlite:////data/study_assistant.db`
- Keep `DATA_RETENTION_WEEKS=1` if you only want the current week of task history

Required Railway environment values:

- `TELEGRAM_BOT_TOKEN`
- `OPENAI_API_KEY`
- `BASE_URL`
- `DATABASE_URL`

The app now includes a daily `00:30` prune job and an on-demand `POST /api/jobs/prune-history` endpoint. With `DATA_RETENTION_WEEKS=1`, old tasks, conversations, and plan drafts from prior weeks are removed automatically, which keeps the SQLite volume small for a single-user bot.

## Current limitations

- Natural-language weekly intake is not yet fully structured through Telegram alone; the HTTP planning endpoint is the most reliable input path.
- Conversation persistence is wired around daily conversation records plus OpenAI conversation IDs, but production-hardening around retries and migration tooling is still needed.
- Database migrations are not set up yet; tables are created automatically at startup.

## Verification

```bash
python -m compileall src
pytest
```
