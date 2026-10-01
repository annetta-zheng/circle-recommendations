"""Repository: the only module that speaks SQL.

Every function returns plain dicts so the ranking code never touches sqlite3
rows, and the same queries would run unchanged on Postgres.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app import config


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path=None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------------------ members

def get_member(conn, user_id: int) -> dict | None:
    """The member as the ranker sees her: profile, stated interests, memberships."""
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    member = dict(row)
    member["stated_interests"] = [
        r["topic_slug"]
        for r in conn.execute(
            "SELECT topic_slug FROM user_topic_interests WHERE user_id = ? ORDER BY topic_slug",
            (user_id,),
        )
    ]
    member["memberships"] = _rows(
        conn.execute(
            """SELECT m.circle_id, m.role, m.joined_at, c.name, c.topic
               FROM memberships m JOIN circles c ON c.id = m.circle_id
               WHERE m.user_id = ? ORDER BY m.circle_id""",
            (user_id,),
        )
    )
    return member


def count_members(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ------------------------------------------------------------------ circles

def all_circles_with_stats(conn) -> list[dict]:
    """Every Circle joined to its health snapshot and its tags."""
    circles = _rows(
        conn.execute(
            """SELECT c.id, c.name, c.slug, c.description, c.topic, c.format, c.access,
                      c.join_policy, c.max_members, c.member_count, c.last_activity_at,
                      s.seats_left, s.is_full, s.size_band, s.posts_30d, s.chat_30d,
                      s.comments_30d, s.reactions_30d, s.days_since_activity,
                      julianday((SELECT value FROM settings WHERE key = 'as_of'))
                        - julianday(c.created_at) AS age_days,
                      (SELECT COUNT(*)
                       FROM activity a
                       JOIN memberships l ON l.circle_id = a.circle_id AND l.user_id = a.author_user_id
                       WHERE a.circle_id = c.id AND l.role = 'leader'
                         AND julianday((SELECT value FROM settings WHERE key = 'as_of'))
                             - julianday(a.created_at) BETWEEN 0 AND 30) AS leader_posts_30d
               FROM circles c JOIN circle_stats s ON s.circle_id = c.id
               ORDER BY c.id"""
        )
    )
    tags: dict[int, list[str]] = {}
    for r in conn.execute("SELECT circle_id, tag_slug FROM circle_tags ORDER BY circle_id, tag_slug"):
        tags.setdefault(r["circle_id"], []).append(r["tag_slug"])
    for c in circles:
        c["tags"] = tags.get(c["id"], [])
    return circles


def count_circles(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM circles").fetchone()[0]


def topic_labels(conn) -> dict[str, str]:
    return {r["slug"]: r["label"] for r in conn.execute("SELECT slug, label FROM topics")}


# ------------------------------------------------------------------ social proof

def co_member_overlap(conn, user_id: int, interests: list[str], location: str | None,
                      industry: str | None = None, company: str | None = None) -> dict[int, dict]:
    """Per Circle: members (excluding her) who share a stated interest, her city, industry or company.

    Backed by idx_memberships_circle and idx_interests_topic.
    """
    out: dict[int, dict] = {}
    if interests:
        placeholders = ",".join("?" * len(interests))
        cur = conn.execute(
            f"""SELECT m.circle_id, COUNT(DISTINCT m.user_id) AS n
                FROM memberships m
                JOIN user_topic_interests i ON i.user_id = m.user_id
                WHERE i.topic_slug IN ({placeholders}) AND m.user_id != ?
                GROUP BY m.circle_id""",
            (*interests, user_id),
        )
        for r in cur:
            out.setdefault(r["circle_id"], {})["shared_interest"] = r["n"]
    if location and location not in config.NON_CITIES:
        cur = conn.execute(
            """SELECT m.circle_id, COUNT(*) AS n
               FROM memberships m JOIN users u ON u.id = m.user_id
               WHERE u.location = ? AND m.user_id != ?
               GROUP BY m.circle_id""",
            (location, user_id),
        )
        for r in cur:
            out.setdefault(r["circle_id"], {})["same_city"] = r["n"]
    if industry:
        cur = conn.execute(
            """SELECT m.circle_id, COUNT(*) AS n
               FROM memberships m JOIN users u ON u.id = m.user_id
               WHERE u.industry = ? AND m.user_id != ?
               GROUP BY m.circle_id""",
            (industry, user_id),
        )
        for r in cur:
            out.setdefault(r["circle_id"], {})["same_industry"] = r["n"]
    if company:
        cur = conn.execute(
            """SELECT m.circle_id, COUNT(*) AS n
               FROM memberships m JOIN users u ON u.id = m.user_id
               WHERE u.company = ? AND m.user_id != ?
               GROUP BY m.circle_id""",
            (company, user_id),
        )
        for r in cur:
            out.setdefault(r["circle_id"], {})["coworkers"] = r["n"]
    return out


def co_membership(conn, user_id: int, circle_ids: list[int],
                  max_peers_per_circle: int = config.CO_MEMBERSHIP_MAX_PEERS_PER_CIRCLE,
                  max_peer_degree: int = config.CO_MEMBERSHIP_MAX_PEER_DEGREE) -> dict[int, list[int]]:
    """Per Circle: how many Circles each of her co-members belongs to (their degree).

    A co-member is anyone else in one of `circle_ids`. Each appears once per
    Circle however many of her Circles they share. Only degrees come back, never
    who, so the ranker cannot name a member. `circle_ids` is passed in rather
    than read from her memberships, so the leave-one-out harness can hide one
    Circle without it leaking back through its own members.

    Bounded for hot Circles and heavy joiners: at most `max_peers_per_circle`
    co-members per source Circle (the most recent joiners), and co-members in
    more than `max_peer_degree` Circles are dropped. The caller caps
    `circle_ids` (see features.co_membership_sources).

    Backed by idx_memberships_circle (her co-members) and idx_memberships_user
    (their Circles).
    """
    if not circle_ids:
        return {}
    placeholders = ",".join("?" * len(circle_ids))
    cur = conn.execute(
        f"""WITH ranked AS (
                SELECT user_id,
                       ROW_NUMBER() OVER (PARTITION BY circle_id ORDER BY joined_at DESC, user_id) AS rn
                FROM memberships
                WHERE circle_id IN ({placeholders}) AND user_id != ?
            ),
            peers AS (
                SELECT DISTINCT user_id FROM ranked WHERE rn <= ?
            ),
            degree AS (
                SELECT m.user_id, COUNT(*) AS d
                FROM memberships m JOIN peers p ON p.user_id = m.user_id
                GROUP BY m.user_id
                HAVING COUNT(*) <= ?
            )
            SELECT m.circle_id, degree.d
            FROM memberships m JOIN degree ON degree.user_id = m.user_id
            ORDER BY m.circle_id, m.user_id""",
        (*circle_ids, user_id, max_peers_per_circle, max_peer_degree),
    )
    out: dict[int, list[int]] = {}
    for r in cur:
        out.setdefault(r["circle_id"], []).append(r["d"])
    return out


def as_of(conn) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = 'as_of'").fetchone()
    return row["value"] if row else config.AS_OF
