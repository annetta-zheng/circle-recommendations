# Database schema

`schema.sql` is the whole schema: seven tables, one settings row, five indexes, one view. `python -m app.seed` rebuilds `circles.db` from the five JSON files in `data/` (idempotent: it deletes and recreates the file). Row counts after seeding: topics 22, users 100, user_topic_interests 140, circles 40, circle_tags 82 (the source has 83 tag entries; one Circle lists a tag twice), memberships 441, activity 386.

## Tables

| Table | Holds | Design note |
| --- | --- | --- |
| `settings` | one row, `as_of = 2026-09-21` | The dataset's anchor date. The view measures "last 30 days" against it, so results never drift with the wall clock. |
| `topics` | id, slug, label, domain | `slug` is `UNIQUE` so the two slug foreign keys below hold. |
| `users` | profile fields, joined_at, profile_completeness | Nullable bio, job title, company, industry, location as in the source; `has_photo` as 0/1 with a CHECK. |
| `user_topic_interests` | user_id, topic_slug | The `topic_interests` array, one row per stated interest; FK to `topics.slug`. |
| `circles` | name, topic, format, access, join_policy, max_members, member_count, last_activity_at, feed_posts_30d, chat_messages_30d | Enums as CHECK constraints. `topic` is nullable (two Circles have none) and FKs to `topics.slug`. `max_members` NULL means uncapped. |
| `circle_tags` | circle_id, tag_slug | The `tags` array. No FK to `topics`: the dataset README calls tags free-form, and in the ranker they are worth 0.15 at most. |
| `memberships` | user_id, circle_id, role, joined_at | Primary key on (user_id, circle_id): a member joins a Circle once. Leaders are rows with `role = 'leader'`. |
| `activity` | circle_id, author_user_id, reaction/comment/save counts, created_at | Post bodies are kept but never read by the ranker. |

## Derived, not stored: `circle_stats`

A view, one row per Circle: `seats_left`, `is_full`, `size_band` (small ≤ 8, medium ≤ 15, large), `posts_30d`, `chat_30d`, `comments_30d`, `reactions_30d`, `days_since_activity`. Comments and reactions are summed from `activity` over the 30 days before `settings.as_of`; posts and chat come from the denormalized counters on `circles`.

What the view deliberately does not compute: the log-scaled volume, its percentile, and the dormant flag. Those live in `app/features.py` next to the thresholds in `app/config.py`, so there is one place to argue with the numbers and no SQLite math-extension dependency.

## Why the shape differs from the JSON

- Arrays became join tables so that "who shares this interest" and "which Circles carry this tag" are index lookups, not string scans.
- Activity aggregates on `circles` stay denormalized as in the source. `chat_messages_30d` cannot be rebuilt from `activity` (chat text is not in the dataset), and in production these counters are refreshed by a job, not computed per request.
- Everything the ranker needs per Circle comes from one join (`circles` × `circle_stats`) plus two aggregates over `memberships`: social proof (members who share her interests, city or industry), and co-membership (how many Circles each person in her Circles belongs to — degrees only, never who, bounded per request).

## Indexes

Each index backs exactly one query in the pipeline. With 40 Circles none of them changes a query plan; they are declared for the shape the service would have at 170k Circles.

| Index | Query it backs |
| --- | --- |
| `memberships(user_id)` | what has she joined (exclusion, implicit interests); the Circles of each person in hers (co-membership) |
| `memberships(circle_id)` | co-members of a candidate (social proof, leader lookup); who else is in her Circles (co-membership). At scale `(circle_id, joined_at)` serves the most-recent-50 cap without a sort |
| `circles(topic)` | candidates by topic |
| `user_topic_interests(topic_slug)` | members who share an interest |
| `activity(circle_id, created_at)` | recent posts per Circle for the view |

Skipped on purpose: indexes on `users.location`, `users.industry` and `users.company` (social proof is aggregated per Circle through `memberships`, not scanned per user), and a partial index on open, not-full Circles (worth adding on Postgres once candidate generation reads it).

## At scale

Same tables on Postgres; `circle_stats` becomes a materialized table refreshed on a schedule; candidate generation reads `circles(topic)` plus a geo index instead of scanning; the `settings.as_of` row goes away because "now" is now.
