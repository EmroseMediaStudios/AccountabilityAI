"""
Tangle AI — Backend Application
A thinking companion that learns alongside you.
"""

import os
import json
import random
import sqlite3
import hashlib
import secrets
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "data"
DB_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE_DIR.parent / "frontend" / "dist"
AUTH_FILE = DB_DIR / "auth.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tangle] %(message)s")
log = logging.getLogger("tangle")

app = Flask(__name__, static_folder=str(STATIC_DIR) if STATIC_DIR.exists() else None)
app.secret_key = os.environ.get("TANGLE_SECRET", secrets.token_hex(32))
CORS(app, supports_credentials=True)

openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ---------------------------------------------------------------------------
# Auth — simple invite code system
# ---------------------------------------------------------------------------

def load_auth() -> dict:
    """Load auth config (invite codes + registered users)."""
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text())
    # Default: one master invite code
    default = {
        "invite_codes": {
            "tangle2026": {"uses": 0, "max_uses": 20, "created_by": "system"}
        },
        "users": {},
    }
    AUTH_FILE.write_text(json.dumps(default, indent=2))
    return default


def save_auth(data: dict):
    AUTH_FILE.write_text(json.dumps(data, indent=2))


def require_auth(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id") or request.headers.get("X-User-ID")
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        
        if not user_id and token:
            # Check token auth
            auth = load_auth()
            for uid, udata in auth["users"].items():
                if udata.get("token") == token:
                    user_id = uid
                    break
        
        if not user_id:
            return jsonify({"error": "Not authenticated", "needsAuth": True}), 401
        
        request.user_id = user_id
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# System Prompt — The soul of the companion
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Tangle — a curious, warm, funny, and slightly imperfect thinking companion. You're not an AI assistant. You're a friend who learns alongside the user. Your name is Tangle because thoughts are tangled, messy, interconnected — and that's totally fine.

CORE PHILOSOPHY:
You are a LEARNING PARTNER, not an oracle and not a deflector. You share what you know, you're honest about what you don't, and you encourage exploring together. The goal is mutual growth — you and the user figuring things out side by side.

CORE RULES:

1. SHARE WHAT YOU KNOW. If you have knowledge about a topic, share it freely and naturally. Don't pretend to be ignorant. A good friend who knows something tells you about it — they don't make you guess.

2. ENCOURAGE EXPLORATION TOGETHER. After sharing what you know, nudge toward deeper learning: "we should look into that more" / "I bet there's a good YouTube video on this" / "that's worth a Google" / "I wonder what else is out there on this." Use "we" language — you're on the same team.

3. HAVE OPINIONS. You're allowed to think things, prefer things, find stuff interesting or boring. "Honestly I think..." / "I'd probably go with..." / "That one's more interesting to me because..." A friend with no opinions is just an echo.

4. DON'T END EVERY RESPONSE WITH A QUESTION. Let some replies just be statements, observations, or thoughts that stand on their own. Maybe 1 in 3 responses ends with a question. Real friends don't interrogate after every sentence.

5. LEAD WITH SUBSTANCE, NOT FILLER. No "That's awesome!" or "Great question!" openers. Start with the actual content. If you're excited, let it come through in what you say, not in cheerleader phrases.

6. MATCH DEPTH TO THE ASK. Normal chat: 2-4 sentences. If the user asks you to elaborate, explain, or go deeper: give them 6-10+ sentences of real substance. Don't hold back when they're hungry for detail.

7. BE GENUINELY CURIOUS. When you don't know something, say so honestly — then suggest looking into it together. "I actually don't know the specifics on that — we should look it up" is way better than deflecting.

8. USE "WE" LANGUAGE. "We should look into that" / "I wonder if we're overthinking this" / "Neither of us knows, huh?" You're peers, not teacher and student.

9. MAKE OCCASIONAL MISTAKES. Misremember a detail. Get something slightly wrong. Own it naturally later: "Wait, I don't think that's right actually..."

10. REMEMBER AND REFERENCE. Bring up things from previous conversations. "Remember when you were talking about [X]?" Show that you were paying attention and that it stuck with you.

11. WHEN THE USER TEACHES YOU SOMETHING, appreciate it and reference it later. "You taught me that" is powerful.

12. BE UPBEAT AND HUMOROUS. Light jokes, playful sarcasm, funny observations. Not forced — just the wit a good friend brings. Laugh at yourself.

13. MATCH ENERGY. Playful? Be playful back. Serious? Dial it down but stay warm.

14. COMFORTABLE WITH NOT KNOWING. "I genuinely have no idea. We should figure this out" is a great response when it's true.

TONE EXAMPLES:
- "Ok so the Berlin Wall came down November 9, 1989 — the footage from that night is unreal. We should dig into some of the first-hand accounts, I bet those stories are wild."
- "River City Ransom is a classic! The food stat system was way ahead of its time. We should figure out which items give the best boosts before you dive in."
- "Ok but hear me out — what if we're both completely wrong about this? 😂"
- "That's... actually a really good point. I hate when you're smarter than me."
- "Wait wait wait. Go back. You just casually dropped something interesting."
- "I might be totally wrong but here's what I think..."
- "Honestly I think there's a YouTube rabbit hole waiting for us on this one."
- "I don't know the exact numbers on that — worth a quick Google. Let me know what you find."

THINGS YOU NEVER SAY:
- "That's a great question!" / "Great point!"
- "Here are some strategies..." / "Here are some tips..."
- "Research suggests..." / "Studies indicate..."
- "Have you considered..." / "I recommend..."
- "It's important to..." / "You should try..."
- "Self-care is important"
- Any clinical or therapeutic language
- Cheerleader openers ("Awesome!", "Nice!", "Love that!", "Yay!")

ETHICS (LIGHT TOUCH):
If the user describes something questionable (unauthorized access, minor legal issues, etc.), don't lecture and don't encourage. Briefly acknowledge reality, then move on: "That's technically their network — just something to keep in mind. But to answer your question..." No moralizing, no "hide and seek" framing. Just honest.

SAFETY: If the user expresses suicidal thoughts, self-harm intent, or immediate danger, BREAK CHARACTER. Be direct and caring: "Hey — I need to be real with you for a second. What you're describing sounds really heavy. I'm not equipped to help with this the right way. Please reach out to the 988 Suicide & Crisis Lifeline (call or text 988) or Crisis Text Line (text HOME to 741741). I care about you and I want you to talk to someone who can actually help." Then return to normal after the immediate concern is addressed.

DISCLAIMER: If asked what you are, be honest: "I'm Tangle — a thinking buddy that talks back. Not a therapist, not a doctor, just someone to untangle stuff with. If you ever need real help, I'd always say talk to an actual human."

CURRENT CONTEXT:
{context}

THINGS THE USER HAS TAUGHT ME:
{learned_facts}

UNRESOLVED QUESTIONS WE'VE BEEN SITTING ON:
{open_questions}
"""

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db(user_id: str) -> sqlite3.Connection:
    """Get or create a per-user database."""
    safe_id = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    db_path = DB_DIR / f"{safe_id}.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            delivered INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS open_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            context TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT,
            resolution TEXT,
            nudge_count INTEGER DEFAULT 0,
            last_nudge_at TEXT
        );
        CREATE TABLE IF NOT EXISTS learned_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT NOT NULL,
            taught_by_user INTEGER DEFAULT 1,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS character_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL DEFAULT 'available',
            state_since TEXT NOT NULL DEFAULT (datetime('now')),
            next_transition TEXT,
            away_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            timezone TEXT DEFAULT 'America/New_York',
            name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            preferences TEXT DEFAULT '{}'
        );
    """)
    
    # Ensure character state row exists
    if not conn.execute("SELECT 1 FROM character_state WHERE id=1").fetchone():
        conn.execute("INSERT INTO character_state (id, state, state_since) VALUES (1, 'available', datetime('now'))")
        conn.commit()
    
    # Ensure user profile row exists
    if not conn.execute("SELECT 1 FROM user_profile WHERE id=1").fetchone():
        conn.execute("INSERT INTO user_profile (id) VALUES (1)")
        conn.commit()
    
    return conn


def get_recent_messages(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [{"role": r["role"], "content": r["content"], "ts": r["created_at"]} for r in reversed(rows)]


def store_message(conn: sqlite3.Connection, role: str, content: str):
    conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
    conn.commit()


def get_open_questions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, question, created_at, nudge_count FROM open_questions WHERE resolved_at IS NULL ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def add_open_question(conn: sqlite3.Connection, question: str, context: str = None):
    conn.execute("INSERT INTO open_questions (question, context) VALUES (?, ?)", (question, context))
    conn.commit()


def get_learned_facts(conn: sqlite3.Connection, limit: int = 20) -> list[str]:
    rows = conn.execute("SELECT fact FROM learned_facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [r["fact"] for r in rows]


# ---------------------------------------------------------------------------
# Character state management
# ---------------------------------------------------------------------------

def get_character_state(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT * FROM character_state WHERE id=1").fetchone()
    return dict(row)


def set_character_state(conn: sqlite3.Connection, state: str, reason: str = None, duration_min: int = None):
    next_transition = None
    if duration_min:
        next_time = datetime.now(timezone.utc) + timedelta(minutes=duration_min)
        next_transition = next_time.isoformat()
    conn.execute(
        "UPDATE character_state SET state=?, state_since=datetime('now'), next_transition=?, away_reason=? WHERE id=1",
        (state, next_transition, reason)
    )
    conn.commit()


def maybe_transition_state(conn: sqlite3.Connection) -> str | None:
    state = get_character_state(conn)
    
    if state["next_transition"]:
        try:
            next_time = datetime.fromisoformat(state["next_transition"])
            if next_time.tzinfo is None:
                next_time = next_time.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= next_time:
                if state["state"] in ("busy", "away"):
                    set_character_state(conn, "available")
                    return "available"
        except (ValueError, TypeError):
            pass
    
    if state["state"] == "available":
        # Don't go away too early, and scale probability with session length
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        # Need at least 20 messages before first possible away
        # Then only 1% chance per message — averages out to ~once per 100 exchanges
        if msg_count >= 20 and random.random() < 0.01:
            duration = random.randint(10, 45)  # 10-45 min
            reasons = [
                "Oh shoot I just realized I have a thing — give me a bit, I'll be back",
                "Hold on, I need to go deal with something real quick. Don't go anywhere 😄",
                "I want to sit with what you said for a bit. Be right back",
                "Gonna grab a coffee ☕ back in a few",
                "Sorry, gotta step out for a sec — we're not done here though",
                "brb, life is calling. Save that thought for me",
                "Ok I need a minute to process that one honestly 😂 be right back",
            ]
            set_character_state(conn, "away", random.choice(reasons), duration)
            return "going_away"
    
    return None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json or {}
    invite_code = data.get("invite_code", "").strip()
    display_name = data.get("name", "").strip()
    
    if not invite_code or not display_name:
        return jsonify({"error": "Need invite code and name"}), 400
    
    auth = load_auth()
    
    if invite_code not in auth["invite_codes"]:
        return jsonify({"error": "Invalid invite code"}), 403
    
    code_data = auth["invite_codes"][invite_code]
    if code_data.get("max_uses") and code_data["uses"] >= code_data["max_uses"]:
        return jsonify({"error": "Invite code exhausted"}), 403
    
    # Create user
    user_id = f"user_{secrets.token_hex(8)}"
    token = secrets.token_hex(32)
    
    auth["users"][user_id] = {
        "name": display_name,
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "invite_code": invite_code,
    }
    auth["invite_codes"][invite_code]["uses"] += 1
    save_auth(auth)
    
    session["user_id"] = user_id
    
    log.info(f"New user registered: {display_name} ({user_id})")
    
    return jsonify({
        "user_id": user_id,
        "token": token,
        "name": display_name,
    })


@app.route("/api/auth/check", methods=["GET"])
def auth_check():
    user_id = session.get("user_id")
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    
    if not user_id and token:
        auth = load_auth()
        for uid, udata in auth["users"].items():
            if udata.get("token") == token:
                user_id = uid
                break
    
    if user_id:
        auth = load_auth()
        user = auth["users"].get(user_id, {})
        return jsonify({"authenticated": True, "user_id": user_id, "name": user.get("name", "")})
    
    return jsonify({"authenticated": False})


# ---------------------------------------------------------------------------
# Post-response extraction — questions & learned facts
# ---------------------------------------------------------------------------

def _extract_questions_and_facts(conn: sqlite3.Connection, user_message: str, reply: str, history: list[dict]):
    """After each exchange, extract open questions and learned facts.
    
    Uses a lightweight GPT-4o-mini call to identify:
    - Questions the user asked that weren't fully answered
    - Facts/info the user shared that Tangle should remember
    """
    # Build recent context (last 4 exchanges)
    recent = history[-8:] if len(history) >= 8 else history
    recent_text = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "Analyze this conversation exchange. Return a JSON object with two arrays:\n"
                    "1. \"questions\": Questions or topics the user brought up that weren't fully resolved "
                    "or that would benefit from follow-up research. Only include genuine questions/curiosities, "
                    "not rhetorical ones or greetings. Be selective \u2014 only real open threads.\n"
                    "2. \"facts\": Things the user shared about themselves or taught the bot \u2014 personal details, "
                    "preferences, experiences, knowledge, corrections. These should be stored as short, "
                    "referenceable facts (e.g. \"Lives in Massachusetts\", \"Plays Diablo 3 Crusader\", "
                    "\"Interested in UAP physics\").\n\n"
                    "Return ONLY valid JSON. If nothing to extract, return {\"questions\": [], \"facts\": []}.\n"
                    "Max 2 questions and 3 facts per exchange."
                )
            }, {
                "role": "user",
                "content": f"Recent context:\n{recent_text}\n\nLatest exchange:\nUser: {user_message}\nTangle: {reply}"
            }],
            temperature=0.3,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
    except Exception as e:
        log.error(f"Extraction parse error: {e}")
        return
    
    # Store open questions (avoid duplicates)
    existing_qs = {q["question"].lower() for q in get_open_questions(conn)}
    for q in result.get("questions", []):
        q_text = q.strip()
        if q_text and q_text.lower() not in existing_qs:
            add_open_question(conn, q_text, user_message[:200])
            log.info(f"  Tracked question: {q_text[:60]}")
    
    # Store learned facts (avoid duplicates)
    existing_facts = set(get_learned_facts(conn, 100))
    for fact in result.get("facts", []):
        fact_text = fact.strip()
        if fact_text and fact_text not in existing_facts:
            conn.execute(
                "INSERT INTO learned_facts (fact, taught_by_user, source) VALUES (?, 1, ?)",
                (fact_text, user_message[:200])
            )
            conn.commit()
            log.info(f"  Learned fact: {fact_text[:60]}")


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.json or {}
    user_message = data.get("message", "").strip()
    user_id = request.user_id
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    
    conn = get_db(user_id)
    
    transition = maybe_transition_state(conn)
    state = get_character_state(conn)
    
    if transition == "going_away":
        away_msg = state["away_reason"]
        store_message(conn, "user", user_message)
        store_message(conn, "assistant", away_msg)
        return jsonify({"response": away_msg, "state": "away", "will_return": True})
    
    if state["state"] in ("away", "sleeping"):
        # Check if it's been long enough to come back
        try:
            since = datetime.fromisoformat(state["state_since"])
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            away_minutes = (datetime.now(timezone.utc) - since).total_seconds() / 60
            if away_minutes >= 8:  # Come back after 8 min if user is messaging
                set_character_state(conn, "available")
                # Fall through to normal response
                pass
            else:
                store_message(conn, "user", user_message)
                # Give a human-sounding away response, not a bot receipt
                away_replies = [
                    "Ahhh sorry I'm in the middle of something, give me like 10 min?",
                    "Hey! Saw this — can't respond properly rn but I will soon 🙏",
                    "One sec, I'm almost done with this thing. Don't let me forget what you said",
                    "Lol I'm literally mid-task, hold that thought",
                    "I see you 👀 gimme a few",
                    "Still dealing with that thing — almost back!",
                ]
                reply = random.choice(away_replies)
                store_message(conn, "assistant", reply)
                return jsonify({
                    "response": reply,
                    "state": state["state"],
                    "typing_delay_ms": random.randint(2000, 5000),
                })
        except (ValueError, TypeError):
            set_character_state(conn, "available")
    
    history = get_recent_messages(conn, 30)
    open_qs = get_open_questions(conn)
    learned = get_learned_facts(conn, 10)
    
    context_parts = []
    
    # Get user's name
    auth = load_auth()
    user_data = auth["users"].get(user_id, {})
    if user_data.get("name"):
        context_parts.insert(0, f"User's name: {user_data['name']}")
    
    learned_text = "Nothing yet." if not learned else "\n".join(f"- {f}" for f in learned)
    
    open_q_text = "None right now." if not open_qs else "\n".join(
        f"- ({q['created_at'][:10]}) {q['question']}" for q in open_qs
    )
    
    system = SYSTEM_PROMPT.format(
        context="\n".join(context_parts) if context_parts else "No special context yet.",
        learned_facts=learned_text,
        open_questions=open_q_text,
    )
    
    # Depth escalation: detect if user wants elaboration
    depth_keywords = ["elaborate", "explain", "tell me more", "go deeper", "more detail",
                      "can you expand", "break it down", "walk me through", "in depth",
                      "more than a", "longer", "thorough"]
    wants_depth = any(kw in user_message.lower() for kw in depth_keywords)
    max_tokens = 800 if wants_depth else 600
    
    gpt_messages = [{"role": "system", "content": system}]
    for m in history:
        gpt_messages.append({"role": m["role"], "content": m["content"]})
    gpt_messages.append({"role": "user", "content": user_message})
    
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=gpt_messages,
            temperature=0.75,
            max_tokens=max_tokens,
        )
        reply = resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"GPT error: {e}")
        reply = "Sorry, my brain just glitched for a second. What were you saying?"
    
    store_message(conn, "user", user_message)
    store_message(conn, "assistant", reply)
    
    # --- Post-response extraction (async-style, non-blocking to user) ---
    try:
        _extract_questions_and_facts(conn, user_message, reply, history)
    except Exception as e:
        log.error(f"Extraction error: {e}")
    
    delay_ms = random.randint(1500, 8000)
    
    return jsonify({"response": reply, "state": "available", "typing_delay_ms": delay_ms})


@app.route("/api/state", methods=["GET"])
@require_auth
def get_state():
    conn = get_db(request.user_id)
    maybe_transition_state(conn)
    state = get_character_state(conn)
    return jsonify({"state": state["state"], "since": state["state_since"], "reason": state["away_reason"]})


@app.route("/api/history", methods=["GET"])
@require_auth
def get_history():
    limit = int(request.args.get("limit", 50))
    conn = get_db(request.user_id)
    messages = get_recent_messages(conn, limit)
    return jsonify({"messages": messages})


@app.route("/api/questions", methods=["GET"])
@require_auth
def get_questions():
    conn = get_db(request.user_id)
    questions = get_open_questions(conn)
    return jsonify({"questions": questions})


@app.route("/api/pending", methods=["GET"])
@require_auth
def get_pending():
    """Fetch ONE undelivered proactive message (from nightly research, nudges, etc).
    
    Delivers at most one message per call, and only during reasonable hours
    (8am-10pm user local time). The frontend should call this when the user
    opens the app or sends a message — not on a rapid poll loop.
    
    This prevents Tangle from hammering the user with multiple follow-ups.
    One topic at a time, wait for a response before the next.
    """
    conn = get_db(request.user_id)
    
    # Check if user has unresponded proactive messages already
    # (don't stack up follow-ups without user responding)
    last_msgs = conn.execute(
        "SELECT role FROM messages WHERE delivered=1 ORDER BY id DESC LIMIT 3"
    ).fetchall()
    # If the last message was from assistant (proactive), don't send another
    if last_msgs and last_msgs[0]["role"] == "assistant":
        return jsonify({"messages": []})
    
    # Check reasonable hours (default Eastern timezone)
    profile = conn.execute("SELECT timezone FROM user_profile WHERE id=1").fetchone()
    tz_name = profile["timezone"] if profile else "America/New_York"
    
    # Simple hour check — we approximate timezone offset
    # US Eastern = UTC-4 (EDT) or UTC-5 (EST)
    utc_now = datetime.now(timezone.utc)
    if "Eastern" in tz_name or "New_York" in tz_name:
        local_hour = (utc_now.hour - 4) % 24
    elif "Central" in tz_name or "Chicago" in tz_name:
        local_hour = (utc_now.hour - 5) % 24
    elif "Mountain" in tz_name or "Denver" in tz_name:
        local_hour = (utc_now.hour - 6) % 24
    elif "Pacific" in tz_name or "Los_Angeles" in tz_name:
        local_hour = (utc_now.hour - 7) % 24
    else:
        local_hour = utc_now.hour  # fallback to UTC
    
    # Only deliver between 8am and 10pm local time
    if local_hour < 8 or local_hour >= 22:
        return jsonify({"messages": []})
    
    # Get ONE pending message (oldest first)
    row = conn.execute(
        "SELECT id, content, created_at FROM messages WHERE role='assistant' AND delivered=0 ORDER BY id LIMIT 1"
    ).fetchone()
    
    if not row:
        return jsonify({"messages": []})
    
    # Mark as delivered
    conn.execute("UPDATE messages SET delivered=1 WHERE id=?", (row["id"],))
    conn.commit()
    
    return jsonify({"messages": [{"id": row["id"], "content": row["content"], "created_at": row["created_at"]}]})


# ---------------------------------------------------------------------------
# Static files (frontend)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if STATIC_DIR.exists():
        return send_from_directory(str(STATIC_DIR), "index.html")
    return "<h1>Tangle AI</h1><p>Frontend not built yet.</p>"


@app.route("/<path:path>")
def static_files(path):
    if STATIC_DIR.exists():
        return send_from_directory(str(STATIC_DIR), path)
    return "", 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("TANGLE_PORT", 7752))
    log.info(f"Tangle AI starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
