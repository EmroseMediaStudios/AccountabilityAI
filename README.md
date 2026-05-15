# Tangle AI

A thinking companion that learns alongside you, not for you.

Tangle isn't a chatbot or an assistant — it's a friend who gets tangled up in ideas with you. It remembers what you've talked about, follows up on things you were curious about, and actually has opinions.

## Features

- **Genuine conversation** — Shares knowledge freely, has opinions, uses "we" language. No cheerleader openers, no deflection
- **Learns from you** — Extracts facts and open questions from every conversation. Remembers what you've taught it
- **Nightly research** — Follows up on unresolved questions while you sleep (DuckDuckGo search, GPT synthesis). Delivers one topic at a time when you're next active
- **Image sharing** — Upload, drag & drop, or paste images. GPT-4o vision so Tangle can see and discuss what you share
- **Character states** — Goes away, comes back, sleeps. Human-like presence with natural away messages
- **Per-user isolation** — Each user gets their own SQLite database. Conversations, facts, and questions are private
- **Smart delivery** — Proactive messages only during reasonable hours (8am–10pm local), never stacks follow-ups

## Tech Stack

- **Backend:** Python/Flask, GPT-4o-mini (text) / GPT-4o (vision), SQLite per-user
- **Frontend:** Vanilla HTML/CSS/JS (no build step), mobile-optimized
- **Search:** DuckDuckGo Lite (free, no API key)
- **Hosting:** Supervisor + Cloudflare Tunnel for HTTPS
- **Auth:** Username/password with 7-day token expiry

## Setup

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install flask flask-cors openai werkzeug
export OPENAI_API_KEY="sk-..."
python app.py  # starts on port 7752
```

Optional environment variables:
- `TANGLE_PORT` — server port (default: 7752)
- `TANGLE_SECRET` — Flask session secret
- `TANGLE_ADMIN_TOKEN` — bearer token for admin API access

## User Accounts

- Open registration — username, password, display name
- 7-day token expiry with auto-redirect to login
- Admin endpoints for user management (`/api/admin/*`)

### Legacy Migration

Legacy users (created with invite codes before the auth overhaul) are auto-migrated when they register with their mapped username. Migration copies messages, learned facts, and open questions to the new account.

Currently mapped: `kid_psychotic` → Drew's legacy account

## Project Structure

```
tangle-ai/
├── backend/
│   ├── app.py                 # Main Flask application (~1100 lines)
│   ├── nightly_research.py    # Cron job for follow-up research
│   └── data/                  # SQLite DBs, auth.json, uploads (gitignored)
├── frontend/
│   └── dist/
│       ├── index.html         # Single-file frontend (HTML + CSS + JS)
│       ├── avatar.png          # Tangle avatar
│       ├── banner-full.png     # Auth screen banner
│       └── favicon.png
├── docs/
│   └── CHARACTER_DESIGN.md    # Tangle's personality design doc
├── CHANGELOG.md
└── README.md
```

## Key Endpoints

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/register` | POST | — | Create account (username, password, name) |
| `/api/login` | POST | — | Login, returns bearer token |
| `/api/auth/check` | GET | token | Verify session (checks 7-day expiry) |
| `/api/chat` | POST | token | Send message (+ optional image_url) |
| `/api/upload` | POST | token | Upload image, returns URL |
| `/api/history` | GET | token | Get conversation history |
| `/api/state` | GET | token | Get character state |
| `/api/pending` | GET | token | Get one undelivered proactive message |
| `/api/questions` | GET | token | Get open questions |
| `/api/admin/users` | GET | admin | List all users |
| `/api/admin/migrate` | POST | admin | Manual user data migration |
| `/api/admin/set-admin` | POST | admin | Promote user to admin |

## Cron Jobs

- **Nightly research** — 6:00 UTC (2am Eastern) via system cron
  - Processes oldest unresolved question per user
  - DuckDuckGo search + GPT synthesis
  - Stores as undelivered message (delivered on next user activity)
