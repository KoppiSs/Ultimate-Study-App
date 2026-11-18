"""
Course Manager Module.

Provides standard CRUD functions for academic course management.
Persists course data to a JSON file at data/courses.json.
Schema includes:
- course_id (string)
- name (string)
- status (string: 'Past', 'Active', 'Upcoming')
- instructors (list of dicts with 'name' and 'email')
- tas (list of dicts with 'name' and 'email')
- schedule (list of dicts with 'day', 'start_time', 'end_time', 'location')
- linked_subject (string)
- start_date (string)
- end_date (string)
- exam_date (string)
"""

from datetime import datetime, date
import json
import os
import re
from typing import Any, Dict, List, Optional, Union
import uuid

from settings_manager import get_storage_paths, write_json

# Directory & Storage Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_COURSES_FILE = os.path.join(DEFAULT_DATA_DIR, "courses.json")
COURSES_FILE = os.getenv("COURSES_FILE", DEFAULT_COURSES_FILE)

VALID_STATUSES = {"Past", "Active", "Upcoming"}


def _ensure_courses_file(filepath: Optional[str] = None) -> str:
    """
    Ensures that the target directory and the JSON file exist.
    If the file does not exist, creates the parent directory and initializes
    the file with an empty list `[]`.
    Returns the resolved absolute path to the courses file.
    """
    path = filepath or get_storage_paths()["courses_file"]
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)

    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

    if not os.path.exists(path):
        write_json(path, [])

    return path


def _parse_schedule_string(schedule_str: str) -> List[Dict[str, str]]:
    """
    Safely migrates legacy string schedules (e.g., 'Wed 14:15-16:00, Fri 10:15-12:00')
    into a structured list of schedule block dicts.
    """
    if not schedule_str or not isinstance(schedule_str, str):
        return []

    day_map = {
        "mon": "Monday", "monday": "Monday",
        "tue": "Tuesday", "tues": "Tuesday", "tuesday": "Tuesday",
        "wed": "Wednesday", "wednesday": "Wednesday",
        "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "thursday": "Thursday",
        "fri": "Friday", "friday": "Friday",
        "sat": "Saturday", "saturday": "Saturday",
        "sun": "Sunday", "sunday": "Sunday",
    }

    blocks: List[Dict[str, str]] = []
    chunks = [c.strip() for c in re.split(r"[,;]", schedule_str) if c.strip()]
    for chunk in chunks:
        match = re.search(r"([A-Za-z]+)\s*(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})", chunk)
        if match:
            raw_day, start_t, end_t = match.groups()
            day_clean = day_map.get(raw_day.lower(), raw_day.capitalize())
            location = chunk[match.end():].strip().lstrip("()[]- ")
            blocks.append({
                "day": day_clean,
                "start_time": start_t,
                "end_time": end_t,
                "location": location,
            })
        else:
            blocks.append({
                "day": "Monday",
                "start_time": "",
                "end_time": "",
                "location": chunk,
            })
    return blocks


def compute_course_status(
    course_data: Dict[str, Any],
    current_date: Optional[Union[datetime, date, str]] = None,
) -> str:
    """
    Computes effective status ('Upcoming', 'Active', 'Past') based on start_date, end_date and current_date.
    - If current date is yet to be the start date of the course (< start_date): 'Upcoming'
    - If current date is past the end date of the course (> end_date): 'Past'
    - If start_date <= current date <= end_date: 'Active'
    - If dates are not set or unparseable, falls back to course's configured status or 'Active'.
    """
    if current_date is None:
        curr_d = datetime.now().date()
    elif isinstance(current_date, datetime):
        curr_d = current_date.date()
    elif isinstance(current_date, date):
        curr_d = current_date
    elif isinstance(current_date, str):
        try:
            curr_d = datetime.strptime(current_date[:10], "%Y-%m-%d").date()
        except Exception:
            curr_d = datetime.now().date()
    else:
        curr_d = datetime.now().date()

    start_d: Optional[date] = None
    end_d: Optional[date] = None

    raw_start = str(course_data.get("start_date") or "").strip()
    if raw_start:
        try:
            start_d = datetime.strptime(raw_start[:10], "%Y-%m-%d").date()
        except Exception:
            start_d = None

    raw_end = str(course_data.get("end_date") or "").strip()
    if raw_end:
        try:
            end_d = datetime.strptime(raw_end[:10], "%Y-%m-%d").date()
        except Exception:
            end_d = None

    # Condition 1: Current date is yet to be start date
    if start_d is not None and curr_d < start_d:
        return "Upcoming"

    # Condition 2: Current date is past end date
    if end_d is not None and curr_d > end_d:
        return "Past"

    # Condition 3: Both dates present, and current date is in between
    if start_d is not None and end_d is not None and start_d <= curr_d <= end_d:
        return "Active"

    # Condition 4: Only start_date present, current date >= start_date
    if start_d is not None and end_d is None:
        return "Active"

    # Condition 5: Only end_date present, current date <= end_date
    if end_d is not None and start_d is None:
        return "Active"

    # Fallback to configured status
    cfg_status = str(course_data.get("status") or "Active").strip()
    return cfg_status if cfg_status in VALID_STATUSES else "Active"


def _validate_and_normalize_course(course_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates and normalizes course data to conform to the updated JSON schema:
    - course_id (string)
    - name (string)
    - status (string: 'Past', 'Active', 'Upcoming')
    - instructors (list of dicts with 'name' and 'email')
    - tas (list of dicts with 'name' and 'email')
    - schedule (list of dicts with 'day', 'start_time', 'end_time', 'location')
    - linked_subject (string)
    - start_date (string)
    - end_date (string)
    - exam_date (string)

    Safely handles and migrates legacy course fields:
    - instructor (dict) -> instructors (list of dicts)
    - schedule (string/list of strings) -> schedule (list of dicts)
    - missing linked_subject -> empty string
    - missing start_date / end_date -> empty string
    """
    course_id = str(course_data.get("course_id") or "").strip()
    if not course_id:
        course_id = f"course_{uuid.uuid4().hex[:8]}"

    name = str(course_data.get("name") or "").strip()

    # Instructors normalization (migrates legacy 'instructor' dict)
    instructors: List[Dict[str, str]] = []
    raw_instructors = course_data.get("instructors")
    if isinstance(raw_instructors, list):
        for inst in raw_instructors:
            if isinstance(inst, dict):
                i_name = str(inst.get("name") or "").strip()
                i_email = str(inst.get("email") or "").strip()
                if i_name or i_email:
                    instructors.append({"name": i_name, "email": i_email})
    elif isinstance(course_data.get("instructor"), dict):
        legacy_inst = course_data["instructor"]
        i_name = str(legacy_inst.get("name") or "").strip()
        i_email = str(legacy_inst.get("email") or "").strip()
        if i_name or i_email:
            instructors.append({"name": i_name, "email": i_email})

    # TAs normalization
    tas: List[Dict[str, str]] = []
    raw_tas = course_data.get("tas")
    if isinstance(raw_tas, list):
        for ta in raw_tas:
            if isinstance(ta, dict):
                ta_name = str(ta.get("name") or "").strip()
                ta_email = str(ta.get("email") or "").strip()
                if ta_name or ta_email:
                    tas.append({"name": ta_name, "email": ta_email})

    # Schedule normalization (migrates legacy string or list of strings to list of dicts)
    schedule: List[Dict[str, str]] = []
    raw_schedule = course_data.get("schedule")
    if isinstance(raw_schedule, list):
        for item in raw_schedule:
            if isinstance(item, dict):
                s_day = str(item.get("day") or "Monday").strip()
                s_st = str(item.get("start_time") or "").strip()
                s_et = str(item.get("end_time") or "").strip()
                s_loc = str(item.get("location") or "").strip()
                if s_st or s_et or s_loc or s_day:
                    schedule.append({
                        "day": s_day,
                        "start_time": s_st,
                        "end_time": s_et,
                        "location": s_loc,
                    })
            elif isinstance(item, str):
                parsed = _parse_schedule_string(item)
                schedule.extend(parsed)
    elif isinstance(raw_schedule, str) and raw_schedule.strip():
        schedule = _parse_schedule_string(raw_schedule.strip())

    # Linked Subject normalization
    raw_linked = course_data.get("linked_subject", "")
    linked_subject = str(raw_linked or "").strip()
    if linked_subject == "None":
        linked_subject = ""

    # Dates normalization
    start_date = str(course_data.get("start_date") or "").strip()
    end_date = str(course_data.get("end_date") or "").strip()
    exam_date = str(course_data.get("exam_date") or "").strip()

    # Dynamic status computation based on start/end dates
    status = compute_course_status(course_data)

    return {
        "course_id": course_id,
        "name": name,
        "status": status,
        "instructors": instructors,
        "tas": tas,
        "schedule": schedule,
        "linked_subject": linked_subject,
        "start_date": start_date,
        "end_date": end_date,
        "exam_date": exam_date,
    }


def load_courses(filepath: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads all courses from the courses JSON file.
    Safely migrates and standardizes any legacy course entries.
    If migration occurred, persists the normalized records back to disk.
    """
    target_path = _ensure_courses_file(filepath)
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []
            raw_courses = json.loads(content)
            if not isinstance(raw_courses, list):
                return []

        normalized_courses = []
        needs_migration_save = False

        for c in raw_courses:
            if not isinstance(c, dict):
                continue
            norm = _validate_and_normalize_course(c)
            # Detect if legacy fields were migrated
            if (
                "instructor" in c
                or not isinstance(c.get("schedule"), list)
                or "linked_subject" not in c
                or "start_date" not in c
                or "end_date" not in c
            ):
                needs_migration_save = True
            normalized_courses.append(norm)

        if needs_migration_save:
            write_json(target_path, normalized_courses)

        return normalized_courses
    except (json.JSONDecodeError, OSError):
        return []


def save_course(course_data: Dict[str, Any], filepath: Optional[str] = None) -> Dict[str, Any]:
    """
    Saves or updates a course in the courses JSON file.
    If course_id matches an existing course, updates it. Otherwise appends the new course.
    Returns the normalized course dictionary.
    """
    target_path = _ensure_courses_file(filepath)
    normalized = _validate_and_normalize_course(course_data)
    courses = load_courses(target_path)

    existing_idx = None
    for idx, c in enumerate(courses):
        if str(c.get("course_id") or "").strip() == str(normalized.get("course_id") or "").strip():
            existing_idx = idx
            break

    if existing_idx is not None:
        courses[existing_idx] = normalized
    else:
        courses.append(normalized)

    write_json(target_path, courses)

    return normalized


def delete_course(course_id: str, filepath: Optional[str] = None) -> bool:
    """
    Deletes a course with the specified course_id from the courses JSON file.
    Returns True if the course was found and deleted, False otherwise.
    """
    target_path = _ensure_courses_file(filepath)
    courses = load_courses(target_path)

    initial_len = len(courses)
    filtered_courses = [c for c in courses if str(c.get("course_id")) != str(course_id)]

    if len(filtered_courses) < initial_len:
        write_json(target_path, filtered_courses)
        return True

    return False


def get_course_by_id(course_id: str, filepath: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single course by course_id, or None if not found.
    """
    courses = load_courses(filepath)
    for c in courses:
        if str(c.get("course_id")) == str(course_id):
            return c
    return None
