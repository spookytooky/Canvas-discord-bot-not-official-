"""SQLite persistence for Canvas courses, assignments, and grade history.

This is the only module that talks to SQLite. The bot uses it to remember what
it has already seen, so it can show how a grade moved since the last recorded
day and summarise outstanding work without re-asking Canvas.

Every call site treats a database problem as non-fatal: if the file cannot be
opened the bot keeps working straight from the Canvas API.
"""

import contextlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).with_name("canvas.db")

EASTERN = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    course_code TEXT,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS assignments (
    id              INTEGER PRIMARY KEY,
    course_id       INTEGER NOT NULL REFERENCES courses (id),
    name            TEXT    NOT NULL,
    due_at          TEXT,
    html_url        TEXT,
    points_possible REAL,
    score           REAL,
    is_submitted    INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS grade_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL REFERENCES courses (id),
    score         REAL,
    snapshot_date TEXT    NOT NULL,
    recorded_at   TEXT    NOT NULL,
    UNIQUE (course_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_assignments_due
    ON assignments (due_at);

CREATE INDEX IF NOT EXISTS idx_snapshots_course
    ON grade_snapshots (course_id, snapshot_date);
"""


def utc_now_iso() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM:SS' — the format SQLite's date functions parse."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def eastern_today() -> str:
    """Today's date in Eastern time, used as the grade-snapshot key.

    Using the date rather than an exact timestamp means running /grades twice in
    one day updates the same row instead of piling up duplicate snapshots.
    """
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def normalize_due(due_at):
    """Turn a Canvas ISO-8601 timestamp into 'YYYY-MM-DD HH:MM:SS' UTC, or None.

    Canvas sends '2026-10-08T16:00:00Z'. Storing one consistent format keeps
    string comparisons and julianday() arithmetic predictable.
    """
    if not due_at:
        return None
    try:
        parsed = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def init_db(conn) -> None:
    """Create the tables and indexes if they are missing."""
    conn.executescript(SCHEMA)


@contextlib.contextmanager
def session(db_path=None):
    """Open the database, make sure the schema exists, and commit on success.

    A fresh connection per call keeps things safe: the bot runs commands in
    worker threads via asyncio.to_thread, and SQLite connections should not be
    shared across threads by default.
    """
    conn = sqlite3.connect(Path(db_path) if db_path is not None else DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        init_db(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_courses(conn, courses) -> int:
    """Upsert course rows and record one grade snapshot per course for today."""
    now = utc_now_iso()
    today = eastern_today()

    rows = [
        (course.get("id"), course.get("name") or "Unknown Course", course.get("course_code"), now)
        for course in (courses or [])
        if course.get("id") is not None
    ]
    conn.executemany(
        """
        INSERT INTO courses (id, name, course_code, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            name        = excluded.name,
            course_code = excluded.course_code,
            updated_at  = excluded.updated_at
        """,
        rows,
    )

    snapshots = [
        (course.get("id"), course.get("current_score"), today, now)
        for course in (courses or [])
        if course.get("id") is not None
    ]
    conn.executemany(
        """
        INSERT INTO grade_snapshots (course_id, score, snapshot_date, recorded_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (course_id, snapshot_date) DO UPDATE SET
            score       = excluded.score,
            recorded_at = excluded.recorded_at
        """,
        snapshots,
    )
    return len(rows)


def save_assignments(conn, assignments) -> int:
    """Upsert assignment rows. Each item must carry its Canvas ``course_id``."""
    now = utc_now_iso()
    rows = []

    for item in assignments or []:
        course_id = item.get("course_id")
        if course_id is None or item.get("id") is None:
            continue

        # /urgent and the daily digest only know the course *name*, not its row.
        # Create a placeholder so the foreign key still resolves; a later
        # /grades run replaces the placeholder with the real course name.
        conn.execute(
            """
            INSERT OR IGNORE INTO courses (id, name, course_code, updated_at)
            VALUES (?, ?, NULL, ?)
            """,
            (course_id, item.get("course") or "Unknown Course", now),
        )

        rows.append(
            (
                item.get("id"),
                course_id,
                item.get("name") or "",
                normalize_due(item.get("due_at")),
                item.get("url"),
                item.get("points_possible"),
                item.get("score"),
                1 if item.get("is_submitted") else 0,
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO assignments
            (id, course_id, name, due_at, html_url, points_possible, score, is_submitted, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            course_id       = excluded.course_id,
            name            = excluded.name,
            due_at          = excluded.due_at,
            html_url        = excluded.html_url,
            points_possible = excluded.points_possible,
            score           = excluded.score,
            is_submitted    = excluded.is_submitted,
            updated_at      = excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def grade_changes(conn) -> dict:
    """Each course's score change: ``{course_id: (current, previous)}``.

    ``previous`` comes from an earlier snapshot date and is None the first time a
    course is recorded. Implemented with a correlated subquery over the snapshots.
    """
    rows = conn.execute(
        """
        SELECT snapshots.course_id AS course_id,
               snapshots.score     AS current,
               (
                   SELECT earlier.score
                     FROM grade_snapshots AS earlier
                    WHERE earlier.course_id = snapshots.course_id
                      AND earlier.snapshot_date < snapshots.snapshot_date
                    ORDER BY earlier.snapshot_date DESC
                    LIMIT 1
               )                   AS previous
          FROM grade_snapshots AS snapshots
         WHERE snapshots.snapshot_date = (
                   SELECT MAX(newest.snapshot_date)
                     FROM grade_snapshots AS newest
                    WHERE newest.course_id = snapshots.course_id
               )
        """
    ).fetchall()
    return {row["course_id"]: (row["current"], row["previous"]) for row in rows}


def deadline_summary(conn, now=None, hours: float = 72) -> tuple:
    """Count unsubmitted work due within ``hours``: ``(assignments, courses)``.

    The window is calculated in SQL with julianday(), so each assignment's due
    date is compared against a reference time inside the database.
    """
    reference = normalize_due(now) or now or utc_now_iso()
    row = conn.execute(
        """
        SELECT COUNT(*)                  AS total,
               COUNT(DISTINCT course_id) AS courses
          FROM assignments
         WHERE is_submitted = 0
           AND due_at IS NOT NULL
           AND julianday(due_at) - julianday(?) BETWEEN 0 AND ?
        """,
        (reference, hours / 24.0),
    ).fetchone()
    return (row["total"], row["courses"])


def course_deadline_report(conn, now=None, hours: float = 72) -> list:
    """Per-course counts of outstanding work: ``[(course_name, count), ...]``.

    A JOIN between courses and assignments, grouped in SQL so the database does
    the counting instead of the bot.
    """
    reference = normalize_due(now) or now or utc_now_iso()
    rows = conn.execute(
        """
        SELECT courses.name          AS course_name,
               COUNT(assignments.id) AS due_count
          FROM courses
          JOIN assignments ON assignments.course_id = courses.id
         WHERE assignments.is_submitted = 0
           AND assignments.due_at IS NOT NULL
           AND julianday(assignments.due_at) - julianday(?) BETWEEN 0 AND ?
         GROUP BY courses.id
         ORDER BY due_count DESC, courses.name ASC
        """,
        (reference, hours / 24.0),
    ).fetchall()
    return [(row["course_name"], row["due_count"]) for row in rows]
