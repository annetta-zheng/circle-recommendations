"""Load the five JSON files into SQLite. Idempotent: rebuilds the database from scratch.

    python -m app.seed            # writes circles.db next to schema.sql
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from app import config


def _load(name: str) -> list[dict]:
    with open(config.DATA_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def seed(db_path: Path | None = None, data_dir: Path | None = None) -> dict[str, int]:
    db_path = Path(db_path or config.DB_PATH)
    data_dir = Path(data_dir or config.DATA_DIR)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))

    def load(name: str) -> list[dict]:
        with open(data_dir / f"{name}.json", encoding="utf-8") as f:
            return json.load(f)

    topics, users, circles = load("topics"), load("users"), load("circles")
    memberships, activity = load("memberships"), load("activity")

    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('as_of', ?)", (config.AS_OF,))
        conn.executemany(
            "INSERT INTO topics (id, slug, label, domain) VALUES (?, ?, ?, ?)",
            [(t["id"], t["slug"], t["label"], t["domain"]) for t in topics],
        )
        conn.executemany(
            """INSERT INTO users (id, first_name, last_name, has_photo, bio, job_title, company,
                                  industry, location, joined_at, profile_completeness)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (u["id"], u["first_name"], u["last_name"], int(bool(u["has_photo"])), u["bio"],
                 u["job_title"], u["company"], u["industry"], u["location"], u["joined_at"],
                 u["profile_completeness"])
                for u in users
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO user_topic_interests (user_id, topic_slug) VALUES (?, ?)",
            [(u["id"], slug) for u in users for slug in u["topic_interests"]],
        )
        conn.executemany(
            """INSERT INTO circles (id, name, slug, description, topic, cover_image_url, format, access,
                                    join_policy, max_members, member_count, created_at, last_activity_at,
                                    feed_posts_30d, chat_messages_30d)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (c["id"], c["name"], c["slug"], c["description"], c["topic"], c["cover_image_url"],
                 c["format"], c["access"], c["join_policy"], c["max_members"], c["member_count"],
                 c["created_at"], c["last_activity_at"], c["feed_posts_30d"], c["chat_messages_30d"])
                for c in circles
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO circle_tags (circle_id, tag_slug) VALUES (?, ?)",
            [(c["id"], tag) for c in circles for tag in c["tags"]],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO memberships (user_id, circle_id, role, joined_at) VALUES (?, ?, ?, ?)",
            [(m["user_id"], m["circle_id"], m["role"], m["joined_at"]) for m in memberships],
        )
        conn.executemany(
            """INSERT INTO activity (id, circle_id, author_user_id, body, reaction_count, comment_count,
                                     save_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (a["id"], a["circle_id"], a["author_user_id"], a["body"], a["reaction_count"],
                 a["comment_count"], a["save_count"], a["created_at"])
                for a in activity
            ],
        )

    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("topics", "users", "user_topic_interests", "circles", "circle_tags", "memberships", "activity")
    }
    conn.close()
    return counts


if __name__ == "__main__":
    counts = seed()
    print(f"seeded {config.DB_PATH.name} (as_of {config.AS_OF}):")
    for table, n in counts.items():
        print(f"  {table:22s} {n:5d}")
    sys.exit(0)
