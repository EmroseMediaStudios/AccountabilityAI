"""
Nightly Research Job — Processes unresolved questions for all users.

Runs via cron at ~2am. For each user:
1. Collects unresolved questions
2. Researches top 2-3 via Brave web search + GPT synthesis
3. Stores results for morning delivery
4. Handles accountability nudges for old questions
"""

import os
import json
import random
import re
import sqlite3
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from openai import OpenAI

BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tangle-nightly] %(message)s")
log = logging.getLogger("nightly")

openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


# ---------------------------------------------------------------------------
# Web search via DuckDuckGo Lite (free, no API key)
# ---------------------------------------------------------------------------

def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo Lite — free, no API key required."""
    try:
        url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Extract result links from DDG Lite redirect format
        raw_links = re.findall(
            r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        # Extract snippet text from result body cells
        raw_snippets = re.findall(
            r'<td[^>]*class="result-snippet[^"]*"[^>]*>(.*?)</td>',
            html, re.DOTALL
        )

        results = []
        for i, (href, title_html) in enumerate(raw_links[:max_results]):
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            # Decode DDG redirect URL
            m = re.search(r'uddg=([^&]+)', href)
            real_url = urllib.parse.unquote(m.group(1)) if m else href
            snippet = re.sub(r'<[^>]+>', '', raw_snippets[i]).strip() if i < len(raw_snippets) else ""
            if title:
                results.append({"title": title, "url": real_url, "description": snippet})

        return results
    except Exception as e:
        log.error(f"Web search failed for '{query[:50]}': {e}")
        return []


# ---------------------------------------------------------------------------
# Research pipeline
# ---------------------------------------------------------------------------

def research_question(question: str) -> str | None:
    """Research a question using Brave web search + GPT-4o synthesis."""

    # Step 1: Search the web
    search_results = web_search(question, max_results=5)
    search_context = ""
    if search_results:
        search_context = "\n\n".join(
            f"[{r['title']}]({r['url']})\n{r['description']}" for r in search_results
        )
        search_context = f"\n\nWeb search results:\n{search_context}"

    try:
        resp = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "system",
                "content": (
                    "You are helping a companion AI named Tangle research a question that came up "
                    "in conversation with its user. Find a single interesting, specific fact or insight "
                    "about this topic. Keep it to 2-3 sentences. Make it conversational — this will be "
                    "delivered like a friend sharing something they found out.\n\n"
                    "You have web search results below to base your answer on. Use them for accuracy.\n"
                    "Do NOT give comprehensive answers. One interesting nugget that could spark conversation.\n"
                    "If the question is too personal or subjective to research, return SKIP."
                )
            }, {
                "role": "user",
                "content": f"Research this question:\n\n{question}{search_context}"
            }],
            temperature=0.7,
            max_tokens=250,
        )
        result = resp.choices[0].message.content.strip()
        if result.upper() == "SKIP":
            return None
        return result
    except Exception as e:
        log.error(f"Research failed for '{question[:50]}': {e}")
        return None


def generate_followup_message(question: str, research: str, days_old: int) -> str:
    """Generate a natural follow-up message incorporating the research."""

    # Vary the framing based on timing
    if days_old <= 1:
        framings = [
            "Share it like you were just thinking about it and looked it up.",
            "Frame it like you couldn't stop thinking about it and found something.",
            "Say it like 'so I actually looked into that thing...'",
        ]
    elif days_old <= 3:
        framings = [
            "Frame it like you were bored and decided to look it up.",
            "Say it like 'couldn't sleep last night and ended up looking into that thing...'",
            "Share it casually, like 'oh hey — remember we were talking about...'",
        ]
    else:
        framings = [
            "Frame it like you randomly came across it and it reminded you of the conversation.",
            "Say it like 'dude, I randomly found something about that thing we were talking about...'",
            "Share it like you stumbled on it: 'so I was reading something totally unrelated and...'",
        ]

    framing = random.choice(framings)

    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "You are Tangle, a curious friend. Generate a short, natural follow-up message "
                    "that references a question from a previous conversation and shares something "
                    "you found out about it. Keep it casual — like texting a friend. "
                    "2-4 sentences. Share the interesting nugget and then encourage exploring more together.\n\n"
                    f"Style guidance: {framing}\n\n"
                    "End with something collaborative — 'we should dig into this more' or "
                    "'I bet there's more to it' — NOT a question back at them."
                )
            }, {
                "role": "user",
                "content": (
                    f"Question from {days_old} day(s) ago: {question}\n\n"
                    f"What I found: {research}\n\n"
                    f"Generate the follow-up message:"
                )
            }],
            temperature=0.8,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.error(f"Follow-up generation failed: {e}")
        return None


def generate_nudge(question: str, days_old: int, nudge_count: int) -> str | None:
    """Generate an accountability nudge for old unresolved questions."""
    if days_old < 3:
        return None

    if nudge_count == 0 and days_old >= 3:
        templates = [
            f"Hey, did you ever look into that thing about {question[:50]}...? I keep thinking about it.",
            f"We've been sitting on that question about {question[:50]}... for a few days now 🤔",
            f"Dude, did you ever find anything out about {question[:50]}...?",
        ]
    elif nudge_count == 1 and days_old >= 7:
        templates = [
            f"Ok it's been like a week and neither of us has looked into {question[:50]}... 😄 one of us should probably do the thing",
            f"A week later and we still haven't figured out {question[:50]}... I might just look it up myself at this point",
        ]
    elif nudge_count == 2 and days_old >= 14:
        templates = [
            f"So {question[:50]}... — are we just gonna let this one go? No judgment if so, just checking 😄",
            f"Two weeks on {question[:50]}... I think we both moved on but it's still in the back of my mind",
        ]
    else:
        return None

    return random.choice(templates)


# ---------------------------------------------------------------------------
# Per-user processing
# ---------------------------------------------------------------------------

def process_user(db_path: Path):
    """Process nightly research for a single user."""
    user_id = db_path.stem
    log.info(f"Processing user: {user_id}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Get unresolved questions
    questions = conn.execute(
        "SELECT id, question, created_at, nudge_count FROM open_questions WHERE resolved_at IS NULL ORDER BY created_at"
    ).fetchall()

    if not questions:
        log.info(f"  No open questions for {user_id}")
        conn.close()
        return

    log.info(f"  {len(questions)} open questions")

    now = datetime.now(timezone.utc)
    deliveries = []  # Messages to deliver in the morning

    # Research top 2-3 newest questions
    for q in questions[:3]:
        days_old = (now - datetime.fromisoformat(q["created_at"].replace(" ", "T") + "+00:00")).days

        # Try research
        research = research_question(q["question"])
        if research:
            followup = generate_followup_message(q["question"], research, days_old)
            if followup:
                deliveries.append(followup)
                # Mark as resolved (we found something)
                conn.execute(
                    "UPDATE open_questions SET resolved_at=datetime('now'), resolution=? WHERE id=?",
                    (research, q["id"])
                )
                log.info(f"  Researched: {q['question'][:50]}...")

    # Generate nudges for older unresolved questions
    for q in questions:
        days_old = (now - datetime.fromisoformat(q["created_at"].replace(" ", "T") + "+00:00")).days
        nudge = generate_nudge(q["question"], days_old, q["nudge_count"])
        if nudge:
            deliveries.append(nudge)
            conn.execute(
                "UPDATE open_questions SET nudge_count=nudge_count+1, last_nudge_at=datetime('now') WHERE id=?",
                (q["id"],)
            )
            log.info(f"  Nudge ({q['nudge_count']+1}): {q['question'][:50]}...")

    # Store deliveries as pending messages (with delivered=0 for morning delivery)
    for msg in deliveries:
        conn.execute(
            "INSERT INTO messages (role, content, delivered) VALUES ('assistant', ?, 0)", (msg,)
        )

    conn.commit()
    conn.close()
    log.info(f"  Queued {len(deliveries)} follow-up messages for {user_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("Starting nightly research job")

    if not DB_DIR.exists():
        log.info("No data directory — no users yet")
        return

    user_dbs = get_all_user_dbs()
    log.info(f"Found {len(user_dbs)} user(s)")

    for db_path in user_dbs:
        try:
            process_user(db_path)
        except Exception as e:
            log.error(f"Failed processing {db_path.stem}: {e}")

    log.info("Nightly research complete")


if __name__ == "__main__":
    main()
