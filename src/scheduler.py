"""
Scheduling Engine for the Ultimate Study Application.

This module provides:
1. Pure-Python, type-hinted algorithms for calculating sequential study and break intervals.
2. Legacy database-driven daily schedule generation for database topics.
"""

from dataclasses import dataclass, asdict
import calendar
from datetime import datetime, time, timedelta
import json
import os
import re
import sqlite3
from typing import Any, Dict, List, Literal, Optional, Union

from settings_manager import get_storage_paths, write_json
from study_db import get_connection


__all__ = [
    "IntervalType",
    "StudyInterval",
    "StudyBlockSchedule",
    "parse_time_input",
    "generate_study_schedule",
    "split_interval_minutes",
    "get_topics",
    "calculate_priority",
    "generate_daily_schedule",
    "save_study_block_json",
    "load_study_blocks",
    "load_study_blocks_json",
    "delete_study_block",
    "clear_study_blocks",
    "get_study_block_by_id",
    "dict_to_study_block_schedule",
    "get_block_chronological_key",
    "sort_study_blocks_chronologically",
    "is_block_ended",
    "mark_study_block_historical",
    "mark_study_blocks_historical",
    "unmark_study_block_historical",
    "activate_historical_study_block",
    "get_study_blocks_by_date",
    "get_calendar_month_data",
    "SESSIONS_DIR",
]

# ==============================================================================
# PURE SCHEDULING ENGINE (Domain Models & Algorithms)
# ==============================================================================

IntervalType = Literal["focus", "break"]


@dataclass
class StudyInterval:
    """Represents a single timed study or break block."""
    index: int
    interval_type: IntervalType
    start_time: str  # Format: "HH:MM"
    end_time: str    # Format: "HH:MM"
    duration_minutes: int
    title: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the interval to a Python dictionary."""
        return asdict(self)


@dataclass
class StudyBlockSchedule:
    """Represents an entire study session composed of sequential intervals."""
    topic: str
    session_date: str  # Format: "YYYY-MM-DD"
    start_time: str    # Format: "HH:MM"
    end_time: str      # Format: "HH:MM"
    total_duration_minutes: int
    focus_duration_minutes: int
    break_duration_minutes: int
    intervals: List[StudyInterval]

    @property
    def total_focus_minutes(self) -> int:
        """Total minutes allocated to focus blocks."""
        return sum(i.duration_minutes for i in self.intervals if i.interval_type == "focus")

    @property
    def total_break_minutes(self) -> int:
        """Total minutes allocated to break blocks."""
        return sum(i.duration_minutes for i in self.intervals if i.interval_type == "break")

    @property
    def focus_intervals_count(self) -> int:
        """Total number of focus intervals."""
        return sum(1 for i in self.intervals if i.interval_type == "focus")

    @property
    def break_intervals_count(self) -> int:
        """Total number of break intervals."""
        return sum(1 for i in self.intervals if i.interval_type == "break")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the schedule and intervals to a nested dictionary."""
        data = asdict(self)
        data["total_focus_minutes"] = self.total_focus_minutes
        data["total_break_minutes"] = self.total_break_minutes
        data["focus_intervals_count"] = self.focus_intervals_count
        data["break_intervals_count"] = self.break_intervals_count
        return data

    def to_markdown_table(self) -> str:
        """Renders the schedule intervals as a Markdown table."""
        headers = "| # | Type | Time Window | Duration | Description |"
        divider = "|---|------|-------------|----------|-------------|"
        rows: List[str] = []
        for i in self.intervals:
            icon = "🎯 Focus" if i.interval_type == "focus" else "☕ Break"
            rows.append(
                f"| {i.index} | {icon} | {i.start_time} - {i.end_time} | {i.duration_minutes} min | {i.title} |"
            )
        return "\n".join([headers, divider] + rows)


def parse_time_input(time_input: Union[str, time, datetime]) -> time:
    """
    Parses a time specification into a standard datetime.time object.
    Supports strings ('HH:MM', 'HH:MM:SS'), datetime.time, and datetime.datetime.
    """
    if isinstance(time_input, time):
        return time(time_input.hour, time_input.minute)
    if isinstance(time_input, datetime):
        return time(time_input.hour, time_input.minute)
    if isinstance(time_input, str):
        cleaned = time_input.strip()
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
            try:
                parsed = datetime.strptime(cleaned, fmt).time()
                return time(parsed.hour, parsed.minute)
            except ValueError:
                continue
        raise ValueError(
            f"Invalid time format: '{time_input}'. Expected formats like '14:00', '14:00:00', or '2:00 PM'."
        )
    raise TypeError(f"Unsupported time_input type: {type(time_input).__name__}")


def split_interval_minutes(total_minutes: int, num_intervals: int) -> List[int]:
    """
    Divides total_minutes evenly across num_intervals.
    Any remainder minutes are distributed one-by-one to the leading intervals.
    """
    if num_intervals <= 0 or total_minutes <= 0:
        return []
    base = total_minutes // num_intervals
    rem = total_minutes % num_intervals
    return [base + (1 if i < rem else 0) for i in range(num_intervals)]


def generate_study_schedule(
    start_time: Union[str, time, datetime] = "14:00",
    total_duration_minutes: int = 120,
    focus_duration_minutes: int = 45,
    break_duration_minutes: int = 15,
    topic: str = "Machine Learning",
    session_date: Optional[str] = None,
    total_focus_minutes: Optional[int] = None,
    total_break_minutes: Optional[int] = None,
    num_focus_intervals: Optional[int] = None,
    num_break_intervals: Optional[int] = None,
) -> StudyBlockSchedule:
    """
    Pure-Python function to generate a study session schedule broken into
    sequential focus and break intervals.

    Supports two allocation modes:
    1. Interval Splitting: When num_focus_intervals is specified (> 0), splits
       total focus minutes across num_focus_intervals and break minutes across
       num_break_intervals, interleaving them sequentially.
    2. Fixed Block Allocation: When num_focus_intervals is None, alternates between
       fixed focus_duration_minutes and break_duration_minutes until total_duration_minutes is filled.
    """
    clean_start_time = parse_time_input(start_time)
    
    # Date determination
    if session_date is None:
        active_date_str = datetime.now().strftime("%Y-%m-%d")
    else:
        active_date_str = session_date.strip()

    anchor_dt = datetime(2026, 1, 1, clean_start_time.hour, clean_start_time.minute)
    cursor_dt = anchor_dt

    # -------------------------------------------------------------------------
    # Mode 1: Explicit Focus & Break Interval Splitting
    # -------------------------------------------------------------------------
    if num_focus_intervals is not None and num_focus_intervals > 0:
        if total_focus_minutes is not None:
            if total_focus_minutes <= 0:
                raise ValueError(f"total_focus_minutes must be positive, got {total_focus_minutes}")
            eff_focus_total = total_focus_minutes
        else:
            eff_focus_total = max(1, focus_duration_minutes * num_focus_intervals)

        focus_lengths = split_interval_minutes(eff_focus_total, num_focus_intervals)
        focus_lengths = [fl for fl in focus_lengths if fl > 0]
        if not focus_lengths:
            focus_lengths = [eff_focus_total]

        eff_num_breaks = num_break_intervals if num_break_intervals is not None else max(0, len(focus_lengths) - 1)
        if total_break_minutes is not None:
            if total_break_minutes < 0:
                raise ValueError(f"total_break_minutes cannot be negative, got {total_break_minutes}")
            eff_break_total = total_break_minutes
        else:
            eff_break_total = max(0, break_duration_minutes * eff_num_breaks)

        if eff_num_breaks > 0 and eff_break_total > 0:
            break_lengths = split_interval_minutes(eff_break_total, eff_num_breaks)
            break_lengths = [bl for bl in break_lengths if bl > 0]
        else:
            break_lengths = []

        F = len(focus_lengths)
        B = len(break_lengths)
        slots: List[List[int]] = [[] for _ in range(F)]
        if B > 0:
            target_transitions = F - 1 if B < F else F
            for b_idx in range(B):
                slot_pos = int((b_idx + 0.5) * target_transitions / B)
                slot_pos = min(slot_pos, F - 1)
                slots[slot_pos].append(break_lengths[b_idx])

        intervals: List[StudyInterval] = []
        index = 1
        focus_count = 0
        break_count = 0

        for f_idx, fl in enumerate(focus_lengths):
            focus_count += 1
            b_start = cursor_dt
            b_end = cursor_dt + timedelta(minutes=fl)
            intervals.append(
                StudyInterval(
                    index=index,
                    interval_type="focus",
                    start_time=b_start.strftime("%H:%M"),
                    end_time=b_end.strftime("%H:%M"),
                    duration_minutes=fl,
                    title=f"Focus Block {focus_count}: {topic}",
                    description=f"Deep focus on {topic} concepts and practice.",
                )
            )
            index += 1
            cursor_dt = b_end

            for bl in slots[f_idx]:
                break_count += 1
                br_start = cursor_dt
                br_end = cursor_dt + timedelta(minutes=bl)
                intervals.append(
                    StudyInterval(
                        index=index,
                        interval_type="break",
                        start_time=br_start.strftime("%H:%M"),
                        end_time=br_end.strftime("%H:%M"),
                        duration_minutes=bl,
                        title=f"Break {break_count}",
                        description="Rest, hydrate, step away from screens, and reset.",
                    )
                )
                index += 1
                cursor_dt = br_end

        total_dur = sum(i.duration_minutes for i in intervals)
        avg_focus = sum(focus_lengths) // len(focus_lengths) if focus_lengths else 0
        avg_break = sum(break_lengths) // len(break_lengths) if break_lengths else 0

        return StudyBlockSchedule(
            topic=topic,
            session_date=active_date_str,
            start_time=clean_start_time.strftime("%H:%M"),
            end_time=cursor_dt.strftime("%H:%M"),
            total_duration_minutes=total_dur,
            focus_duration_minutes=avg_focus,
            break_duration_minutes=avg_break,
            intervals=intervals,
        )

    # -------------------------------------------------------------------------
    # Mode 2: Standard Fixed Block Sequential Allocation
    # -------------------------------------------------------------------------
    if total_duration_minutes <= 0:
        raise ValueError(f"total_duration_minutes must be positive, got {total_duration_minutes}")
    if focus_duration_minutes <= 0:
        raise ValueError(f"focus_duration_minutes must be positive, got {focus_duration_minutes}")
    if break_duration_minutes < 0:
        raise ValueError(f"break_duration_minutes cannot be negative, got {break_duration_minutes}")

    remaining_minutes = total_duration_minutes
    intervals = []
    index = 1
    focus_count = 0
    break_count = 0

    while remaining_minutes > 0:
        current_focus_len = min(remaining_minutes, focus_duration_minutes)
        focus_count += 1
        block_start = cursor_dt
        block_end = cursor_dt + timedelta(minutes=current_focus_len)

        intervals.append(
            StudyInterval(
                index=index,
                interval_type="focus",
                start_time=block_start.strftime("%H:%M"),
                end_time=block_end.strftime("%H:%M"),
                duration_minutes=current_focus_len,
                title=f"Focus Block {focus_count}: {topic}",
                description=f"Deep focus on {topic} concepts and practice.",
            )
        )
        index += 1
        cursor_dt = block_end
        remaining_minutes -= current_focus_len

        if remaining_minutes > 0 and break_duration_minutes > 0:
            current_break_len = min(remaining_minutes, break_duration_minutes)
            break_count += 1
            break_start = cursor_dt
            break_end = cursor_dt + timedelta(minutes=current_break_len)

            intervals.append(
                StudyInterval(
                    index=index,
                    interval_type="break",
                    start_time=break_start.strftime("%H:%M"),
                    end_time=break_end.strftime("%H:%M"),
                    duration_minutes=current_break_len,
                    title=f"Break {break_count}",
                    description="Rest, hydrate, step away from screens, and reset.",
                )
            )
            index += 1
            cursor_dt = break_end
            remaining_minutes -= current_break_len

    final_start_str = clean_start_time.strftime("%H:%M")
    final_end_str = cursor_dt.strftime("%H:%M")

    return StudyBlockSchedule(
        topic=topic,
        session_date=active_date_str,
        start_time=final_start_str,
        end_time=final_end_str,
        total_duration_minutes=total_duration_minutes,
        focus_duration_minutes=focus_duration_minutes,
        break_duration_minutes=break_duration_minutes,
        intervals=intervals,
    )


# ==============================================================================
# DATABASE-DRIVEN TOPIC SCHEDULING (Legacy / Today's Schedule Support)
# ==============================================================================

def get_topics() -> List[Dict[str, Any]]:
    """Fetches all topics from the local SQLite database."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM topics")
    topics = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return topics


def calculate_priority(topic: Dict[str, Any]) -> float:
    """Calculates scheduling priority for a topic based on difficulty and exam date."""
    difficulty = topic.get("difficulty_weight", 1)
    exam_date_str = topic.get("target_exam_date")

    if not exam_date_str:
        return difficulty * 2.0

    try:
        exam_date = datetime.strptime(exam_date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        days_left = (exam_date - today).days

        if days_left < 0:
            return 0.0
        return difficulty * (30.0 / max(1, days_left))
    except ValueError:
        return difficulty * 2.0


def generate_daily_schedule(study_minutes: int = 120, block_size: int = 40) -> None:
    """Generates daily revision blocks in the SQLite database based on topic priority."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Check existing blocks for today
    cursor.execute(
        "SELECT topic_id, duration_minutes, completed FROM study_blocks WHERE scheduled_date = ?",
        (today_str,),
    )
    existing_blocks = cursor.fetchall()

    completed_minutes = sum(b["duration_minutes"] for b in existing_blocks if b["completed"])
    completed_topic_ids = {b["topic_id"] for b in existing_blocks if b["completed"]}

    # Delete uncompleted blocks to recalculate remaining budget
    cursor.execute(
        "DELETE FROM study_blocks WHERE scheduled_date = ? AND completed = 0",
        (today_str,),
    )

    remaining_budget = max(0, study_minutes - completed_minutes)
    if remaining_budget < block_size:
        conn.commit()
        conn.close()
        print(f"[✓] Daily budget already met ({completed_minutes}/{study_minutes} mins completed).")
        return

    # 2. Get candidate topics (excluding topics already completed today)
    topics = get_topics()
    for t in topics:
        t["priority"] = calculate_priority(t)

    # Filter out completed topics & passed exams
    active_topics = [t for t in topics if t["priority"] > 0 and t["id"] not in completed_topic_ids]
    active_topics.sort(key=lambda x: x["priority"], reverse=True)

    if not active_topics:
        active_topics = [t for t in topics if t["priority"] > 0]
        active_topics.sort(key=lambda x: x["priority"], reverse=True)

    allocated = 0
    for topic in active_topics:
        if allocated >= remaining_budget:
            break
        cursor.execute(
            "INSERT INTO study_blocks (topic_id, scheduled_date, duration_minutes, completed) VALUES (?, ?, ?, 0)",
            (topic["id"], today_str, block_size),
        )
        allocated += block_size

    conn.commit()
    conn.close()
    print(f"[✓] Scheduled {allocated} additional minutes for {today_str}.")


# ==============================================================================
# JSON STUDY BLOCK PERSISTENCE (data/sessions/)
# ==============================================================================

SESSIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sessions"
)


def save_study_block_json(
    schedule_data: Union[Dict[str, Any], StudyBlockSchedule],
    date_str: Optional[str] = None,
    directory: Optional[str] = None,
) -> str:
    """
    Writes the generated study block dictionary to a JSON file in data/sessions/
    (or a custom directory). Creates the directory if it does not exist.
    """
    target_dir = os.path.abspath(directory or get_storage_paths()["sessions_dir"])
    os.makedirs(target_dir, exist_ok=True)

    if isinstance(schedule_data, StudyBlockSchedule):
        block_dict = schedule_data.to_dict()
    elif isinstance(schedule_data, dict):
        block_dict = dict(schedule_data)
    else:
        raise TypeError(f"Unsupported schedule_data type: {type(schedule_data).__name__}")

    effective_date = (
        date_str
        or block_dict.get("session_date")
        or datetime.now().strftime("%Y-%m-%d")
    )
    block_dict["session_date"] = effective_date

    topic_str = block_dict.get("topic", "session").lower().strip()
    clean_topic = re.sub(r"[^a-zA-Z0-9_-]+", "_", topic_str).strip("_") or "session"
    start_time_slug = block_dict.get("start_time", "00:00").replace(":", "")

    block_id = block_dict.get("id") or f"block_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    block_dict["id"] = block_id
    if "created_at" not in block_dict:
        block_dict["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    filename = f"{effective_date}_{start_time_slug}_{clean_topic}_{block_id}.json"
    filepath = os.path.join(target_dir, filename)

    write_json(filepath, block_dict)
    remove_session_records(target_dir, block_id, keep_path=filepath)
    return filepath


def read_block_records(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        return [data]
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def remove_session_records(target_dir: str, block_id: str, keep_path: Optional[str] = None) -> bool:
    if not os.path.isdir(target_dir):
        return False
    deleted = False
    with os.scandir(target_dir) as entries:
        for entry in entries:
            if not entry.is_file() or not entry.name.endswith(".json") or entry.path == keep_path:
                continue
            records = read_block_records(entry.path)
            remaining = [record for record in records if record.get("id") != block_id]
            if len(remaining) == len(records):
                continue
            if remaining:
                write_json(entry.path, remaining)
            else:
                os.remove(entry.path)
            deleted = True
    return deleted


def delete_study_block(
    block_id: str, directory: Optional[str] = None, legacy_file: Optional[str] = None
) -> bool:
    """Delete a block from the session directory and its old registry, if present."""
    storage = get_storage_paths()
    target_dir = os.path.abspath(directory or storage["sessions_dir"])
    registry = legacy_file or (storage["legacy_study_blocks_file"] if directory is None else None)
    deleted = remove_session_records(target_dir, block_id)
    if registry:
        records = read_block_records(registry)
        remaining = [record for record in records if record.get("id") != block_id]
        if len(remaining) != len(records):
            write_json(registry, remaining)
            deleted = True
    return deleted


def clear_study_blocks(block_ids: List[str], directory: Optional[str] = None) -> int:
    """
    Deletes the study block JSON files corresponding to block_ids from data/sessions/ (or directory).
    Returns the count of successfully deleted blocks.
    """
    deleted_count = 0
    for bid in block_ids:
        if delete_study_block(bid, directory=directory):
            deleted_count += 1
    return deleted_count


def get_study_block_by_id(block_id: str, directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches a single study block dictionary by its ID from data/sessions/."""
    for b in load_study_blocks(directory=directory):
        if b.get("id") == block_id:
            return b
    return None


def load_study_blocks(directory: Optional[str] = None, seed_if_empty: bool = False) -> List[Dict[str, Any]]:
    """
    Reads all study block JSON files from data/sessions/ (or specified directory).
    Returns list of study block dictionaries, ordered newest first.
    If the directory is empty and seed_if_empty is True, initializes it with a default study block.
    """
    target_dir = os.path.abspath(directory or get_storage_paths()["sessions_dir"])
    os.makedirs(target_dir, exist_ok=True)

    blocks: List[Dict[str, Any]] = []
    with os.scandir(target_dir) as entries:
        for entry in entries:
            if entry.is_file() and entry.name.endswith(".json"):
                blocks.extend(read_block_records(entry.path))

    if directory is None:
        session_ids = {block.get("id") for block in blocks}
        legacy_file = get_storage_paths()["legacy_study_blocks_file"]
        blocks.extend(block for block in read_block_records(legacy_file) if block.get("id") not in session_ids)

    if not blocks and seed_if_empty:
        default_sched = generate_study_schedule(
            start_time="14:00",
            total_duration_minutes=120,
            focus_duration_minutes=45,
            break_duration_minutes=15,
            topic="Machine Learning",
            session_date=datetime.now().strftime("%Y-%m-%d"),
        )
        block_dict = default_sched.to_dict()
        block_dict["id"] = f"block_{datetime.now().strftime('%Y%m%d')}_default"
        block_dict["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block_dict["linked_note"] = "Machine Learning/machine_learning_foundations.md"
        save_study_block_json(block_dict, directory=target_dir)
        blocks.append(block_dict)

    # Sort blocks newest first by session_date, created_at, start_time
    blocks.sort(
        key=lambda b: (
            b.get("session_date", ""),
            b.get("created_at", ""),
            b.get("start_time", ""),
        ),
        reverse=True,
    )
    return blocks


# Aliases for compatibility
load_study_blocks_json = load_study_blocks


def dict_to_study_block_schedule(data: Dict[str, Any]) -> StudyBlockSchedule:
    """Converts a raw JSON dictionary into a strongly typed StudyBlockSchedule instance."""
    intervals = []
    for item in data.get("intervals", []):
        intervals.append(
            StudyInterval(
                index=int(item["index"]),
                interval_type=item["interval_type"],
                start_time=item["start_time"],
                end_time=item["end_time"],
                duration_minutes=int(item["duration_minutes"]),
                title=item["title"],
                description=item.get("description", ""),
            )
        )
    return StudyBlockSchedule(
        topic=data.get("topic", "General Study"),
        session_date=data.get("session_date", datetime.now().strftime("%Y-%m-%d")),
        start_time=data.get("start_time", "14:00"),
        end_time=data.get("end_time", "16:00"),
        total_duration_minutes=int(data.get("total_duration_minutes", 120)),
        focus_duration_minutes=int(data.get("focus_duration_minutes", 45)),
        break_duration_minutes=int(data.get("break_duration_minutes", 15)),
        intervals=intervals,
    )


def get_block_chronological_key(block: Dict[str, Any]) -> tuple:
    """Returns a sorting key (date_str, hour, minute) for chronological ordering of study blocks."""
    date_str = str(block.get("session_date", "") or "").strip()
    time_str = str(block.get("start_time", "00:00") or "00:00").strip()
    parts = time_str.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        hour, minute = 0, 0
    return (date_str, hour, minute)


def sort_study_blocks_chronologically(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sorts study blocks in ascending chronological order based on date and start time."""
    return sorted(blocks, key=get_block_chronological_key)


def is_block_ended(block: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """
    Checks if a study block's scheduled end datetime has passed.
    Returns True if ended, False otherwise.
    """
    if now is None:
        now = datetime.now()

    date_str = str(block.get("session_date", "") or "").strip()
    end_time_str = str(block.get("end_time", "") or "").strip()

    if not date_str:
        return False

    try:
        block_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False

    now_date = now.date()
    if block_date < now_date:
        return True
    if block_date > now_date:
        return False

    # Block date is today, compare time
    if not end_time_str:
        return False

    try:
        parts = end_time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        block_end_time = time(hour, minute)
        return now.time() >= block_end_time
    except Exception:
        return False


def mark_study_block_historical(block_id: str, directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Marks a study block as historical by setting 'is_historical': True and persisting to disk.
    Returns the updated block dictionary if found, or None.
    """
    block = get_study_block_by_id(block_id, directory=directory)
    if block:
        block["is_historical"] = True
        block["status"] = "historical"
        block["manually_reinstated"] = False
        save_study_block_json(block, date_str=block.get("session_date"), directory=directory)
        return block
    return None


def mark_study_blocks_historical(block_ids: List[str], directory: Optional[str] = None) -> int:
    """
    Marks multiple study blocks as historical and persists changes to disk.
    Returns the count of updated blocks.
    """
    count = 0
    for bid in block_ids:
        if mark_study_block_historical(bid, directory=directory):
            count += 1
    return count


def unmark_study_block_historical(block_id: str, directory: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Clears historical status on a study block by setting 'is_historical': False and 'status': 'active', persisting to disk.
    Returns the updated block dictionary if found, or None.
    """
    block = get_study_block_by_id(block_id, directory=directory)
    if block:
        block["is_historical"] = False
        block["status"] = "active"
        save_study_block_json(block, date_str=block.get("session_date"), directory=directory)
        return block
    return None


def activate_historical_study_block(
    block_id: str,
    target_date: Optional[str] = None,
    directory: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Reactivates a historical study block:
    1. Sets 'is_historical' to False and 'status' to 'active'.
    2. If the block's session_date differs from target_date (defaults to current date),
       updates session_date to target_date and records the date transition in 'date_history'.
    3. Flags 'manually_reinstated' to True.
    4. Persists the updated block dictionary to disk (removing any old session file).
    Returns the updated block dictionary, or None if not found.
    """
    block = get_study_block_by_id(block_id, directory=directory)
    if not block:
        return None

    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    old_date = str(block.get("session_date", "") or "").strip()

    if old_date and old_date != target_date:
        if "date_history" not in block or not isinstance(block["date_history"], list):
            block["date_history"] = []

        history_entry = {
            "previous_date": old_date,
            "new_date": target_date,
            "changed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": "Reinstated from Historical Registry to Today's Agenda",
        }
        block["date_history"].append(history_entry)
        block["session_date"] = target_date

    block["is_historical"] = False
    block["status"] = "active"
    block["manually_reinstated"] = True

    save_study_block_json(block, date_str=block.get("session_date"), directory=directory)
    return block


def get_study_blocks_by_date(
    blocks: Optional[List[Dict[str, Any]]] = None,
    directory: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Groups study blocks by their session_date ("YYYY-MM-DD").
    Blocks without a valid session_date are grouped under "undated".
    Each list of blocks is sorted chronologically using sort_study_blocks_chronologically.
    """
    if blocks is None:
        blocks = load_study_blocks_json(directory=directory)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for block in blocks:
        s_date = block.get("session_date")
        if not s_date or not isinstance(s_date, str) or not s_date.strip():
            date_key = "undated"
        else:
            date_key = s_date.strip()
        grouped.setdefault(date_key, []).append(block)

    for date_key in grouped:
        grouped[date_key] = sort_study_blocks_chronologically(grouped[date_key])

    return grouped


def get_calendar_month_data(year: int, month: int) -> Dict[str, Any]:
    """
    Returns calendar metadata and week matrix for the specified year and month.
    Each week is a list of 7 day integers (0 for days outside the month, 1..31 for days in the month).
    Monday is day 0, Sunday is day 6.
    """
    cal = calendar.Calendar(firstweekday=0)  # Monday start
    month_days = cal.monthdayscalendar(year, month)
    month_name = calendar.month_name[month]
    days_in_month = calendar.monthrange(year, month)[1]

    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    return {
        "year": year,
        "month": month,
        "month_name": month_name,
        "weeks": month_days,
        "days_in_month": days_in_month,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }


if __name__ == "__main__":
    import sys
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    test_plan = generate_study_schedule(
        start_time="14:00",
        total_duration_minutes=120,
        focus_duration_minutes=45,
        break_duration_minutes=15,
        topic="Machine Learning",
    )
    print("=== Generated Schedule ===")
    print(f"Topic: {test_plan.topic}")
    print(f"Window: {test_plan.start_time} -> {test_plan.end_time} ({test_plan.total_duration_minutes} min)")
    print(f"Focus: {test_plan.total_focus_minutes} min | Breaks: {test_plan.total_break_minutes} min\n")
    print(test_plan.to_markdown_table())