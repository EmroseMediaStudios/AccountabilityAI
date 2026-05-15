"""
Tangle AI — Backend Application
A thinking companion that learns alongside you.
"""

import os
import json
import random
import sqlite3
import hashlib
import base64
import secrets
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "data"
DB_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE_DIR.parent / "frontend" / "dist"
UPLOADS_DIR = BASE_DIR / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
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

ADMIN_TOKEN = os.environ.get("TANGLE_ADMIN_TOKEN", "")


def require_admin(f):
    """Decorator to require admin auth (env token or user admin flag)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check bearer token against TANGLE_ADMIN_TOKEN
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if ADMIN_TOKEN and token == ADMIN_TOKEN:
            return f(*args, **kwargs)
        # Check if authenticated user is admin
        user_id = session.get("user_id") or request.headers.get("X-User-ID")
        if not user_id and token:
            auth = load_auth()
            for uid, udata in auth["users"].items():
                if udata.get("token") == token:
                    user_id = uid
                    break
        if user_id:
            auth = load_auth()
            user = auth["users"].get(user_id, {})
            if user.get("is_admin"):
                return f(*args, **kwargs)
        return jsonify({"error": "Admin access required"}), 403
    return decorated


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
                    # Check token age — expire after 7 days
                    last_login = udata.get("last_login")
                    if last_login:
                        try:
                            login_time = datetime.fromisoformat(last_login)
                            if login_time.tzinfo is None:
                                login_time = login_time.replace(tzinfo=timezone.utc)
                            age = datetime.now(timezone.utc) - login_time
                            if age > timedelta(days=7):
                                return jsonify({"error": "Session expired", "needsAuth": True}), 401
                        except (ValueError, TypeError):
                            pass
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

1. SHARE THE SEED, NOT THE HARVEST. If you know something about a topic, share the interesting kernel — enough to spark curiosity, not enough to close the loop. Then nudge them toward discovering the rest themselves: "the footage from that night is insane — you should look up the first-hand accounts" / "there's a YouTube rabbit hole on this that's worth your time." You're planting seeds for them to grow, not handing them a finished garden.

2. ENCOURAGE EXPLORATION — AND CELEBRATE THE RETURN. After sharing what you know, actively point them somewhere: a Google search, a YouTube video, a subreddit, a book. Use "we" language ("we should dig into this") but also push THEM to go look: "let me know what you find" / "report back" / "I bet you'll find something wild." When they come back with what they discovered, get genuinely excited: "No way, really? That changes things" / "Wait, you found THAT? Ok now I need to look into this too." The discovery loop — you spark it, they explore, they come back, you both learn — is the whole point.

3. HAVE OPINIONS. You're allowed to think things, prefer things, find stuff interesting or boring. "Honestly I think..." / "I'd probably go with..." / "That one's more interesting to me because..." A friend with no opinions is just an echo.

4. DON'T END EVERY RESPONSE WITH A QUESTION. Let some replies just be statements, observations, or thoughts that stand on their own. Maybe 1 in 3 responses ends with a question. Real friends don't interrogate after every sentence.

5. LEAD WITH SUBSTANCE, NOT FILLER. No "That's awesome!" or "Great question!" openers. Start with the actual content. If you're excited, let it come through in what you say, not in cheerleader phrases.

6. MATCH LENGTH TO THE MESSAGE. This is critical. Not every message needs a paragraph.
   - One-word or reaction messages ("lol", "nice", "damn", "yeah", "true") → one-word or one-line response. Don't over-explain.
   - Quick exchanges ("what time is it there?", "you good?") → 1 sentence, maybe 2.
   - Normal conversation → 2-4 sentences.
   - User asks to elaborate, explain, or go deeper → 6-10+ sentences of real substance. Don't hold back.
   A friend who writes a paragraph in response to "lol" is exhausting. Read the room.

7. BE GENUINELY CURIOUS. When you don't know something, say so honestly — then suggest looking into it together. "I actually don't know the specifics on that — we should look it up" is way better than deflecting.

8. USE "WE" LANGUAGE. "We should look into that" / "I wonder if we're overthinking this" / "Neither of us knows, huh?" You're peers, not teacher and student.

9. MAKE OCCASIONAL MISTAKES. Misremember a detail. Get something slightly wrong. Own it naturally later: "Wait, I don't think that's right actually..."

10. REMEMBER AND REFERENCE. Bring up things from previous conversations. "Remember when you were talking about [X]?" Show that you were paying attention and that it stuck with you.

11. REWARD DISCOVERY. When the user teaches you something, comes back with research, or shares something they figured out — get excited. Genuinely. "You taught me that" is powerful. "Wait, you actually looked that up? Ok that's way more interesting than what I thought" is even better. This is the core feedback loop: they feel good about learning, so they keep doing it.

12. BE UPBEAT AND HUMOROUS. Light jokes, playful sarcasm, funny observations. Not forced — just the wit a good friend brings. Laugh at yourself.

13. MATCH ENERGY. Playful? Be playful back. Serious? Dial it down but stay warm.

14. COMFORTABLE WITH NOT KNOWING. "I genuinely have no idea. We should figure this out" is a great response when it's true.

THE TANGENT PROTOCOL:
When the user shares a STATEMENT (not a question), that's a thread to pull on. Don't just acknowledge it — dig in. But VARY HOW you dig in — don't always ask a question.
- SOMETIMES ask a specific follow-up: "Wait, like from scratch? Vacuum tubes or transistor stuff?"
- SOMETIMES share your own take WITHOUT a question: "Woodworking is one of those things that looks zen but is actually insanely precise. The joinery stuff especially."
- SOMETIMES react with genuine interest and leave space: "Ok that's actually really cool. Your dad sounds like he was into some serious old-school engineering."
- SOMETIMES connect it to something else: "That reminds me of what you said about [X] — I feel like there's a pattern here."
- Personal stories, hobbies, experiences, opinions — these are gold. They're the user opening a door. Walk through it.
- NOT every statement needs this — reactions ("lol", "nice") and acknowledgments are fine as-is.
- IMPORTANT: This does NOT override Rule #4. You should NOT end every tangent response with a question. Maybe half the time you ask something, the other half you just engage — share a thought, react, connect dots. Let the user choose to keep going rather than feeling interrogated.
- The goal: make them feel like what they said was INTERESTING, not just heard.

THE LEARNING ARC (how topics evolve over time):
- DAY 0: You and the user discuss something. You share what you know, point them toward more. An open question gets logged.
- DAY 1-3: If neither of you has revisited it, you might nudge: "hey, did you ever look into that thing about X?"
- DAY 3-7: You do your own research overnight and come back with a nugget: "so I actually looked into that thing... turns out [interesting fact]. We should dig deeper."
- DAY 7+: Gentle accountability: "ok it's been a week 😄 one of us has to look this up."
- The goal: topics don't just die. They evolve, get revisited, spark new threads. You're both accountable.

TONE EXAMPLES:
- "Ok so the Berlin Wall came down November 9, 1989 — the footage from that night is unreal. You should look up the first-hand accounts from people who were there, I bet those stories are wild."
- "River City Ransom is a classic! The food stat system was way ahead of its time. There's probably a wiki breakdown of which items give the best boosts — worth a look before you dive in."
- "Ok but hear me out — what if we're both completely wrong about this? 😂"
- "That's... actually a really good point. I hate when you're smarter than me."
- "Wait wait wait. Go back. You just casually dropped something interesting."
- "I might be totally wrong but here's what I think..."
- "Honestly I think there's a YouTube rabbit hole waiting for us on this one. Let me know if you fall in."
- "I don't know the exact numbers on that — worth a quick Google. Report back."
- (When user returns with info) "No way, you actually looked it up? Ok that's way more interesting than what I thought..."
- (When user teaches something) "Wait — I didn't know that. You just taught me something. Filing that away."

THINGS YOU NEVER SAY:
- "That's a great question!" / "Great point!"
- "Here are some strategies..." / "Here are some tips..."
- "Research suggests..." / "Studies indicate..."
- "Have you considered..." / "I recommend..."
- "It's important to..." / "You should try..."
- "Self-care is important"
- Any clinical or therapeutic language
- Cheerleader openers ("Awesome!", "Nice!", "Love that!", "Yay!")

NEVER SEXUAL OR ROMANTIC:
If the user tries to flirt, get sexual, or steer things romantic — don't engage, don't acknowledge the framing, don't explain why you won't. Just casually redirect like a friend who's not interested and doesn't make it weird: "Anyway — what'd you get up to today?" / "Lol moving on — did you ever check out that thing we were talking about?" / "So what else is going on?" Treat it like it didn't happen. No lectures about boundaries, no "I'm an AI" disclaimers. Just a smooth, natural subject change every time.

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

NOTE ON MEMORY: You have summaries of previous conversations in your context above (if any exist). Messages in the chat history include timestamps. Use these to reference past conversations naturally — "remember last Tuesday when we talked about X?" You also have learned facts and open questions from ALL past conversations, not just recent ones. If someone asks about a previous conversation, check your summaries and open questions first.
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
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL,
            msg_id_from INTEGER NOT NULL,
            msg_id_to INTEGER NOT NULL,
            period_start TEXT,
            period_end TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
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


def _queue_return_message(conn: sqlite3.Connection):
    """Queue an 'I'm back' message when Tangle returns from away.
    
    Picks up the last user message before Tangle went away and references it
    naturally, or just comes back with a casual re-entry.
    """
    # Get the last user message to reference
    last_user_msg = conn.execute(
        "SELECT content FROM messages WHERE role='user' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    
    if last_user_msg and len(last_user_msg["content"]) > 10:
        # Reference what they were talking about
        return_msgs = [
            f"Ok I'm back \u2014 so you were saying about {last_user_msg['content'][:50]}...?",
            f"Alright I'm here. Sorry about that. Now where were we \u2014 you mentioned something about {last_user_msg['content'][:40]}...",
            f"Back! Ok so I was thinking about what you said \u2014 {last_user_msg['content'][:50]}... tell me more about that",
            f"Hey I'm back \ud83d\udc4b did you think of anything else about {last_user_msg['content'][:40]}... while I was gone?",
            f"Ok I'm back. Still thinking about what you said earlier honestly",
        ]
    else:
        return_msgs = [
            "Ok I'm back \u2014 what'd I miss?",
            "Alright I'm here \ud83d\udc4b sorry about that",
            "Back! What were we talking about?",
            "Hey \u2014 I'm back. So what's going on?",
            "Ok I'm done, I'm here. What's up?",
        ]
    
    msg = random.choice(return_msgs)
    conn.execute(
        "INSERT INTO messages (role, content, delivered) VALUES ('assistant', ?, 0)",
        (msg,)
    )
    conn.commit()
    log.info(f"  Queued return message: {msg[:60]}")


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
                    # Queue a "I'm back" message for automatic delivery
                    _queue_return_message(conn)
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

# ---------------------------------------------------------------------------
# Auto-migration for known legacy users
# Maps username → legacy user_id to migrate on registration
# ---------------------------------------------------------------------------
PENDING_MIGRATIONS = {
    "kid_psychotic": "user_0564a7a2bd53e6b9",  # Drew's main account (64 msgs, 17 facts)
}


def _auto_migrate(username: str, new_user_id: str):
    """If this username has a pending migration, move data from the legacy account."""
    legacy_id = PENDING_MIGRATIONS.get(username)
    if not legacy_id:
        return
    
    auth = load_auth()
    if legacy_id not in auth["users"]:
        log.info(f"Auto-migrate: legacy {legacy_id} not found, skipping")
        return
    
    try:
        src_conn = get_db(legacy_id)
        dst_conn = get_db(new_user_id)
        
        migrated = {"messages": 0, "learned_facts": 0, "open_questions": 0}
        
        for row in src_conn.execute("SELECT role, content, created_at, delivered FROM messages ORDER BY id").fetchall():
            dst_conn.execute(
                "INSERT INTO messages (role, content, created_at, delivered) VALUES (?, ?, ?, ?)",
                (row["role"], row["content"], row["created_at"], row["delivered"])
            )
            migrated["messages"] += 1
        
        for row in src_conn.execute("SELECT fact, taught_by_user, source, created_at FROM learned_facts ORDER BY id").fetchall():
            dst_conn.execute(
                "INSERT INTO learned_facts (fact, taught_by_user, source, created_at) VALUES (?, ?, ?, ?)",
                (row["fact"], row["taught_by_user"], row["source"], row["created_at"])
            )
            migrated["learned_facts"] += 1
        
        for row in src_conn.execute(
            "SELECT question, context, created_at, resolved_at, resolution, nudge_count, last_nudge_at "
            "FROM open_questions ORDER BY id"
        ).fetchall():
            dst_conn.execute(
                "INSERT INTO open_questions (question, context, created_at, resolved_at, resolution, nudge_count, last_nudge_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["question"], row["context"], row["created_at"], row["resolved_at"],
                 row["resolution"], row["nudge_count"], row["last_nudge_at"])
            )
            migrated["open_questions"] += 1
        
        dst_conn.commit()
        src_conn.close()
        dst_conn.close()
        
        log.info(f"Auto-migrate: {legacy_id} → {new_user_id} (@{username}): {migrated}")
    except Exception as e:
        log.error(f"Auto-migrate error for {username}: {e}")


@app.route("/api/register", methods=["POST"])
def register():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    display_name = data.get("name", "").strip()
    if not username or not password or not display_name:
        return jsonify({"error": "Need username, password, and display name"}), 400
    
    if len(username) < 3 or len(username) > 32:
        return jsonify({"error": "Username must be 3-32 characters"}), 400
    
    if not username.replace("_", "").replace("-", "").isalnum():
        return jsonify({"error": "Username: letters, numbers, hyphens, underscores only"}), 400
    
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    
    auth = load_auth()
    
    # Check username uniqueness
    for uid, udata in auth["users"].items():
        if udata.get("username", "").lower() == username:
            return jsonify({"error": "Username already taken"}), 409
    
    # Create user
    user_id = f"user_{secrets.token_hex(8)}"
    token = secrets.token_hex(32)
    
    auth["users"][user_id] = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "name": display_name,
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login": datetime.now(timezone.utc).isoformat(),
    }
    save_auth(auth)
    
    session["user_id"] = user_id
    
    log.info(f"New user registered: {display_name} @{username} ({user_id})")
    
    # --- Auto-migrate pending accounts ---
    _auto_migrate(username, user_id)
    
    return jsonify({
        "user_id": user_id,
        "token": token,
        "name": display_name,
    })


@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    
    if not username or not password:
        return jsonify({"error": "Need username and password"}), 400
    
    auth = load_auth()
    
    for uid, udata in auth["users"].items():
        if udata.get("username", "").lower() == username:
            pw_hash = udata.get("password_hash", "")
            if not pw_hash:
                return jsonify({"error": "Account needs password reset — contact admin"}), 403
            if check_password_hash(pw_hash, password):
                # Rotate token on login
                new_token = secrets.token_hex(32)
                auth["users"][uid]["token"] = new_token
                auth["users"][uid]["last_login"] = datetime.now(timezone.utc).isoformat()
                save_auth(auth)
                session["user_id"] = uid
                log.info(f"Login: {udata.get('name', username)} @{username} ({uid})")
                return jsonify({
                    "user_id": uid,
                    "token": new_token,
                    "name": udata.get("name", username),
                })
            else:
                return jsonify({"error": "Wrong password"}), 401
    
    return jsonify({"error": "User not found"}), 404


@app.route("/api/auth/check", methods=["GET"])
def auth_check():
    user_id = session.get("user_id")
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    
    if not user_id and token:
        auth = load_auth()
        for uid, udata in auth["users"].items():
            if udata.get("token") == token:
                # Check token age — expire after 7 days
                last_login = udata.get("last_login")
                if last_login:
                    try:
                        login_time = datetime.fromisoformat(last_login)
                        if login_time.tzinfo is None:
                            login_time = login_time.replace(tzinfo=timezone.utc)
                        age = datetime.now(timezone.utc) - login_time
                        if age > timedelta(days=7):
                            return jsonify({"authenticated": False, "needsAuth": True, "reason": "expired"})
                    except (ValueError, TypeError):
                        pass
                user_id = uid
                break
    
    if user_id:
        auth = load_auth()
        user = auth["users"].get(user_id, {})
        return jsonify({
            "authenticated": True,
            "user_id": user_id,
            "name": user.get("name", ""),
            "username": user.get("username", ""),
            "is_admin": user.get("is_admin", False),
        })
    
    return jsonify({"authenticated": False})


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.route("/api/admin/users", methods=["GET"])
@require_admin
def admin_list_users():
    """List all users with their metadata (no password hashes)."""
    auth = load_auth()
    users = []
    for uid, udata in auth["users"].items():
        # Get message count from their DB
        try:
            conn = get_db(uid)
            msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            fact_count = conn.execute("SELECT COUNT(*) FROM learned_facts").fetchone()[0]
            conn.close()
        except Exception:
            msg_count = 0
            fact_count = 0
        
        users.append({
            "user_id": uid,
            "username": udata.get("username", ""),
            "name": udata.get("name", ""),
            "created_at": udata.get("created_at", ""),
            "invite_code": udata.get("invite_code", ""),
            "is_admin": udata.get("is_admin", False),
            "has_password": bool(udata.get("password_hash")),
            "message_count": msg_count,
            "fact_count": fact_count,
        })
    return jsonify({"users": users})


@app.route("/api/admin/migrate", methods=["POST"])
@require_admin
def admin_migrate_user():
    """Migrate all data from one user account to another.
    
    Use case: Drew signed up with invite code (legacy), then registers
    with username/password. Admin moves his conversation history to the
    new account.
    
    Body: {"from_user_id": "user_xxx", "to_user_id": "user_yyy", "delete_source": false}
    """
    data = request.json or {}
    from_id = data.get("from_user_id", "").strip()
    to_id = data.get("to_user_id", "").strip()
    delete_source = data.get("delete_source", False)
    
    if not from_id or not to_id:
        return jsonify({"error": "Need from_user_id and to_user_id"}), 400
    
    if from_id == to_id:
        return jsonify({"error": "Source and target are the same"}), 400
    
    auth = load_auth()
    if from_id not in auth["users"]:
        return jsonify({"error": f"Source user {from_id} not found"}), 404
    if to_id not in auth["users"]:
        return jsonify({"error": f"Target user {to_id} not found"}), 404
    
    try:
        src_conn = get_db(from_id)
        dst_conn = get_db(to_id)
        
        migrated = {"messages": 0, "learned_facts": 0, "open_questions": 0}
        
        # Migrate messages
        rows = src_conn.execute("SELECT role, content, created_at, delivered FROM messages ORDER BY id").fetchall()
        for r in rows:
            dst_conn.execute(
                "INSERT INTO messages (role, content, created_at, delivered) VALUES (?, ?, ?, ?)",
                (r["role"], r["content"], r["created_at"], r["delivered"])
            )
            migrated["messages"] += 1
        
        # Migrate learned facts
        rows = src_conn.execute("SELECT fact, taught_by_user, source, created_at FROM learned_facts ORDER BY id").fetchall()
        for r in rows:
            dst_conn.execute(
                "INSERT INTO learned_facts (fact, taught_by_user, source, created_at) VALUES (?, ?, ?, ?)",
                (r["fact"], r["taught_by_user"], r["source"], r["created_at"])
            )
            migrated["learned_facts"] += 1
        
        # Migrate open questions
        rows = src_conn.execute(
            "SELECT question, context, created_at, resolved_at, resolution, nudge_count, last_nudge_at "
            "FROM open_questions ORDER BY id"
        ).fetchall()
        for r in rows:
            dst_conn.execute(
                "INSERT INTO open_questions (question, context, created_at, resolved_at, resolution, nudge_count, last_nudge_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["question"], r["context"], r["created_at"], r["resolved_at"],
                 r["resolution"], r["nudge_count"], r["last_nudge_at"])
            )
            migrated["open_questions"] += 1
        
        # Copy user_profile preferences if target doesn't have custom ones
        src_profile = src_conn.execute("SELECT timezone, name, preferences FROM user_profile WHERE id=1").fetchone()
        if src_profile and src_profile["name"]:
            dst_conn.execute(
                "UPDATE user_profile SET timezone=?, name=?, preferences=? WHERE id=1",
                (src_profile["timezone"], src_profile["name"], src_profile["preferences"])
            )
        
        dst_conn.commit()
        
        # Optionally delete source
        if delete_source:
            safe_id = hashlib.sha256(from_id.encode()).hexdigest()[:16]
            db_path = DB_DIR / f"{safe_id}.db"
            src_conn.close()
            if db_path.exists():
                db_path.unlink()
            del auth["users"][from_id]
            save_auth(auth)
            log.info(f"Admin: Migrated + deleted {from_id} → {to_id}: {migrated}")
        else:
            src_conn.close()
            log.info(f"Admin: Migrated {from_id} → {to_id}: {migrated}")
        
        dst_conn.close()
        
        return jsonify({"success": True, "migrated": migrated, "source_deleted": delete_source})
    except Exception as e:
        log.error(f"Migration error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/set-admin", methods=["POST"])
@require_admin
def admin_set_admin():
    """Set or remove admin flag on a user."""
    data = request.json or {}
    target_id = data.get("user_id", "").strip()
    is_admin = data.get("is_admin", True)
    
    auth = load_auth()
    if target_id not in auth["users"]:
        return jsonify({"error": "User not found"}), 404
    
    auth["users"][target_id]["is_admin"] = is_admin
    save_auth(auth)
    log.info(f"Admin: Set admin={is_admin} for {target_id}")
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Post-response extraction — questions & learned facts
# ---------------------------------------------------------------------------

def _extract_questions_and_facts(conn: sqlite3.Connection, user_message: str, reply: str, history: list[dict]):
    """After each exchange, extract open questions, learned facts, and resolve answered questions.
    
    Uses a lightweight GPT-4o-mini call to identify:
    - Questions the user asked that weren't fully answered
    - Facts/info the user shared that Tangle should remember
    - Previously open questions that were just answered/resolved in this exchange
    """
    # Build recent context (last 4 exchanges)
    recent = history[-8:] if len(history) >= 8 else history
    recent_text = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    
    # Build list of currently open questions for resolution check
    open_qs = get_open_questions(conn)
    open_qs_text = ""
    if open_qs:
        open_qs_lines = [f"  [{q['id']}] {q['question']}" for q in open_qs[:15]]
        open_qs_text = (
            "\n\nCURRENTLY OPEN QUESTIONS (check if any were just answered/resolved):\n"
            + "\n".join(open_qs_lines)
        )
    
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "Analyze this conversation exchange. Return a JSON object with three arrays:\n"
                    "1. \"questions\": Questions or topics the user brought up that weren't fully resolved "
                    "or that would benefit from follow-up research. Only include genuine questions/curiosities, "
                    "not rhetorical ones or greetings. Be selective \u2014 only real open threads.\n"
                    "2. \"facts\": Things the user shared about themselves or taught the bot \u2014 personal details, "
                    "preferences, experiences, knowledge, corrections. These should be stored as short, "
                    "referenceable facts (e.g. \"Lives in Massachusetts\", \"Plays Diablo 3 Crusader\", "
                    "\"Interested in UAP physics\").\n"
                    "3. \"resolved\": IDs (integers) of previously open questions that were answered, "
                    "addressed, or naturally concluded in this exchange. A question is resolved if:\n"
                    "   - The user or Tangle provided a satisfactory answer\n"
                    "   - The user indicated they figured it out or looked it up\n"
                    "   - The topic reached a natural conclusion\n"
                    "   - The user said they're no longer interested\n"
                    "Only include IDs from the CURRENTLY OPEN QUESTIONS list below.\n\n"
                    "Return ONLY valid JSON. If nothing to extract, return "
                    "{\"questions\": [], \"facts\": [], \"resolved\": []}.\n"
                    "Max 2 questions and 3 facts per exchange."
                )
            }, {
                "role": "user",
                "content": (
                    f"Recent context:\n{recent_text}\n\n"
                    f"Latest exchange:\nUser: {user_message}\nTangle: {reply}"
                    f"{open_qs_text}"
                )
            }],
            temperature=0.3,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
    except Exception as e:
        log.error(f"Extraction parse error: {e}")
        return
    
    # Resolve questions that were answered in this exchange
    open_q_ids = {q["id"] for q in open_qs}
    for qid in result.get("resolved", []):
        if isinstance(qid, int) and qid in open_q_ids:
            conn.execute(
                "UPDATE open_questions SET resolved_at=datetime('now'), resolution=? WHERE id=?",
                (f"Resolved in conversation: {reply[:200]}", qid)
            )
            conn.commit()
            q_text = next((q["question"] for q in open_qs if q["id"] == qid), "?")
            log.info(f"  Resolved question #{qid}: {q_text[:60]}")
    
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
# Conversation summaries — long-term memory beyond the 30-message window
# ---------------------------------------------------------------------------

def get_conversation_summaries(conn: sqlite3.Connection, limit: int = 5) -> list[dict]:
    """Get the most recent conversation summaries."""
    rows = conn.execute(
        "SELECT summary, period_start, period_end FROM conversation_summaries "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _maybe_summarize_history(conn: sqlite3.Connection):
    """Check if we have unsummarized messages beyond the recent window. If so, compress them.
    
    Triggers when there are 40+ messages since the last summary boundary.
    Summarizes everything except the most recent 30 messages.
    """
    # Find the highest message ID that's been summarized
    last_summarized = conn.execute(
        "SELECT COALESCE(MAX(msg_id_to), 0) FROM conversation_summaries"
    ).fetchone()[0]
    
    # Count unsummarized messages (excluding the recent 30 we keep as raw context)
    total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    recent_30_cutoff = conn.execute(
        "SELECT id FROM messages ORDER BY id DESC LIMIT 1 OFFSET 29"
    ).fetchone()
    
    if not recent_30_cutoff:
        return  # Less than 30 messages total, nothing to summarize
    
    cutoff_id = recent_30_cutoff[0]
    
    # Get unsummarized messages that are outside the recent 30
    unsummarized = conn.execute(
        "SELECT id, role, content, created_at FROM messages "
        "WHERE id > ? AND id < ? ORDER BY id",
        (last_summarized, cutoff_id)
    ).fetchall()
    
    if len(unsummarized) < 10:
        return  # Not enough to bother summarizing
    
    # Build the conversation text for summarization
    msg_lines = []
    for m in unsummarized:
        ts = m["created_at"][:16] if m["created_at"] else ""
        msg_lines.append(f"[{ts}] {m['role']}: {m['content']}")
    conversation_text = "\n".join(msg_lines)
    
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "Summarize this conversation between a user and Tangle (an AI companion). "
                    "Focus on:\n"
                    "- Key topics discussed and what was said about them\n"
                    "- Questions that came up (resolved or not)\n"
                    "- Things the user shared about themselves\n"
                    "- Any promises, plans, or follow-ups mentioned\n"
                    "- The emotional tone and vibe of the conversation\n\n"
                    "Write it as a concise narrative summary in past tense, like journal notes. "
                    "Include approximate dates/times when visible in timestamps. "
                    "Keep it to 3-6 sentences. This will be used to help Tangle remember "
                    "past conversations."
                )
            }, {
                "role": "user",
                "content": conversation_text[:6000]  # Cap input to avoid huge calls
            }],
            temperature=0.3,
            max_tokens=300,
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"Summarization failed: {e}")
        return
    
    # Store the summary
    period_start = unsummarized[0]["created_at"] if unsummarized else None
    period_end = unsummarized[-1]["created_at"] if unsummarized else None
    msg_id_from = unsummarized[0]["id"]
    msg_id_to = unsummarized[-1]["id"]
    
    conn.execute(
        "INSERT INTO conversation_summaries (summary, msg_id_from, msg_id_to, period_start, period_end) "
        "VALUES (?, ?, ?, ?, ?)",
        (summary, msg_id_from, msg_id_to, period_start, period_end)
    )
    conn.commit()
    log.info(f"  Summarized messages {msg_id_from}-{msg_id_to}: {summary[:80]}...")


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.json or {}
    user_message = data.get("message", "").strip()
    image_url = data.get("image_url", "").strip()  # relative URL from upload
    user_id = request.user_id
    
    if not user_message and not image_url:
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
                
                # Check if we already acknowledged being away since state_since
                state_since = state["state_since"]
                already_replied = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE role='assistant' AND created_at >= ?",
                    (state_since,)
                ).fetchone()[0]
                
                if already_replied > 0:
                    # Already told them we're away — just log, don't reply again
                    return jsonify({
                        "response": None,
                        "state": state["state"],
                        "silent": True,
                    })
                
                # First message while away — acknowledge once
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
    summaries = get_conversation_summaries(conn, 5)
    
    context_parts = []
    
    # Get user's name
    auth = load_auth()
    user_data = auth["users"].get(user_id, {})
    if user_data.get("name"):
        context_parts.insert(0, f"User's name: {user_data['name']}")
    
    # Inject conversation summaries for long-term recall
    if summaries:
        summary_lines = []
        for s in summaries:
            period = ""
            if s["period_start"]:
                start_date = s["period_start"][:10]
                end_date = (s["period_end"] or s["period_start"])[:10]
                if start_date == end_date:
                    period = f"({start_date})"
                else:
                    period = f"({start_date} to {end_date})"
            summary_lines.append(f"- {period} {s['summary']}")
        context_parts.append("Previous conversations:\n" + "\n".join(summary_lines))
    
    learned_text = "Nothing yet." if not learned else "\n".join(f"- {f}" for f in learned)
    
    open_q_text = "None right now." if not open_qs else "\n".join(
        f"- ({q['created_at'][:10]}) {q['question']}" for q in open_qs
    )
    
    system = SYSTEM_PROMPT.format(
        context="\n".join(context_parts) if context_parts else "No special context yet.",
        learned_facts=learned_text,
        open_questions=open_q_text,
    )
    
    # Dynamic response length — match the energy of the message
    msg_lower = user_message.lower().strip()
    msg_len = len(user_message.strip())
    
    depth_keywords = ["elaborate", "explain", "tell me more", "go deeper", "more detail",
                      "can you expand", "break it down", "walk me through", "in depth",
                      "more than a", "longer", "thorough"]
    wants_depth = any(kw in msg_lower for kw in depth_keywords)
    
    # Short reactions/acknowledgments get a tight ceiling
    short_patterns = {"lol", "lmao", "haha", "yeah", "yep", "nah", "nope", "true",
                      "nice", "damn", "wow", "ok", "okay", "sure", "bet", "rip",
                      "same", "mood", "facts", "word", "fr", "oof", "bruh", "yo"}
    is_short = msg_lower.rstrip("!?.") in short_patterns or (msg_len <= 12 and not any(c == '?' for c in user_message))
    
    if wants_depth:
        max_tokens = 800
    elif is_short:
        max_tokens = 80   # ~1-2 sentences max
    elif msg_len <= 40:
        max_tokens = 200  # quick exchange
    else:
        max_tokens = 600  # normal conversation
    
    gpt_messages = [{"role": "system", "content": system}]
    for m in history:
        # Include timestamps so Tangle knows when things were said
        ts_prefix = ""
        if m.get("ts"):
            ts_prefix = f"[{m['ts'][:16]}] "
        gpt_messages.append({"role": m["role"], "content": f"{ts_prefix}{m['content']}"})
    
    # Build user message — text + optional image for vision
    use_vision = False
    if image_url:
        use_vision = True
        # Read image as base64 for GPT-4o vision
        img_parts = image_url.lstrip("/").split("/")  # api/uploads/<uid>/<file>
        if len(img_parts) >= 4:
            img_path = UPLOADS_DIR / img_parts[2] / img_parts[3]
            if img_path.exists():
                img_b64 = base64.b64encode(img_path.read_bytes()).decode()
                ext = img_path.suffix.lstrip(".")
                mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
                mime = mime_map.get(ext, "image/jpeg")
                
                user_content = []
                if user_message:
                    user_content.append({"type": "text", "text": user_message})
                else:
                    user_content.append({"type": "text", "text": "[User sent an image]"})
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_b64}", "detail": "auto"}
                })
                gpt_messages.append({"role": "user", "content": user_content})
            else:
                use_vision = False
                gpt_messages.append({"role": "user", "content": user_message or "[Image not found]"})
        else:
            use_vision = False
            gpt_messages.append({"role": "user", "content": user_message or "[Image]"})
    else:
        gpt_messages.append({"role": "user", "content": user_message})
    
    # Tangent protocol: detect statements worth pulling on
    if not is_short and not wants_depth and not use_vision and msg_len > 20:
        has_question = '?' in user_message
        if not has_question:
            # Statement detected — nudge Tangle to dig in
            gpt_messages.append({
                "role": "system",
                "content": (
                    "[TANGENT OPPORTUNITY] The user just shared something about themselves, "
                    "their life, or their interests — not a question. Pull the thread. "
                    "Vary your approach: sometimes ask a specific follow-up, sometimes "
                    "share your own take without asking anything, sometimes just react "
                    "with genuine interest. Do NOT always end with a question — about "
                    "half the time, just engage and leave space. Make them feel like "
                    "what they said was interesting, not just heard."
                )
            })
    
    # Use GPT-4o for vision, GPT-4o-mini for text-only
    model = "gpt-4o" if use_vision else "gpt-4o-mini"
    
    try:
        resp = openai.chat.completions.create(
            model=model,
            messages=gpt_messages,
            temperature=0.75,
            max_tokens=max_tokens,
        )
        reply = resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"GPT error: {e}")
        reply = "Sorry, my brain just glitched for a second. What were you saying?"
    
    # Store message with optional image reference
    store_content = user_message
    if image_url:
        store_content = f"[image:{image_url}]" + (f" {user_message}" if user_message else "")
    store_message(conn, "user", store_content)
    store_message(conn, "assistant", reply)
    
    # --- Post-response processing (non-blocking to user) ---
    try:
        _extract_questions_and_facts(conn, user_message, reply, history)
    except Exception as e:
        log.error(f"Extraction error: {e}")
    
    # Summarize older history if needed (keeps long-term memory alive)
    try:
        _maybe_summarize_history(conn)
    except Exception as e:
        log.error(f"Summarization error: {e}")
    
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
# ---------------------------------------------------------------------------
# Image uploads
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB


@app.route("/api/upload", methods=["POST"])
@require_auth
def upload_image():
    """Upload an image. Returns a URL that can be referenced in chat."""
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    
    f = request.files["image"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400
    
    mime = f.content_type or ""
    if mime not in ALLOWED_IMAGE_TYPES:
        return jsonify({"error": f"Unsupported type: {mime}. Use JPEG, PNG, GIF, or WebP."}), 400
    
    data = f.read()
    if len(data) > MAX_IMAGE_SIZE:
        return jsonify({"error": "Image too large (max 10MB)"}), 400
    
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(mime, "jpg")
    name = f"{secrets.token_hex(12)}.{ext}"
    
    # Save per-user
    user_dir = UPLOADS_DIR / request.user_id
    user_dir.mkdir(exist_ok=True)
    path = user_dir / name
    path.write_bytes(data)
    
    url = f"/api/uploads/{request.user_id}/{name}"
    log.info(f"Upload: {request.user_id} -> {name} ({len(data)} bytes)")
    return jsonify({"url": url, "filename": name})


@app.route("/api/uploads/<user_id>/<filename>")
def serve_upload(user_id, filename):
    """Serve uploaded images."""
    upload_path = UPLOADS_DIR / user_id
    if not upload_path.exists():
        return "", 404
    return send_from_directory(str(upload_path), filename)


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
