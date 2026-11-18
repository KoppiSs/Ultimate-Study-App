"""Populate a new default workspace with clearly labeled example records."""

from datetime import date, timedelta
from pathlib import Path
import shutil

from scheduler import generate_study_schedule, save_study_block_json
from settings_manager import BASE_DIR, DEFAULT_DATA_DIR, STORAGE_PATHS, get_storage_paths


def seed_examples(settings):
    storage = get_storage_paths(settings)
    if settings.get("data_dir") or storage["data_dir"] != DEFAULT_DATA_DIR:
        return
    if any(storage[key] != str(Path(DEFAULT_DATA_DIR) / relative) for key, (relative, _, _) in STORAGE_PATHS.items()):
        return

    root = Path(DEFAULT_DATA_DIR)
    marker = root / ".examples-initialized"
    notes = Path(storage["notes_vault_dir"])
    sessions = Path(storage["sessions_dir"])
    courses = Path(storage["courses_file"])
    legacy = Path(storage["legacy_study_blocks_file"])
    if marker.exists() or courses.exists() or legacy.exists() or notes.exists() or sessions.exists():
        return

    examples = Path(BASE_DIR) / "examples"
    root.mkdir(parents=True, exist_ok=True)
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return
    shutil.copytree(examples / "notes", notes)
    shutil.copyfile(examples / "courses.json", courses)
    session_date = (date.today() + timedelta(days=1)).isoformat()
    schedule = generate_study_schedule(
        start_time="14:00", total_duration_minutes=45, focus_duration_minutes=35,
        break_duration_minutes=10, topic="Retrieval Practice (Example)", session_date=session_date,
    ).to_dict()
    schedule.update({
        "id": "demo-retrieval-practice",
        "course_id": "DEMO-101",
        "linked_note": "Practice Methods/retrieval_practice.md",
        "linked_notes": ["Practice Methods/retrieval_practice.md"],
    })
    save_study_block_json(schedule, directory=str(sessions))
