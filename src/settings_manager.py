"""
Settings Manager Module.

Provides centralized management for application settings, including:
1. Vault Directory Mapping: configure and resolve the Markdown notes root folder.
2. Global Export/Backup: archive data JSONs and notes Markdown files into a timestamped zip.
3. Orphaned Data Cleanup: scan and purge study blocks referencing deleted courses or notes.
4. Date & Time Formatting: global formatting for 24h/12h clocks and preferred date styles (YYYY-MM-DD vs DD/MM/YYYY).
"""

from datetime import datetime, date, time
import io
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import zipfile

# Directory & Storage Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_NOTES_DIR = os.path.join(DEFAULT_DATA_DIR, "notes")
DEFAULT_SESSIONS_DIR = os.path.join(DEFAULT_DATA_DIR, "sessions")
DEFAULT_COURSES_FILE = os.path.join(DEFAULT_DATA_DIR, "courses.json")
DEFAULT_SETTINGS_FILE = os.path.join(DEFAULT_DATA_DIR, "settings.json")

SETTINGS_FILE = os.getenv("SETTINGS_FILE", DEFAULT_SETTINGS_FILE)

# (Default relative to the study data folder, environment override, directory)
STORAGE_PATHS = {
    "notes_vault_dir": ("notes", "NOTES_DIR", True),
    "courses_file": ("courses.json", "COURSES_FILE", False),
    "sessions_dir": ("sessions", "SESSIONS_DIR", True),
    "legacy_study_blocks_file": ("study_blocks.json", "STUDY_BLOCKS_FILE", False),
    "study_db_file": (os.path.join("notes", "study_core.db"), "STUDY_DB_FILE", False),
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "data_dir": "",
    **dict.fromkeys(STORAGE_PATHS, ""),
    "wikipedia_enabled": False,
    "tutor_notes_enabled": True,
    "zim_path": "",
    "ollama_url": "",
    "ollama_model": "",
    "time_format": "24h",  # '24h' or '12h'
    "date_format": "YYYY-MM-DD",  # 'YYYY-MM-DD' or 'DD/MM/YYYY'
}


def load_settings(filepath: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads application settings from settings.json.
    Returns merged defaults if file does not exist or is invalid.
    """
    path = os.path.abspath(filepath or SETTINGS_FILE)
    settings = dict(DEFAULT_SETTINGS)

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    settings.update(data)
        except Exception:
            pass

    return settings


def resolve_path(path: str, base: str = BASE_DIR) -> str:
    return os.path.abspath(os.path.join(base, os.path.expanduser(path)))


def get_storage_paths(settings: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    settings = load_settings() if settings is None else settings
    root = resolve_path(settings.get("data_dir") or DEFAULT_DATA_DIR)
    paths = {"data_dir": root}
    for key, (default, env_var, _) in STORAGE_PATHS.items():
        configured = settings.get(key)
        env_path = os.getenv(env_var) if not settings.get("data_dir") else None
        paths[key] = resolve_path(configured or env_path or default, BASE_DIR if env_path and not configured else root)
    return paths


def validate_storage_paths(settings: Dict[str, Any]) -> None:
    paths = get_storage_paths(settings)
    for key, path in paths.items():
        is_directory = key == "data_dir" or STORAGE_PATHS[key][2]
        has_expected_type = os.path.isdir if is_directory else os.path.isfile
        if os.path.exists(path) and not has_expected_type(path):
            raise ValueError(f"Expected a {'folder' if is_directory else 'file'} at {path}")
        parent = os.path.dirname(path)
        while not os.path.exists(parent) and parent != os.path.dirname(parent):
            parent = os.path.dirname(parent)
        if not os.path.isdir(parent):
            raise ValueError(f"A parent of {path} is not a folder")
    files = [os.path.normcase(paths[key]) for key, (_, _, is_dir) in STORAGE_PATHS.items() if not is_dir]
    if len(set(files + [os.path.normcase(resolve_path(SETTINGS_FILE))])) != len(files) + 1:
        raise ValueError("Courses, legacy study blocks, SQLite, and settings need separate files.")


def write_json(path: str, payload: Any) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def save_settings(new_settings: Dict[str, Any], filepath: Optional[str] = None) -> None:
    """
    Persists settings dictionary to settings.json.
    """
    path = os.path.abspath(filepath or SETTINGS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    current = load_settings(path)
    current.update(new_settings)

    # Validate options
    if current.get("time_format") not in ["24h", "12h"]:
        current["time_format"] = "24h"
    if current.get("date_format") not in ["YYYY-MM-DD", "DD/MM/YYYY"]:
        current["date_format"] = "YYYY-MM-DD"

    write_json(path, current)


def get_notes_vault_dir() -> str:
    """Return the configured Markdown folder."""
    return get_storage_paths()["notes_vault_dir"]


def set_notes_vault_dir(new_path: str, create_if_missing: bool = True) -> str:
    """
    Updates the root folder for the Markdown notes vault.
    Creates directory if requested, and saves settings.
    """
    if not new_path or not new_path.strip():
        new_path = DEFAULT_NOTES_DIR

    target_dir = os.path.abspath(new_path.strip())
    if create_if_missing:
        os.makedirs(target_dir, exist_ok=True)

    save_settings({"notes_vault_dir": target_dir})
    return target_dir


def get_time_format() -> str:
    """Returns '24h' or '12h'."""
    return load_settings().get("time_format", "24h")


def set_time_format(time_fmt: str) -> None:
    """Sets time format to '24h' or '12h'."""
    save_settings({"time_format": "12h" if time_fmt == "12h" else "24h"})


def get_date_format() -> str:
    """Returns 'YYYY-MM-DD' or 'DD/MM/YYYY'."""
    return load_settings().get("date_format", "YYYY-MM-DD")


def set_date_format(date_fmt: str) -> None:
    """Sets date format to 'YYYY-MM-DD' or 'DD/MM/YYYY'."""
    save_settings({"date_format": "DD/MM/YYYY" if date_fmt == "DD/MM/YYYY" else "YYYY-MM-DD"})


# ==============================================================================
# DATE & TIME FORMATTING HELPERS
# ==============================================================================

def format_time(
    time_val: Union[str, time, datetime, None],
    time_fmt: Optional[str] = None,
) -> str:
    """
    Formats a time into either 24-hour ('14:00') or 12-hour ('02:00 PM') display string.
    Gracefully handles existing string variations and fallback to original.
    """
    if time_val is None:
        return ""
    if isinstance(time_val, str) and not time_val.strip():
        return ""

    fmt = time_fmt or get_time_format()

    hour: Optional[int] = None
    minute: Optional[int] = None

    if isinstance(time_val, datetime):
        hour, minute = time_val.hour, time_val.minute
    elif isinstance(time_val, time):
        hour, minute = time_val.hour, time_val.minute
    elif isinstance(time_val, str):
        cleaned = time_val.strip()
        # Check standard 24h: HH:MM or HH:MM:SS
        m24 = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", cleaned)
        if m24:
            hour, minute = int(m24.group(1)), int(m24.group(2))
        else:
            # Check 12h: H:MM AM/PM or HH:MM AM/PM
            m12 = re.match(r"^(\d{1,2}):(\d{2})\s*([AaPp][Mm])$", cleaned)
            if m12:
                h, m, period = int(m12.group(1)), int(m12.group(2)), m12.group(3).upper()
                if period == "PM" and h < 12:
                    h += 12
                elif period == "AM" and h == 12:
                    h = 0
                hour, minute = h, m
            else:
                return cleaned

    if hour is None or minute is None:
        return str(time_val)

    if fmt == "12h":
        period = "PM" if hour >= 12 else "AM"
        display_hour = hour % 12
        if display_hour == 0:
            display_hour = 12
        return f"{display_hour:02d}:{minute:02d} {period}"
    else:
        return f"{hour:02d}:{minute:02d}"


def format_date(
    date_val: Union[str, date, datetime, None],
    date_fmt: Optional[str] = None,
) -> str:
    """
    Formats a date into either 'YYYY-MM-DD' or 'DD/MM/YYYY'.
    Gracefully handles existing string dates, falling back to original if unparseable.
    """
    if date_val is None:
        return ""
    if isinstance(date_val, str) and not date_val.strip():
        return ""

    fmt = date_fmt or get_date_format()

    dt: Optional[date] = None
    if isinstance(date_val, datetime):
        dt = date_val.date()
    elif isinstance(date_val, date):
        dt = date_val
    elif isinstance(date_val, str):
        cleaned = date_val.strip()
        # Try YYYY-MM-DD
        m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", cleaned)
        if m_iso:
            try:
                dt = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            except ValueError:
                pass
        if dt is None:
            # Try DD/MM/YYYY
            m_dmy = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", cleaned)
            if m_dmy:
                try:
                    dt = date(int(m_dmy.group(3)), int(m_dmy.group(2)), int(m_dmy.group(1)))
                except ValueError:
                    pass
        if dt is None:
            return cleaned

    if dt is None:
        return str(date_val)

    if fmt == "DD/MM/YYYY":
        return dt.strftime("%d/%m/%Y")
    else:
        return dt.strftime("%Y-%m-%d")


def format_date_short(
    date_val: Union[str, date, datetime, None],
    date_fmt: Optional[str] = None,
) -> str:
    """
    Formats a date as a compact label suitable for chart axes (e.g. '19/10' vs '10/19').
    """
    if date_val is None:
        return ""
    fmt = date_fmt or get_date_format()

    dt: Optional[date] = None
    if isinstance(date_val, datetime):
        dt = date_val.date()
    elif isinstance(date_val, date):
        dt = date_val
    elif isinstance(date_val, str):
        cleaned = date_val.strip()
        m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", cleaned)
        if m_iso:
            try:
                dt = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            except ValueError:
                pass
        if dt is None:
            return cleaned

    if dt is None:
        return str(date_val)

    if fmt == "DD/MM/YYYY":
        return dt.strftime("%d/%m")
    else:
        return dt.strftime("%m/%d")


def format_datetime(
    dt_val: Union[str, datetime, None],
    time_fmt: Optional[str] = None,
    date_fmt: Optional[str] = None,
) -> str:
    """
    Formats a full datetime string or object according to preferred date and time formats.
    """
    if dt_val is None:
        return ""
    if isinstance(dt_val, str) and not dt_val.strip():
        return ""

    t_fmt = time_fmt or get_time_format()
    d_fmt = date_fmt or get_date_format()

    parsed_dt: Optional[datetime] = None
    if isinstance(dt_val, datetime):
        parsed_dt = dt_val
    elif isinstance(dt_val, str):
        for pattern in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
            try:
                parsed_dt = datetime.strptime(dt_val.strip(), pattern)
                break
            except ValueError:
                continue

    if not parsed_dt:
        return str(dt_val)

    formatted_d = format_date(parsed_dt.date(), date_fmt=d_fmt)
    formatted_t = format_time(parsed_dt.time(), time_fmt=t_fmt)
    return f"{formatted_d} {formatted_t}".strip()


# ==============================================================================
# GLOBAL EXPORT & BACKUP ARCHIVE
# ==============================================================================

def create_vault_archive_zip(
    notes_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Zips the data/ (JSONs) and notes/ (Markdown) folders into a single timestamped in-memory zip archive.
    Explicitly excludes heavy Kiwix ZIM binaries, SQLite databases, and executables.
    Returns: (zip_bytes, archive_stats)
    """
    target_notes = os.path.abspath(notes_dir or get_notes_vault_dir())
    target_data = os.path.abspath(data_dir or DEFAULT_DATA_DIR)

    zip_buffer = io.BytesIO()
    notes_count = 0
    json_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if data_dir is None:
            storage = get_storage_paths()
            json_files = [
                (storage["courses_file"], "data/courses.json"),
                (storage["legacy_study_blocks_file"], "data/study_blocks.json"),
            ]
            if os.path.isdir(storage["sessions_dir"]):
                with os.scandir(storage["sessions_dir"]) as entries:
                    json_files.extend(
                        (entry.path, f"data/sessions/{entry.name}")
                        for entry in entries if entry.is_file() and entry.name.endswith(".json")
                    )
            for source, archive_name in json_files:
                if os.path.isfile(source):
                    zf.write(source, arcname=archive_name)
                    total_uncompressed_bytes += os.path.getsize(source)
                    json_count += 1
        else:
            # 1. Archive data JSON files (courses, study_blocks, sessions, settings)
            if os.path.exists(target_data):
                for root, dirs, files in os.walk(target_data):
                    # Skip heavy directories
                    dirs[:] = [d for d in dirs if d not in ["zim", "__pycache__", ".git"]]
                    # Also skip the notes directory if it is located inside target_data (we handle notes separately)
                    if os.path.abspath(root) == target_notes or os.path.abspath(root).startswith(target_notes + os.sep):
                        continue

                    for file in files:
                        # Strictly archive JSON data files and metadata configs
                        if file.endswith(".json"):
                            full_path = os.path.join(root, file)
                            try:
                                rel_to_data = os.path.relpath(full_path, target_data)
                                archive_arcname = os.path.join("data", rel_to_data).replace("\\", "/")
                                file_size = os.path.getsize(full_path)
                                zf.write(full_path, arcname=archive_arcname)
                                json_count += 1
                                total_uncompressed_bytes += file_size
                            except Exception:
                                continue

        # 2. Archive Markdown Vault notes
        if os.path.exists(target_notes):
            for root, dirs, files in os.walk(target_notes):
                dirs[:] = [d for d in dirs if d not in ["__pycache__", ".git", ".obsidian"]]
                for file in files:
                    if file.endswith((".md", ".markdown", ".txt")):
                        full_path = os.path.join(root, file)
                        try:
                            rel_to_notes = os.path.relpath(full_path, target_notes)
                            archive_arcname = os.path.join("notes", rel_to_notes).replace("\\", "/")
                            file_size = os.path.getsize(full_path)
                            zf.write(full_path, arcname=archive_arcname)
                            notes_count += 1
                            total_uncompressed_bytes += file_size
                        except Exception:
                            continue

    zip_bytes = zip_buffer.getvalue()
    stats = {
        "notes_count": notes_count,
        "json_count": json_count,
        "archive_size_bytes": len(zip_bytes),
        "uncompressed_bytes": total_uncompressed_bytes,
        "notes_vault_path": target_notes,
    }
    return zip_bytes, stats


def get_vault_stats(notes_dir: Optional[str] = None) -> Dict[str, Any]:
    """Returns quick overview stats for the active vault and data directories."""
    target_notes = os.path.abspath(notes_dir or get_notes_vault_dir())
    note_count = 0
    subject_count = 0
    total_size = 0

    if os.path.exists(target_notes):
        try:
            for entry in os.scandir(target_notes):
                if entry.is_dir() and not entry.name.startswith("."):
                    subject_count += 1
                    for sub_entry in os.scandir(entry.path):
                        if sub_entry.is_file() and sub_entry.name.endswith(".md"):
                            note_count += 1
                            total_size += sub_entry.stat().st_size
                elif entry.is_file() and entry.name.endswith(".md"):
                    note_count += 1
                    total_size += entry.stat().st_size
        except Exception:
            pass

    return {
        "vault_path": target_notes,
        "note_count": note_count,
        "subject_count": subject_count,
        "total_size_bytes": total_size,
    }


# ==============================================================================
# ORPHANED DATA SCAN & CLEANUP
# ==============================================================================

def find_orphaned_study_blocks(
    sessions_dir: Optional[str] = None,
    notes_dir: Optional[str] = None,
    courses_file: Optional[str] = None,
    data_dir: Optional[str] = None,
    legacy_file: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Scans study block JSONs in data/sessions/ and data/study_blocks.json.
    Identifies blocks referencing courses or notes that have been deleted.
    Returns list of dicts with:
    - block_id: str
    - file_path: str
    - topic: str
    - session_date: str
    - reasons: List[str] (descriptions of what was deleted)
    - orphaned_notes: List[str]
    - orphaned_course: Optional[str]
    """
    storage = get_storage_paths()
    override_root = data_dir or (os.path.dirname(sessions_dir) if sessions_dir else None)
    target_sessions = os.path.abspath(sessions_dir or (os.path.join(override_root, "sessions") if override_root else storage["sessions_dir"]))
    legacy_file = os.path.abspath(legacy_file or (os.path.join(override_root, "study_blocks.json") if override_root else storage["legacy_study_blocks_file"]))
    target_notes = os.path.abspath(notes_dir or get_notes_vault_dir())
    target_courses_path = os.path.abspath(courses_file or (os.path.join(override_root, "courses.json") if override_root else storage["courses_file"]))

    # 1. Collect valid courses
    valid_course_ids: Set[str] = set()
    valid_course_names: Set[str] = set()
    valid_course_subjects: Set[str] = set()
    if os.path.exists(target_courses_path):
        try:
            with open(target_courses_path, "r", encoding="utf-8") as f:
                c_data = json.load(f)
                if isinstance(c_data, list):
                    for c in c_data:
                        if isinstance(c, dict):
                            if c.get("course_id"):
                                valid_course_ids.add(c["course_id"])
                            if c.get("name"):
                                valid_course_names.add(c["name"].strip().lower())
                            if c.get("linked_subject"):
                                valid_course_subjects.add(c["linked_subject"].strip().lower())
        except Exception:
            pass

    # 2. Collect existing notes files in the active vault
    existing_note_rel_paths: Set[str] = set()
    if os.path.exists(target_notes):
        for root, _, files in os.walk(target_notes):
            for file in files:
                if file.endswith(".md"):
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, target_notes)
                    # Normalize slashes and lower for case-insensitive matching
                    existing_note_rel_paths.add(rel_p.replace("\\", "/").lower())
                    existing_note_rel_paths.add(file.lower())

    orphaned_blocks: List[Dict[str, Any]] = []

    # 3. Helper to inspect a block dict
    def check_block(b: Dict[str, Any], filepath: str) -> Optional[Dict[str, Any]]:
        bid = b.get("id") or os.path.basename(filepath)
        topic = b.get("topic", "Untitled Block")
        reasons: List[str] = []
        orph_notes: List[str] = []
        orph_course: Optional[str] = None

        # Check linked notes
        raw_notes: List[str] = []
        if isinstance(b.get("linked_notes"), list):
            raw_notes.extend([str(n) for n in b["linked_notes"] if n])
        if b.get("linked_note") and isinstance(b["linked_note"], str):
            for part in b["linked_note"].split(","):
                part_clean = part.strip()
                if part_clean and part_clean not in raw_notes:
                    raw_notes.append(part_clean)

        for rn in raw_notes:
            norm_rn = rn.replace("\\", "/").lower()
            rn_filename = os.path.basename(norm_rn)
            if norm_rn not in existing_note_rel_paths and rn_filename not in existing_note_rel_paths:
                # Also check actual file path
                candidate_path = os.path.join(target_notes, rn.replace("/", os.sep).replace("\\", os.sep))
                if not os.path.exists(candidate_path):
                    reasons.append(f"Linked note deleted: '{rn}'")
                    orph_notes.append(rn)

        # Check course references
        c_id = b.get("course_id")
        if c_id and c_id not in valid_course_ids:
            reasons.append(f"Course ID deleted: '{c_id}'")
            orph_course = c_id

        # If explicit course field was provided
        c_field = b.get("course") or b.get("course_name")
        if c_field and isinstance(c_field, str):
            c_field_clean = c_field.strip().lower()
            if (
                c_field_clean not in valid_course_names
                and c_field_clean not in valid_course_subjects
                and c_field not in valid_course_ids
            ):
                reasons.append(f"Referenced course deleted: '{c_field}'")
                orph_course = c_field

        if reasons:
            return {
                "block_id": bid,
                "file_path": filepath,
                "topic": topic,
                "session_date": b.get("session_date", "Unknown"),
                "reasons": reasons,
                "orphaned_notes": orph_notes,
                "orphaned_course": orph_course,
                "block_data": b,
            }
        return None

    # Prefer current session records over legacy entries with the same ID.
    session_ids = set()
    if os.path.exists(target_sessions):
        for entry in os.scandir(target_sessions):
            if entry.is_file() and entry.name.endswith(".json"):
                try:
                    with open(entry.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        session_ids.add(data.get("id"))
                        res = check_block(data, entry.path)
                        if res:
                            orphaned_blocks.append(res)
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                session_ids.add(item.get("id"))
                                res = check_block(item, entry.path)
                                if res:
                                    orphaned_blocks.append(res)
                except Exception:
                    continue

    # 5. Check legacy study_blocks.json if exists
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                leg_data = json.load(f)
            if isinstance(leg_data, list):
                for item in leg_data:
                    if isinstance(item, dict) and item.get("id") not in session_ids:
                        res = check_block(item, legacy_file)
                        if res and not any(ob["block_id"] == res["block_id"] for ob in orphaned_blocks):
                            orphaned_blocks.append(res)
        except Exception:
            pass

    return orphaned_blocks


def cleanup_orphaned_study_blocks(
    orphaned_blocks: List[Dict[str, Any]],
    sessions_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
    legacy_file: Optional[str] = None,
) -> int:
    """
    Deletes the session JSON files and updates registry for the identified orphaned blocks.
    Returns the count of successfully cleaned/deleted blocks.
    """
    storage = get_storage_paths()
    override_root = data_dir or (os.path.dirname(sessions_dir) if sessions_dir else None)
    target_sessions = os.path.abspath(sessions_dir or (os.path.join(override_root, "sessions") if override_root else storage["sessions_dir"]))
    legacy_file = os.path.abspath(legacy_file or (os.path.join(override_root, "study_blocks.json") if override_root else storage["legacy_study_blocks_file"]))
    from scheduler import delete_study_block

    block_ids = {item["block_id"] for item in orphaned_blocks if item.get("block_id")}
    return sum(delete_study_block(block_id, directory=target_sessions, legacy_file=legacy_file) for block_id in block_ids)
