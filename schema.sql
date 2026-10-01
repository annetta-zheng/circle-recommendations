-- Circle recommendations: normalized schema for the seed dataset.
-- Arrays in the source (topic_interests, tags) become join tables; activity
-- aggregates on circles stay denormalized as in the source, because chat volume
-- cannot be rebuilt from posts. Rationale per table and index: docs/schema.md.

PRAGMA foreign_keys = ON;

-- one-row settings: the anchor date the view measures "30 days" against
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id     INTEGER PRIMARY KEY,
    slug   TEXT NOT NULL UNIQUE,        -- UNIQUE so the slug foreign keys below hold
    label  TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'content'
);

CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY,
    first_name           TEXT NOT NULL,
    last_name            TEXT NOT NULL,
    has_photo            INTEGER NOT NULL DEFAULT 0 CHECK (has_photo IN (0, 1)),
    bio                  TEXT,
    job_title            TEXT,
    company              TEXT,
    industry             TEXT,
    location             TEXT,
    joined_at            TEXT NOT NULL,
    profile_completeness REAL NOT NULL CHECK (profile_completeness BETWEEN 0 AND 1)
);

-- the topic_interests array, one row per stated interest
CREATE TABLE IF NOT EXISTS user_topic_interests (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic_slug TEXT    NOT NULL REFERENCES topics(slug),
    PRIMARY KEY (user_id, topic_slug)
);

CREATE TABLE IF NOT EXISTS circles (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    slug              TEXT NOT NULL UNIQUE,
    description       TEXT,
    topic             TEXT REFERENCES topics(slug),   -- nullable: two Circles have no topic
    cover_image_url   TEXT,
    format            TEXT NOT NULL CHECK (format IN ('in_person', 'virtual', 'hybrid')),
    access            TEXT NOT NULL CHECK (access IN ('public', 'unlisted')),
    join_policy       TEXT NOT NULL CHECK (join_policy IN ('open', 'request')),
    max_members       INTEGER,                        -- NULL means uncapped
    member_count      INTEGER NOT NULL CHECK (member_count >= 0),
    created_at        TEXT NOT NULL,
    last_activity_at  TEXT NOT NULL,
    feed_posts_30d    INTEGER NOT NULL DEFAULT 0,
    chat_messages_30d INTEGER NOT NULL DEFAULT 0
);

-- the tags array; free-form per the dataset README, so no foreign key to topics
CREATE TABLE IF NOT EXISTS circle_tags (
    circle_id INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    tag_slug  TEXT    NOT NULL,
    PRIMARY KEY (circle_id, tag_slug)
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    circle_id INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    role      TEXT    NOT NULL CHECK (role IN ('member', 'leader')),
    joined_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, circle_id)                   -- a member joins a Circle once
);

CREATE TABLE IF NOT EXISTS activity (
    id             INTEGER PRIMARY KEY,
    circle_id      INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    author_user_id INTEGER NOT NULL REFERENCES users(id),
    body           TEXT    NOT NULL,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    comment_count  INTEGER NOT NULL DEFAULT 0,
    save_count     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL
);

-- Indexes: each one backs a single query in the ranking pipeline.
CREATE INDEX IF NOT EXISTS idx_memberships_user     ON memberships(user_id);              -- what has she joined
CREATE INDEX IF NOT EXISTS idx_memberships_circle   ON memberships(circle_id);            -- co-members of a candidate
CREATE INDEX IF NOT EXISTS idx_circles_topic        ON circles(topic);                    -- candidates by topic
CREATE INDEX IF NOT EXISTS idx_interests_topic      ON user_topic_interests(topic_slug);  -- who shares an interest
CREATE INDEX IF NOT EXISTS idx_activity_circle_time ON activity(circle_id, created_at);   -- recent posts per Circle

-- Derived health snapshot per Circle. The 30-day window is measured against
-- settings.as_of, so the view is deterministic; the log-volume, percentile and
-- dormant flag are computed in app/features.py from these columns.
CREATE VIEW IF NOT EXISTS circle_stats AS
WITH s AS (
    SELECT value AS as_of FROM settings WHERE key = 'as_of'
),
recent AS (
    SELECT a.circle_id,
           SUM(a.comment_count)  AS comments_30d,
           SUM(a.reaction_count) AS reactions_30d
    FROM activity a
    CROSS JOIN s
    WHERE julianday(s.as_of) - julianday(a.created_at) BETWEEN 0 AND 30
    GROUP BY a.circle_id
)
SELECT c.id AS circle_id,
       c.member_count,
       c.max_members,
       CASE WHEN c.max_members IS NULL THEN NULL ELSE c.max_members - c.member_count END AS seats_left,
       CASE WHEN c.max_members IS NOT NULL AND c.member_count >= c.max_members THEN 1 ELSE 0 END AS is_full,
       CASE WHEN c.member_count <= 8 THEN 'small'
            WHEN c.member_count <= 15 THEN 'medium'
            ELSE 'large' END AS size_band,
       c.feed_posts_30d               AS posts_30d,
       c.chat_messages_30d            AS chat_30d,
       COALESCE(r.comments_30d, 0)    AS comments_30d,
       COALESCE(r.reactions_30d, 0)   AS reactions_30d,
       julianday(s.as_of) - julianday(c.last_activity_at) AS days_since_activity
FROM circles c
CROSS JOIN s
LEFT JOIN recent r ON r.circle_id = c.id;
