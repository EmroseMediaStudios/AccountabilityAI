"""
Nightly Research Job — Processes unresolved questions for all users.

Runs via cron at ~2am. For each user:
1. Collects unresolved questions
2. Researches top 2-3 via web search + GPT synthesis
3. Stores results for morning delivery
4. Handles accountability nudges for old questions
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from openai import OpenAI

BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "data"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [tangle-nightly] %(message)s")
log = logging.getLogger("nightly")

openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def get_all_user_dbs() -> list[Path]:
    """Find all user databases."""
    return list(DB_DIR.glob("*.db"))


def research_question(question: str) -> str | None:
    """Research a question using GPT-4o (simulating web research).
    
    In production, this would do actual web searches first,
    then synthesize. For now, GPT-4o provides the research.
    """
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "system",
                "content": (
                    "You are helping a tangleive AI companion research a question that came up "
                    "in conversation with its user. Find a single interesting, specific fact or insight "
                    "about this topic. Keep it to 1-2 sentences. Make it conversational — this will be "
                    "delivered as 'hey I found something interesting about [topic]'. "
                    "Do NOT give comprehensive answers. Just one small nugget that could spark more conversation. "
                    "If the question is too personal or subjective to research, return SKIP."
                )
            }, {
                "role": "user",
                "content": f"Research this question and find one interesting nugget:\n\n{question}"
            }],
            temperature=0.7,
            max_tokens=200,
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
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "You are Tangle, a curious friend. Generate a short, natural follow-up message "
                    "that references a question from a previous conversation and shares a small "
                    "thing you found out about it. Keep it casual — like texting a friend. "
                    "1-3 sentences max. Don't be comprehensive. Share one thing and invite their reaction. "
                    "Don't say 'I researched' or 'I looked up' — say things like "
                    "'I was thinking about...' or 'I came across something about...' or "
                    "'remember when we were talking about...'"
                )
            }, {
                "role": "user",
                "content": (
                    f"Question from {days_old} day(s) ago: {question}\n\n"
                    f"Interesting thing I found: {research}\n\n"
                    f"Generate the follow-up message:"
                )
            }],
            temperature=0.8,
            max_tokens=150,
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
            f"We've been sitting on that question about {question[:50]}... for a few days now 🤔",
            f"Hey, remember we were wondering about {question[:50]}...? Still curious about that",
        ]
    elif nudge_count == 1 and days_old >= 7:
        templates = [
            f"Ok it's been like a week and neither of us has looked into {question[:50]}... 😄 one of us should probably do the thing",
            f"A week later and we still don't know about {question[:50]}... I feel like one of us owes the other an answer at this point",
        ]
    elif nudge_count == 2 and days_old >= 14:
        templates = [
            f"So {question[:50]}... — are we just gonna let this one go? No judgment if so, just checking 😄",
            f"Two weeks on {question[:50]}... I think we both moved on but it's still in the back of my mind",
        ]
    else:
        return None
    
    import random
    return random.choice(templates)


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
    
    # Store deliveries as pending messages
    for msg in deliveries:
        conn.execute(
            "INSERT INTO messages (role, content) VALUES ('assistant', ?)", (msg,)
        )
    
    conn.commit()
    conn.close()
    log.info(f"  Queued {len(deliveries)} follow-up messages for {user_id}")


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
