"""
Test Suite for Settings Manager Module.
"""

from datetime import datetime, date, time
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from settings_manager import (
    load_settings,
    save_settings,
    get_notes_vault_dir,
    set_notes_vault_dir,
    get_time_format,
    set_time_format,
    get_date_format,
    set_date_format,
    format_time,
    format_date,
    format_date_short,
    format_datetime,
    create_vault_archive_zip,
    get_vault_stats,
    find_orphaned_study_blocks,
    cleanup_orphaned_study_blocks,
    DEFAULT_SETTINGS,
)


class TestSettingsManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.settings_file = os.path.join(self.test_dir, "settings.json")
        self.notes_dir = os.path.join(self.test_dir, "notes")
        self.sessions_dir = os.path.join(self.test_dir, "sessions")
        self.courses_file = os.path.join(self.test_dir, "courses.json")

        os.makedirs(self.notes_dir, exist_ok=True)
        os.makedirs(self.sessions_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_and_save_settings(self):
        # Default load when file doesn't exist
        s = load_settings(self.settings_file)
        self.assertEqual(s["time_format"], "24h")
        self.assertEqual(s["date_format"], "YYYY-MM-DD")

        # Save settings
        save_settings({"time_format": "12h", "date_format": "DD/MM/YYYY"}, filepath=self.settings_file)
        loaded = load_settings(self.settings_file)
        self.assertEqual(loaded["time_format"], "12h")
        self.assertEqual(loaded["date_format"], "DD/MM/YYYY")

    def test_vault_directory_mapping(self):
        new_vault = os.path.join(self.test_dir, "my_custom_obsidian_vault")
        save_settings({"notes_vault_dir": new_vault}, filepath=self.settings_file)
        s = load_settings(self.settings_file)
        self.assertEqual(s["notes_vault_dir"], os.path.abspath(new_vault))

    def test_format_time_24h_and_12h(self):
        # 24h format
        self.assertEqual(format_time("14:30", time_fmt="24h"), "14:30")
        self.assertEqual(format_time("09:15", time_fmt="24h"), "09:15")
        self.assertEqual(format_time(time(16, 45), time_fmt="24h"), "16:45")

        # 12h format
        self.assertEqual(format_time("14:30", time_fmt="12h"), "02:30 PM")
        self.assertEqual(format_time("09:15", time_fmt="12h"), "09:15 AM")
        self.assertEqual(format_time("00:05", time_fmt="12h"), "12:05 AM")
        self.assertEqual(format_time("12:00", time_fmt="12h"), "12:00 PM")
        self.assertEqual(format_time(time(16, 45), time_fmt="12h"), "04:45 PM")

        # Fallback handling
        self.assertEqual(format_time("", time_fmt="12h"), "")
        self.assertEqual(format_time(None, time_fmt="12h"), "")

    def test_format_date_formats(self):
        # YYYY-MM-DD
        self.assertEqual(format_date("2026-10-19", date_fmt="YYYY-MM-DD"), "2026-10-19")
        self.assertEqual(format_date(date(2026, 10, 19), date_fmt="YYYY-MM-DD"), "2026-10-19")

        # DD/MM/YYYY
        self.assertEqual(format_date("2026-10-19", date_fmt="DD/MM/YYYY"), "19/10/2026")
        self.assertEqual(format_date(date(2026, 10, 19), date_fmt="DD/MM/YYYY"), "19/10/2026")
        self.assertEqual(format_date("19/10/2026", date_fmt="DD/MM/YYYY"), "19/10/2026")

        # Short format for charts
        self.assertEqual(format_date_short("2026-10-19", date_fmt="DD/MM/YYYY"), "19/10")
        self.assertEqual(format_date_short("2026-10-19", date_fmt="YYYY-MM-DD"), "10/19")

    def test_vault_archive_zip_creation(self):
        # Create dummy subject folder and note
        subj_dir = os.path.join(self.notes_dir, "Physics")
        os.makedirs(subj_dir, exist_ok=True)
        with open(os.path.join(subj_dir, "thermodynamics.md"), "w", encoding="utf-8") as f:
            f.write("# Thermodynamics Note\nFirst law.")

        # Create dummy data JSON
        data_dir = os.path.join(self.test_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "test_courses.json"), "w", encoding="utf-8") as f:
            json.dump([{"course_id": "c1", "name": "Physics"}], f)

        # Create heavy file in data/zim that MUST be skipped
        zim_dir = os.path.join(data_dir, "zim")
        os.makedirs(zim_dir, exist_ok=True)
        with open(os.path.join(zim_dir, "test.zim"), "w", encoding="utf-8") as f:
            f.write("DUMMY ZIM DATA")

        zip_bytes, stats = create_vault_archive_zip(notes_dir=self.notes_dir, data_dir=data_dir)
        self.assertGreater(len(zip_bytes), 0)
        self.assertGreaterEqual(stats["notes_count"], 1)
        self.assertGreaterEqual(stats["json_count"], 1)

        # Inspect zip contents
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            # Must contain note and json
            self.assertTrue(any("thermodynamics.md" in n for n in namelist))
            self.assertTrue(any("test_courses.json" in n for n in namelist))
            # Must NOT contain ZIM
            self.assertFalse(any(".zim" in n for n in namelist))

    def test_orphaned_study_blocks_detection_and_cleanup(self):
        # 1. Setup existing courses
        with open(self.courses_file, "w", encoding="utf-8") as f:
            json.dump([
                {"course_id": "course_valid", "name": "Active Course", "linked_subject": "Math"}
            ], f)

        # 2. Setup existing notes
        subj_dir = os.path.join(self.notes_dir, "Math")
        os.makedirs(subj_dir, exist_ok=True)
        valid_note = os.path.join(subj_dir, "calculus.md")
        with open(valid_note, "w", encoding="utf-8") as f:
            f.write("# Calculus Note")

        # 3. Create a valid study block
        valid_block = {
            "id": "block_valid",
            "topic": "Math",
            "session_date": "2026-10-19",
            "course_id": "course_valid",
            "linked_notes": ["Math/calculus.md"],
        }
        with open(os.path.join(self.sessions_dir, "valid.json"), "w", encoding="utf-8") as f:
            json.dump(valid_block, f)

        # 4. Create an orphaned study block referencing a deleted note
        orphan_note_block = {
            "id": "block_orphan_note",
            "topic": "Math",
            "session_date": "2026-10-19",
            "course_id": "course_valid",
            "linked_notes": ["Math/deleted_geometry.md"],
        }
        with open(os.path.join(self.sessions_dir, "orphan_note.json"), "w", encoding="utf-8") as f:
            json.dump(orphan_note_block, f)

        # 5. Create an orphaned study block referencing a deleted course
        orphan_course_block = {
            "id": "block_orphan_course",
            "topic": "Old Chemistry",
            "session_date": "2026-10-19",
            "course_id": "course_deleted_123",
        }
        with open(os.path.join(self.sessions_dir, "orphan_course.json"), "w", encoding="utf-8") as f:
            json.dump(orphan_course_block, f)

        # 6. Run scan
        orphans = find_orphaned_study_blocks(
            sessions_dir=self.sessions_dir,
            notes_dir=self.notes_dir,
            courses_file=self.courses_file,
        )
        self.assertEqual(len(orphans), 2)
        orphan_ids = {o["block_id"] for o in orphans}
        self.assertIn("block_orphan_note", orphan_ids)
        self.assertIn("block_orphan_course", orphan_ids)
        self.assertNotIn("block_valid", orphan_ids)

        # 7. Cleanup
        deleted_count = cleanup_orphaned_study_blocks(orphans, sessions_dir=self.sessions_dir)
        self.assertEqual(deleted_count, 2)

        # Rescan: should now be 0 orphans
        orphans_after = find_orphaned_study_blocks(
            sessions_dir=self.sessions_dir,
            notes_dir=self.notes_dir,
            courses_file=self.courses_file,
        )
        self.assertEqual(len(orphans_after), 0)


if __name__ == "__main__":
    unittest.main()

