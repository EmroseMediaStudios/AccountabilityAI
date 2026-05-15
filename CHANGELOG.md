# Tangle AI — Changelog

## 2026-05-15

### Personality & Intelligence Overhaul (bb2b173)
- **System prompt rewritten** — Tangle now shares knowledge freely instead of deflecting to "what do you think?" Leads with substance, has opinions, uses "we" language, kills cheerleader openers ("Great question!"), and only ends ~30% of responses with questions
- **Post-response extraction** — After every exchange, GPT-4o-mini extracts open questions and learned facts from the conversation. Populates `open_questions` and `learned_facts` tables for long-term memory and nightly follow-ups
- **Depth escalation** — Detects "elaborate" / "explain more" / "go deeper" keywords and increases max_tokens from 600→800
- **Temperature tuned** — 0.8→0.75 for better factual balance while keeping personality
- **Drew's data backfilled** — Extracted 17 facts and 12 open questions from existing conversation history

### Search & Delivery (5508e87)
- **DuckDuckGo search** — Replaced Brave API with free DuckDuckGo Lite scraping. Zero cost, no API key, no account needed
- **Smart proactive delivery** — Only delivers ONE pending message per check. Won't stack follow-ups (if last message was from Tangle, holds back). Respects hours: 8am–10pm local time only. Research runs at 2AM, messages drip out when user is active

### Frontend: Image Upload, Emoji Picker, Session Handling (de69ec9)
- **Image upload** — File picker (📷 button), drag & drop onto chat, clipboard paste (Ctrl/Cmd+V). Preview strip before sending with cancel button. 10MB max, JPEG/PNG/GIF/WebP
- **GPT-4o vision** — Images sent with messages route to GPT-4o instead of GPT-4o-mini, so Tangle can see and discuss what you share
- **Image rendering in chat** — Inline thumbnails in message history, click to open lightbox fullscreen view
- **Emoji picker** — 5 tabbed categories (~120 emoji), closes on outside click
- **Session expiry** — All API calls check for expired tokens (7-day TTL). Auto-redirects to login screen and clears stale credentials
- **auth_check hardened** — Now enforces the same 7-day token expiry as require_auth (was previously unchecked)
- **Mobile keyboard** — visualViewport resize auto-scrolls messages to bottom

### UI Polish (30765db)
- **Header branded** — "Tangle" → "Tangle AI" with superscript AI badge
- **Avatar upgraded** — Larger (44px header, 96px welcome), gradient ring border, glowing status dots with colored shadows
- **Welcome screen** — Bigger heading, new tagline, 4 clickable conversation starter chips that auto-send on click
- **Message bubbles** — User bubbles get deeper purple gradient, bot bubbles get purple-tinted glass effect instead of flat white
- **Disclaimer** — Compact, cleaner copy

### Open Registration (f84612a)
- **Invite codes removed** — Anyone can register with just username, password, and display name. No gate, no invite code field

### Already Built (prior session, in codebase)
- **Auto-migration** — `kid_psychotic` username maps to Drew's legacy account (`user_0564a7a2bd53e6b9`, 64 messages, 17 facts). When Drew registers with username `Kid_Psychotic`, all his conversation data migrates automatically
- **Admin endpoints** — `/api/admin/users` (list), `/api/admin/migrate` (manual migration), `/api/admin/set-admin` (promote user)
- **Admin user** — `nehumanescrede` (Wickman) is admin with full password auth
- **Character state system** — available/away/sleeping with human-like away messages and auto-return after 8 minutes
- **Nightly research cron** — 6:00 UTC daily, processes unresolved questions, DuckDuckGo search, stores follow-ups for delivery
- **Per-user SQLite databases** — Isolated conversation history, open questions, learned facts, character state, user profile per user
- **Cloudflare Tunnel** — HTTPS access via tunnel

---

## 2026-05-14

### Initial Build (5c950c7)
- Flask backend with GPT-4o-mini conversation engine
- Invite code auth system (later removed)
- Per-user SQLite databases
- Character state system (available/away/sleeping)
- Nightly research cron job
- Mobile-friendly chat UI (light theme)
- Cloudflare Tunnel + Supervisor processes
- CHARACTER_DESIGN.md documentation
