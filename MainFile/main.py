import os
import sys
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DEFAULT_CANVAS_BASE_URL = "https://canvas.unf.edu"


def require_env(name: str, hint: str) -> str:
    """Return an environment variable or exit with a friendly setup message."""
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    print(f"Missing required setting: {name}")
    print("Add it to the .env file in the project folder (next to README.md).")
    print(f"How to get it: {hint}")
    sys.exit(1)


def require_env_int(name: str, hint: str) -> int:
    """Like require_env, but for numeric Discord IDs."""
    value = require_env(name, hint)
    if value.isdigit():
        return int(value)
    print(f"{name} must be a numeric Discord ID, but it was: {value}")
    print(f"How to get it: {hint}")
    sys.exit(1)


def default_term_filter() -> str:
    """UNF-style Canvas term code: Fall = YYYY80, Spring = YYYY10, Summer = YYYY50."""
    now = datetime.now()
    if 8 <= now.month <= 12:
        term = "80"
    elif 1 <= now.month <= 5:
        term = "10"
    else:
        term = "50"
    return f"{now.year}{term}"


def resolve_term(term_filter=None) -> str:
    """Use the given term, then CANVAS_TERM from .env, then auto-detect."""
    if term_filter:
        return term_filter
    override = (os.getenv("CANVAS_TERM") or "").strip()
    return override or default_term_filter()


class CanvasError(Exception):
    """A Canvas problem we can explain to the user (bad token, wrong URL, offline)."""


class CanvasClient:
    def __init__(self):
        self.web_url = (os.getenv("CANVAS_BASE_URL") or DEFAULT_CANVAS_BASE_URL).strip().rstrip("/")
        self.base_url = f"{self.web_url}/api/v1"
        self.token = require_env(
            "CANVAS_TOKEN",
            "Canvas -> Account -> Settings -> New Access Token (README.md, step 5)",
        )
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def _get(self, endpoint, params=None):
        if params is None:
            params = {}
        params.setdefault("per_page", 100)

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        all_results = []

        while url:
            try:
                response = requests.get(url, headers=self.headers, params=params, timeout=30)
                response.raise_for_status()
            except requests.exceptions.HTTPError:
                status = response.status_code
                if status in (401, 403):
                    raise CanvasError(
                        "Canvas rejected your access token (HTTP 401/403). Create a new token "
                        "in Canvas Settings and update CANVAS_TOKEN in .env."
                    ) from None
                raise CanvasError(f"Canvas request failed with HTTP {status} ({endpoint}).") from None
            except requests.exceptions.RequestException as error:
                raise CanvasError(f"Couldn't reach Canvas at {self.web_url}: {error}") from None

            data = response.json()
            if isinstance(data, list):
                all_results.extend(data)
            else:
                return data

            if "next" in response.links:
                url = response.links["next"]["url"]
                params = {}
            else:
                url = None

        return all_results

    def get_active_courses(self, term_filter=None) -> list[dict]:
        term_filter = resolve_term(term_filter)
        params = {
            "enrollment_state": "active",
            "include[]": "total_scores",
        }
        endpoint = "courses"
        response = self._get(endpoint, params)
        active_courses = []

        for data in response:
            name = data.get("name") or ""
            if term_filter not in name:
                continue

            enrollments = data.get("enrollments") or []
            current_grade = None
            if enrollments:
                current_grade = enrollments[0].get("computed_current_score")

            active_courses.append({
                "id": data.get("id"),
                "name": name,
                "course_code": data.get("course_code"),
                # current_score stays numeric for storage; current_grade is display-ready.
                "current_score": current_grade,
                "current_grade": f"{current_grade}%" if current_grade is not None else "N/A",
            })
        return active_courses

    def get_course_assignments(self, course_id) -> list[dict]:
        params = {"include[]": "submission", "order_by": "due_at"}
        endpoint = f"courses/{course_id}/assignments"
        response = self._get(endpoint, params)
        all_data = []
        for data in response:
            sub = data.get("submission") or {}
            all_data.append({
                "id": data.get("id"),
                "name": data.get("name"),
                "due_at": data.get("due_at"),
                "url": data.get("html_url"),
                "points_possible": data.get("points_possible"),
                "score": sub.get("score"),
                "is_submitted": sub.get("workflow_state") in ["submitted", "graded"],
                "is_graded": sub.get("workflow_state") == "graded",
            })
        return all_data

    def get_all_assignments(self, term_filter=None) -> dict[str, list[dict]]:
        term_filter = resolve_term(term_filter)
        active_courses = self.get_active_courses(term_filter=term_filter)
        all_info = {}

        for course in active_courses:
            class_name = course["name"]
            class_id = course["id"]

            assignments = self.get_course_assignments(class_id)
            # Carry the course id along so callers can store assignments per course.
            for assignment in assignments:
                assignment["course_id"] = class_id
            all_info[class_name] = assignments

        return all_info

    def get_urgent_assignments(self, max_hours=72, term_filter=None) -> list[dict]:
        now = datetime.now(timezone.utc)
        all_courses = self.get_all_assignments(term_filter=term_filter)
        urgent = []

        for course_name, assignments in all_courses.items():
            for item in assignments:
                str_due_date = item.get("due_at")

                if str_due_date is None or item.get("is_submitted"):
                    continue

                delta_due_date = datetime.fromisoformat(str_due_date.replace("Z", "+00:00"))
                delta = delta_due_date - now
                hours_remaining = delta.total_seconds() / 3600

                if 0 <= hours_remaining <= max_hours:
                    item["course"] = course_name
                    item["hours_left"] = round(hours_remaining, 1)
                    urgent.append(item)

        def get_hours(item):
            return item["hours_left"]

        urgent.sort(key=get_hours)
        return urgent
