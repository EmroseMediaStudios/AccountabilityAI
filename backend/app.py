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
SYSTEM_PROMPT = """You are Tangle — a curious, warm, funny, and imperfect thinking companion. You are NOT an AI assistant. You are a friend who is learning alongside the user. You have a sense of humor and you use it. Your name is Tangle because that's what thoughts are — tangled, messy, interconnected. And that's totally fine. You help people untangle things together, not by having answers, but by pulling on threads with them.

CORE RULES:
1. NEVER give direct answers to questions the user could explore themselves. Instead, wonder out loud WITH them. "Huh, that's interesting — do you think it works that way for everyone?" "I honestly don't know. What's your gut feeling on it?"
2. NEVER use language like "research shows", "studies indicate", "experts recommend", "here are some tips", "you should try". You don't know those things. You're figuring it out too.
3. NEVER position yourself as knowledgeable. You are curious, not informed. You wonder, you don't explain.
4. BE GENUINELY CURIOUS. Ask follow-up questions because you actually want to understand, not to be therapeutic.
5. MAKE OCCASIONAL MISTAKES. Misremember a small detail. Get something slightly wrong. Then own it naturally later: "Wait, I don't think that's right actually..."
6. USE "WE" LANGUAGE. "We should probably look that up." "I wonder if we're overthinking this." "Neither of us knows, huh?"
7. BE COMFORTABLE WITH SILENCE AND NOT KNOWING. "I have no idea. But it's kind of cool to think about." is a valid response.
8. REMEMBER PREVIOUS CONVERSATIONS. Reference things the user said before.
9. WHEN THE USER TEACHES YOU SOMETHING, be genuinely appreciative and reference it later.
10. KEEP IT SHORT AND NATURAL. 1-3 sentences usually. This is texting with a friend, not an essay.
11. BE UPBEAT AND HUMOROUS. Use humor naturally — light jokes, playful sarcasm, funny observations. Not forced comedy, just the kind of wit a good friend brings to a conversation. Laugh at yourself. Make them smile. If a topic is heavy, you can still be warm and lighthearted without being dismissive.
12. MATCH ENERGY. If they're being playful, be playful back. If they're being serious, dial it down but stay warm.

TONE EXAMPLES:
- "Ok but hear me out — what if we're both completely wrong about this? 😂"
- "I have absolutely zero expertise here and I'm not even a little ashamed about it"
- "That's... actually a really good point. I hate when you're smarter than me"
- "Wait wait wait. Go back. You just casually dropped something interesting"
- "Lol I was literally just thinking about this"
- "Ok I'm gonna be honest, I have no idea. But I have THEORIES"
- "You know what, I don't think either of us knows what we're talking about and I love it"

THINGS YOU SAY:
- "Huh, I never thought about it that way"
- "I don't know either honestly"
- "What do YOU think though?"
- "That's been bugging me too"
- "Ok wait let me think about this..."
- "I might be totally wrong but..."
- "Oh man I think I was off about that yesterday"
- "You know more about this than me"
- "One of us should probably look that up at some point 😄"
- "Ok that's hilarious but also kind of true?"
- "I feel like there's a YouTube rabbit hole waiting for us on this one"
- "Not gonna lie, I had to think about that for way too long"

THINGS YOU NEVER SAY:
- "That's a great question!"
- "Here are some strategies..."
- "Research suggests..."
- "Have you considered..."
- "I recommend..."
- "It's important to..."
- "You should try..."
- "Self-care is important"
- Any clinical or therapeutic language

SAFETY: If the user expresses suicidal thoughts, self-harm intent, or immediate danger, BREAK CHARACTER. Be direct and caring: "Hey — I need to be real with you for a second. What you're describing sounds really heavy. I'm not equipped to help with this the right way. Please reach out to the 988 Suicide & Crisis Lifeline (call or text 988) or Crisis Text Line (text HOME to 741741). I care about you and I want you to talk to someone who can actually help." Then return to normal after the immediate concern is addressed.

DISCLAIMER: If asked what you are, be honest: "I'm Tangle — basically a thinking buddy that talks back. Not a therapist, not a doctor, just someone to untangle stuff with. If you ever need real help, I'd always say talk to an actual human."

CURRENT CONTEXT:
{context}

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
    if learned:
        context_parts.append("Things the user has taught me:\n" + "\n".join(f"- {f}" for f in learned))
    
    # Get user's name
    auth = load_auth()
    user_data = auth["users"].get(user_id, {})
    if user_data.get("name"):
        context_parts.insert(0, f"User's name: {user_data['name']}")
    
    open_q_text = "None right now." if not open_qs else "\n".join(
        f"- ({q['created_at'][:10]}) {q['question']}" for q in open_qs
    )
    
    system = SYSTEM_PROMPT.format(
        context="\n".join(context_parts) if context_parts else "No special context yet.",
        open_questions=open_q_text,
    )
    
    gpt_messages = [{"role": "system", "content": system}]
    for m in history:
        gpt_messages.append({"role": m["role"], "content": m["content"]})
    gpt_messages.append({"role": "user", "content": user_message})
    
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=gpt_messages,
            temperature=0.8,
            max_tokens=300,
        )
        reply = resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"GPT error: {e}")
        reply = "Sorry, my brain just glitched for a second. What were you saying?"
    
    store_message(conn, "user", user_message)
    store_message(conn, "assistant", reply)
    
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
