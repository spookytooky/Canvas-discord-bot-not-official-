"""Tests for the SQLite storage layer.

Run them from the project root:

    python -m unittest discover -s tests -v

Each test uses its own temporary database file, so nothing here touches
MainFile/canvas.db or your real Canvas data.
"""

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_FILE = PROJECT_ROOT / "MainFile"

# The bot adds MainFile to the path the same way; do it before importing storage.
if str(MAIN_FILE) not in sys.path:
    sys.path.insert(0, str(MAIN_FILE))

import storage  # noqa: E402  (import must follow the sys.path setup above)


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"

    def tearDown(self):
        self._tmp.cleanup()

    def connect(self):
        return storage.session(self.db_path)


class SchemaTests(StorageTestCase):
    def test_database_has_expected_tables(self):
        with self.connect() as conn:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertTrue({"courses", "assignments", "grade_snapshots"} <= names)

    def test_normalize_due_converts_to_utc_and_rejects_junk(self):
        self.assertEqual(storage.normalize_due("2026-10-08T16:00:00Z"), "2026-10-08 16:00:00")
        self.assertEqual(storage.normalize_due("2026-10-08T16:00:00-04:00"), "2026-10-08 20:00:00")
        self.assertIsNone(storage.normalize_due(None))
        self.assertIsNone(storage.normalize_due(""))
        self.assertIsNone(storage.normalize_due("not a date"))


class CourseTests(StorageTestCase):
    def test_save_courses_upserts_instead_of_duplicating(self):
        courses = [{"id": 1, "name": "COP3530", "course_code": "COP3530", "current_score": 91.5}]

        with self.connect() as conn:
            storage.save_courses(conn, courses)

        updated = [{"id": 1, "name": "Data Structures", "course_code": "COP3530", "current_score": 93.0}]
        with self.connect() as conn:
            storage.save_courses(conn, updated)
            rows = conn.execute("SELECT id, name FROM courses").fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Data Structures")

    def test_grade_snapshot_is_recorded_once_per_day(self):
        courses = [{"id": 1, "name": "COP3530", "course_code": "COP3530", "current_score": 91.0}]

        for _ in range(3):
            with self.connect() as conn:
                storage.save_courses(conn, courses)

        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM grade_snapshots").fetchone()[0]

        self.assertEqual(count, 1, "repeat runs on the same day should update one row, not add more")

    def test_grade_change_compares_latest_snapshot_with_the_previous_day(self):
        courses = [{"id": 1, "name": "COP3530", "course_code": "COP3530", "current_score": 91.0}]

        with self.connect() as conn:
            storage.save_courses(conn, courses)

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO grade_snapshots (course_id, score, snapshot_date, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (1, 88.0, "2020-01-01", "2020-01-01 12:00:00"),
            )

        with self.connect() as conn:
            changes = storage.grade_changes(conn)

        self.assertEqual(changes[1], (91.0, 88.0))

    def test_grade_change_has_no_previous_value_on_first_recording(self):
        courses = [{"id": 1, "name": "COP3530", "course_code": "COP3530", "current_score": 91.0}]

        with self.connect() as conn:
            storage.save_courses(conn, courses)

        with self.connect() as conn:
            changes = storage.grade_changes(conn)

        self.assertEqual(changes[1], (91.0, None))


class AssignmentTests(StorageTestCase):
    COURSES = [
        {"id": 1, "name": "COP3530", "course_code": "COP3530", "current_score": None},
        {"id": 2, "name": "MAC2312", "course_code": "MAC2312", "current_score": None},
    ]

    ASSIGNMENTS = [
        {
            "id": 10,
            "course_id": 1,
            "course": "202680.COP3530.83047:Data Structures",
            "name": "Due soon",
            "due_at": "2026-10-08T16:00:00Z",
            "url": "https://canvas.unf.edu/courses/1/assignments/10",
            "points_possible": 100.0,
            "score": None,
            "is_submitted": False,
        },
        {
            "id": 11,
            "course_id": 2,
            "course": "202680.MAC2312.82382:(GM) Calculus II",
            "name": "Due in two weeks",
            "due_at": "2026-10-20T16:00:00Z",
            "url": None,
            "points_possible": 50.0,
            "score": None,
            "is_submitted": False,
        },
        {
            "id": 12,
            "course_id": 1,
            "course": "202680.COP3530.83047:Data Structures",
            "name": "Already handed in",
            "due_at": "2026-10-08T16:00:00Z",
            "url": None,
            "points_possible": 10.0,
            "score": 10.0,
            "is_submitted": True,
        },
    ]

    def test_save_assignments_creates_the_missing_course_row(self):
        """The /urgent path only knows course ids and names, so the FK must still resolve."""
        with self.connect() as conn:
            written = storage.save_assignments(conn, [self.ASSIGNMENTS[0]])
            course = conn.execute("SELECT name FROM courses WHERE id = 1").fetchone()

        self.assertEqual(written, 1)
        self.assertEqual(course["name"], "202680.COP3530.83047:Data Structures")

    def test_save_assignments_skips_items_without_an_id(self):
        with self.connect() as conn:
            written = storage.save_assignments(conn, [{"course_id": 1, "name": "No id"}])

        self.assertEqual(written, 0)

    def test_deadline_summary_windows_by_due_date_and_skips_submitted_work(self):
        with self.connect() as conn:
            storage.save_courses(conn, self.COURSES)
            storage.save_assignments(conn, self.ASSIGNMENTS)
            total, course_count = storage.deadline_summary(conn, now="2026-10-07 16:00:00", hours=72)

        self.assertEqual(total, 1)
        self.assertEqual(course_count, 1)

    def test_course_deadline_report_groups_counts_by_course(self):
        with self.connect() as conn:
            storage.save_courses(conn, self.COURSES)
            storage.save_assignments(conn, self.ASSIGNMENTS)
            report = storage.course_deadline_report(conn, now="2026-10-07 16:00:00", hours=72)

        self.assertEqual(report, [("COP3530", 1)])

    def test_course_deadline_report_uses_the_real_course_name_after_a_grades_run(self):
        with self.connect() as conn:
            storage.save_assignments(conn, self.ASSIGNMENTS)
            storage.save_courses(conn, self.COURSES)
            report = storage.course_deadline_report(conn, now="2026-10-07 16:00:00", hours=72)

        self.assertEqual(report, [("COP3530", 1)])

    def test_assignments_are_upserted_rather_than_duplicated(self):
        with self.connect() as conn:
            storage.save_courses(conn, self.COURSES)
            storage.save_assignments(conn, self.ASSIGNMENTS)
            storage.save_assignments(conn, self.ASSIGNMENTS)
            stored = conn.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]

        self.assertEqual(stored, 3)


if __name__ == "__main__":
    unittest.main()
