"""
Unit tests for the Study Block Workflow components.
"""

from datetime import datetime
import os
import unittest
from scheduler import generate_study_schedule, parse_time_input
from wiki_server import configured_zim_path, get_wikipedia_summary
from notes_manager import (
    sanitize_filename,
    save_study_session_note,
    load_study_note,
    format_study_session_markdown,
    search_markdown_notes,
    extract_single_reference_summary,
)


class TestScheduler(unittest.TestCase):
    def test_2hour_schedule_intervals(self):
        """Verify 2-hour schedule starting at 14:00 with 45m focus and 15m breaks."""
        schedule = generate_study_schedule(
            start_time="14:00",
            total_duration_minutes=120,
            focus_duration_minutes=45,
            break_duration_minutes=15,
            topic="Machine Learning",
        )
        self.assertEqual(schedule.start_time, "14:00")
        self.assertEqual(schedule.end_time, "16:00")
        self.assertEqual(schedule.total_duration_minutes, 120)
        self.assertEqual(schedule.total_focus_minutes, 90)
        self.assertEqual(schedule.total_break_minutes, 30)
        self.assertEqual(len(schedule.intervals), 4)

        # Check interval sequence
        i1 = schedule.intervals[0]
        self.assertEqual(i1.interval_type, "focus")
        self.assertEqual(i1.start_time, "14:00")
        self.assertEqual(i1.end_time, "14:45")
        self.assertEqual(i1.duration_minutes, 45)

        i2 = schedule.intervals[1]
        self.assertEqual(i2.interval_type, "break")
        self.assertEqual(i2.start_time, "14:45")
        self.assertEqual(i2.end_time, "15:00")
        self.assertEqual(i2.duration_minutes, 15)

        i3 = schedule.intervals[2]
        self.assertEqual(i3.interval_type, "focus")
        self.assertEqual(i3.start_time, "15:00")
        self.assertEqual(i3.end_time, "15:45")
        self.assertEqual(i3.duration_minutes, 45)

        i4 = schedule.intervals[3]
        self.assertEqual(i4.interval_type, "break")
        self.assertEqual(i4.start_time, "15:45")
        self.assertEqual(i4.end_time, "16:00")
        self.assertEqual(i4.duration_minutes, 15)

    def test_bounds_and_validations(self):
        with self.assertRaises(ValueError):
            generate_study_schedule(total_duration_minutes=0)
        with self.assertRaises(ValueError):
            generate_study_schedule(focus_duration_minutes=0)
        with self.assertRaises(ValueError):
            generate_study_schedule(break_duration_minutes=-5)
        with self.assertRaises(ValueError):
            parse_time_input("invalid-time")


class TestWikipediaArchive(unittest.TestCase):
    def test_search_machine_learning(self):
        if not os.path.isfile(configured_zim_path()):
            self.skipTest("Set ZIM_PATH to an English Wikipedia archive to run this integration test")
        try:
            import libzim.reader
        except ImportError:
            self.skipTest("Install requirements-wikipedia.txt to run the ZIM integration test")
        res = get_wikipedia_summary("Machine Learning", max_paragraphs=1)
        self.assertTrue(res.get("found"))
        self.assertEqual(res.get("title"), "Machine learning")
        self.assertIn("statistical algorithms", res.get("summary", "").lower())
        # Verify single paragraph returned
        self.assertEqual(len(res.get("summary", "").split("\n\n")), 1)


class TestNotesManager(unittest.TestCase):
    def test_sanitize_filename(self):
        name = "Machine Learning: Advanced ../ Test!"
        clean = sanitize_filename(name)
        self.assertNotIn("..", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn(":", clean)

    def test_single_summary_extraction(self):
        # Test input with 3 paragraphs concatenated
        multi_para = "Para 1.\n\nPara 2.\n\nPara 3."
        wiki_payload = {
            "title": "Test Topic",
            "summary": multi_para,
            "paragraphs": ["Para 1.", "Para 2.", "Para 3."],
            "source": "Offline Wikipedia",
        }
        extracted = extract_single_reference_summary(wiki_payload)
        self.assertEqual(extracted["summary"], "Para 1.")

    def test_no_triplicated_summary_in_markdown(self):
        schedule = generate_study_schedule(
            start_time="14:00",
            total_duration_minutes=120,
            focus_duration_minutes=45,
            break_duration_minutes=15,
            topic="Machine Learning",
        )
        wiki = {
            "title": "Machine learning",
            "summary": "Unique reference summary text.",
            "source": "Offline Wikipedia ZIM Archive",
            "paragraphs": ["Unique reference summary text."],
        }
        md = format_study_session_markdown(schedule, wiki)
        # Verify the summary text appears exactly once in the entire document
        count = md.count("Unique reference summary text.")
        self.assertEqual(count, 1, f"Expected summary to appear once, found {count} times.")

    def test_save_and_load_file_note(self):
        schedule = generate_study_schedule(
            start_time="14:00",
            total_duration_minutes=120,
            focus_duration_minutes=45,
            break_duration_minutes=15,
            topic="Machine Learning",
        )
        wiki = {
            "title": "Machine learning",
            "summary": "Sample summary for test.",
            "source": "Offline Wikipedia ZIM Archive",
            "paragraphs": ["Sample summary for test."],
        }
        saved_path = save_study_session_note(
            schedule=schedule,
            wiki_summary=wiki,
            filename="unit_test_note.md",
        )
        self.assertTrue(os.path.exists(saved_path))
        loaded = load_study_note(saved_path)
        self.assertIn("Study Session: Machine Learning", loaded)
        self.assertIn("14:00 - 14:45", loaded)
        self.assertIn("Sample summary for test.", loaded)

        # Cleanup test file
        if os.path.exists(saved_path):
            os.remove(saved_path)

    def test_no_sqlite_imports_in_notes_manager_and_app(self):
        import notes_manager
        import app
        self.assertFalse(hasattr(notes_manager, "sqlite3"))
        self.assertFalse(hasattr(notes_manager, "study_db"))
        self.assertFalse(hasattr(app, "sqlite3"))
        self.assertFalse(hasattr(app, "study_db"))


class TestStudyBlockEngine(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_retrieve_multiple_linked_notes(self):
        from scheduler import (
            generate_study_schedule,
            save_study_block_json,
            get_study_block_by_id,
            load_study_blocks,
        )

        sched = generate_study_schedule(
            start_time="10:00",
            total_duration_minutes=90,
            focus_duration_minutes=40,
            break_duration_minutes=10,
            topic="Deep Learning",
        )
        data = sched.to_dict()
        block_id = "test_block_multi_notes_123"
        data["id"] = block_id
        data["linked_notes"] = ["math/linear_algebra.md", "ml/neural_nets.md"]
        data["linked_note"] = "math/linear_algebra.md, ml/neural_nets.md"

        filepath = save_study_block_json(data, date_str="2026-10-16", directory=self.temp_dir)
        self.assertTrue(os.path.exists(filepath))

        loaded = get_study_block_by_id(block_id, directory=self.temp_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], block_id)
        self.assertEqual(loaded["topic"], "Deep Learning")
        self.assertEqual(len(loaded["linked_notes"]), 2)
        self.assertIn("math/linear_algebra.md", loaded["linked_notes"])
        self.assertIn("ml/neural_nets.md", loaded["linked_notes"])

    def test_edit_existing_study_block(self):
        from scheduler import (
            generate_study_schedule,
            save_study_block_json,
            get_study_block_by_id,
            load_study_blocks,
        )

        # 1. Initial creation
        sched = generate_study_schedule(
            start_time="09:00",
            total_duration_minutes=60,
            focus_duration_minutes=25,
            break_duration_minutes=5,
            topic="Initial Topic",
        )
        data = sched.to_dict()
        block_id = "test_edit_block_456"
        data["id"] = block_id
        data["linked_notes"] = ["note_v1.md"]
        save_study_block_json(data, date_str="2026-10-16", directory=self.temp_dir)

        # 2. Edit / Update with new topic, duration, and linked notes
        updated_sched = generate_study_schedule(
            start_time="11:00",
            total_duration_minutes=120,
            focus_duration_minutes=50,
            break_duration_minutes=10,
            topic="Updated Advanced Topic",
        )
        updated_data = updated_sched.to_dict()
        updated_data["id"] = block_id  # Preserves block ID
        updated_data["linked_notes"] = ["note_v1.md", "note_v2.md"]
        updated_data["linked_note"] = "note_v1.md, note_v2.md"
        save_study_block_json(updated_data, date_str="2026-10-16", directory=self.temp_dir)

        # Verify old file was replaced and single entry exists
        all_blocks = load_study_blocks(directory=self.temp_dir)
        matching = [b for b in all_blocks if b.get("id") == block_id]
        self.assertEqual(len(matching), 1)

        loaded = get_study_block_by_id(block_id, directory=self.temp_dir)
        self.assertEqual(loaded["topic"], "Updated Advanced Topic")
        self.assertEqual(loaded["total_duration_minutes"], 120)
        self.assertEqual(len(loaded["linked_notes"]), 2)

    def test_delete_study_block(self):
        from scheduler import (
            generate_study_schedule,
            save_study_block_json,
            delete_study_block,
            get_study_block_by_id,
        )

        sched = generate_study_schedule(
            start_time="15:00",
            total_duration_minutes=45,
            focus_duration_minutes=45,
            break_duration_minutes=0,
            topic="To Be Deleted",
        )
        data = sched.to_dict()
        block_id = "test_del_block_789"
        data["id"] = block_id
        save_study_block_json(data, date_str="2026-10-16", directory=self.temp_dir)

        self.assertIsNotNone(get_study_block_by_id(block_id, directory=self.temp_dir))

        # Delete
        result = delete_study_block(block_id, directory=self.temp_dir)
        self.assertTrue(result)
        self.assertIsNone(get_study_block_by_id(block_id, directory=self.temp_dir))

        # Second delete returns False
        result_again = delete_study_block(block_id, directory=self.temp_dir)
        self.assertFalse(result_again)

    def test_split_interval_minutes(self):
        from scheduler import split_interval_minutes

        self.assertEqual(split_interval_minutes(90, 3), [30, 30, 30])
        self.assertEqual(split_interval_minutes(100, 3), [34, 33, 33])
        self.assertEqual(split_interval_minutes(30, 2), [15, 15])
        self.assertEqual(split_interval_minutes(25, 2), [13, 12])
        self.assertEqual(split_interval_minutes(0, 3), [])
        self.assertEqual(split_interval_minutes(50, 0), [])

    def test_generate_study_schedule_interval_splitting(self):
        from scheduler import generate_study_schedule

        # 90m focus split into 3 (30m each), 30m break split into 2 (15m each)
        sched = generate_study_schedule(
            start_time="14:00",
            topic="Quantum Computing",
            total_focus_minutes=90,
            total_break_minutes=30,
            num_focus_intervals=3,
            num_break_intervals=2,
        )
        self.assertEqual(sched.total_focus_minutes, 90)
        self.assertEqual(sched.total_break_minutes, 30)
        self.assertEqual(sched.total_duration_minutes, 120)
        self.assertEqual(sched.focus_intervals_count, 3)
        self.assertEqual(sched.break_intervals_count, 2)
        self.assertEqual(len(sched.intervals), 5)
        self.assertEqual(sched.start_time, "14:00")
        self.assertEqual(sched.end_time, "16:00")

        # Verify interval order: Focus 1, Break 1, Focus 2, Break 2, Focus 3
        types = [i.interval_type for i in sched.intervals]
        self.assertEqual(types, ["focus", "break", "focus", "break", "focus"])
        durations = [i.duration_minutes for i in sched.intervals]
        self.assertEqual(durations, [30, 15, 30, 15, 30])

    def test_generate_study_schedule_zero_breaks(self):
        from scheduler import generate_study_schedule

        sched = generate_study_schedule(
            start_time="09:00",
            topic="Intensive Sprint",
            total_focus_minutes=60,
            total_break_minutes=0,
            num_focus_intervals=2,
            num_break_intervals=0,
        )
        self.assertEqual(sched.total_focus_minutes, 60)
        self.assertEqual(sched.total_break_minutes, 0)
        self.assertEqual(sched.focus_intervals_count, 2)
        self.assertEqual(sched.break_intervals_count, 0)
        self.assertEqual(len(sched.intervals), 2)
        types = [i.interval_type for i in sched.intervals]
        self.assertEqual(types, ["focus", "focus"])
        self.assertEqual(sched.start_time, "09:00")
    def test_sort_study_blocks_chronologically(self):
        from scheduler import sort_study_blocks_chronologically, get_block_chronological_key

        b1 = {"id": "1", "session_date": "2026-10-16", "start_time": "14:00", "topic": "Afternoon"}
        b2 = {"id": "2", "session_date": "2026-10-16", "start_time": "09:30", "topic": "Morning"}
        b3 = {"id": "3", "session_date": "2026-10-15", "start_time": "18:00", "topic": "Yesterday"}
        b4 = {"id": "4", "session_date": "2026-10-16", "start_time": "11:15", "topic": "Midday"}

        sorted_blocks = sort_study_blocks_chronologically([b1, b2, b3, b4])
        self.assertEqual([b["id"] for b in sorted_blocks], ["3", "2", "4", "1"])

    def test_is_block_ended(self):
        from datetime import datetime
        from scheduler import is_block_ended

        # Reference time: 2026-10-16 12:30:00
        ref_time = datetime(2026, 10, 16, 12, 30, 0)

        # 1. Past date -> ended
        past_block = {"session_date": "2026-10-15", "end_time": "18:00"}
        self.assertTrue(is_block_ended(past_block, now=ref_time))

        # 2. Future date -> not ended
        future_block = {"session_date": "2026-10-17", "end_time": "09:00"}
        self.assertFalse(is_block_ended(future_block, now=ref_time))

        # 3. Today, earlier end time (e.g. 11:00) -> ended
        ended_today = {"session_date": "2026-10-16", "end_time": "11:00"}
        self.assertTrue(is_block_ended(ended_today, now=ref_time))

        # 4. Today, later end time (e.g. 14:00) -> not ended
        upcoming_today = {"session_date": "2026-10-16", "end_time": "14:00"}
        self.assertFalse(is_block_ended(upcoming_today, now=ref_time))

        # 5. Today, exactly at end time (12:30) -> ended
        exact_today = {"session_date": "2026-10-16", "end_time": "12:30"}
        self.assertTrue(is_block_ended(exact_today, now=ref_time))

    def test_clear_study_blocks(self):
        from scheduler import save_study_block_json, clear_study_blocks, load_study_blocks

        # Save 3 blocks
        b1 = {"id": "c1", "session_date": "2026-10-16", "topic": "T1"}
        b2 = {"id": "c2", "session_date": "2026-10-16", "topic": "T2"}
        b3 = {"id": "c3", "session_date": "2026-10-16", "topic": "T3"}
        save_study_block_json(b1, directory=self.temp_dir)
        save_study_block_json(b2, directory=self.temp_dir)
        save_study_block_json(b3, directory=self.temp_dir)

        # Clear c1 and c2
        cleared = clear_study_blocks(["c1", "c2"], directory=self.temp_dir)
        self.assertEqual(cleared, 2)

        remaining = load_study_blocks(directory=self.temp_dir)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], "c3")

    def test_mark_study_block_historical(self):
        from scheduler import (
            save_study_block_json,
            mark_study_block_historical,
            get_study_block_by_id,
        )

        b = {"id": "hist_test_1", "session_date": "2026-10-16", "topic": "Active Topic"}
        save_study_block_json(b, directory=self.temp_dir)

        # Before marking
        loaded = get_study_block_by_id("hist_test_1", directory=self.temp_dir)
        self.assertFalse(loaded.get("is_historical", False))

        # Mark historical
        res = mark_study_block_historical("hist_test_1", directory=self.temp_dir)
        self.assertIsNotNone(res)
        self.assertTrue(res.get("is_historical"))
        self.assertEqual(res.get("status"), "historical")

        # Verify disk persistence
        reloaded = get_study_block_by_id("hist_test_1", directory=self.temp_dir)
        self.assertTrue(reloaded.get("is_historical"))

    def test_unmark_study_block_historical(self):
        from scheduler import (
            save_study_block_json,
            mark_study_block_historical,
            unmark_study_block_historical,
            get_study_block_by_id,
        )

        b = {"id": "hist_test_2", "session_date": "2026-10-16", "topic": "Historical Topic", "is_historical": True, "status": "historical"}
        save_study_block_json(b, directory=self.temp_dir)

        # Unmark historical
        res = unmark_study_block_historical("hist_test_2", directory=self.temp_dir)
        self.assertIsNotNone(res)
        self.assertFalse(res.get("is_historical"))
        self.assertEqual(res.get("status"), "active")

        # Verify disk persistence
        reloaded = get_study_block_by_id("hist_test_2", directory=self.temp_dir)
        self.assertFalse(reloaded.get("is_historical"))
        self.assertEqual(reloaded.get("status"), "active")

    def test_activate_historical_study_block(self):
        from scheduler import (
            save_study_block_json,
            activate_historical_study_block,
            get_study_block_by_id,
        )

        # 1. Historical block on an old date
        b = {
            "id": "old_block_1",
            "session_date": "2026-10-10",
            "topic": "Neural Networks",
            "is_historical": True,
            "status": "historical",
        }
        save_study_block_json(b, directory=self.temp_dir)

        # Reactivate with target_date "2026-10-16"
        res = activate_historical_study_block("old_block_1", target_date="2026-10-16", directory=self.temp_dir)
        self.assertIsNotNone(res)
        self.assertEqual(res["session_date"], "2026-10-16")
        self.assertFalse(res["is_historical"])
        self.assertEqual(res["status"], "active")
        self.assertTrue(res["manually_reinstated"])
        self.assertEqual(len(res["date_history"]), 1)
        self.assertEqual(res["date_history"][0]["previous_date"], "2026-10-10")
        self.assertEqual(res["date_history"][0]["new_date"], "2026-10-16")

        # Verify disk persistence and old file cleanup
        reloaded = get_study_block_by_id("old_block_1", directory=self.temp_dir)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["session_date"], "2026-10-16")
        self.assertFalse(reloaded["is_historical"])
        self.assertEqual(len(reloaded["date_history"]), 1)

        # 2. Reactivate again on another date (e.g. "2026-10-20") to test accumulating history
        res2 = activate_historical_study_block("old_block_1", target_date="2026-10-20", directory=self.temp_dir)
        self.assertEqual(res2["session_date"], "2026-10-20")
        self.assertEqual(len(res2["date_history"]), 2)
        self.assertEqual(res2["date_history"][1]["previous_date"], "2026-10-16")
        self.assertEqual(res2["date_history"][1]["new_date"], "2026-10-20")

        # 3. Reactivate block that is already on today's date -> date should not change
        today_hist = {
            "id": "today_block_1",
            "session_date": "2026-10-20",
            "topic": "Transformers",
            "is_historical": True,
            "status": "historical",
        }
        save_study_block_json(today_hist, directory=self.temp_dir)
        res3 = activate_historical_study_block("today_block_1", target_date="2026-10-20", directory=self.temp_dir)
        self.assertEqual(res3["session_date"], "2026-10-20")
        self.assertFalse(res3["is_historical"])
        self.assertTrue(res3["manually_reinstated"])
        self.assertEqual(len(res3.get("date_history", [])), 0)

    def test_get_study_blocks_by_date(self):
        from scheduler import get_study_blocks_by_date, save_study_block_json

        b1 = {"id": "b1", "topic": "Past 1", "session_date": "2026-10-10", "start_time": "10:00"}
        b2 = {"id": "b2", "topic": "Past 2", "session_date": "2026-10-10", "start_time": "09:00"}
        b3 = {"id": "b3", "topic": "Today", "session_date": "2026-10-16", "start_time": "14:00"}
        b4 = {"id": "b4", "topic": "Future", "session_date": "2026-10-25", "start_time": "11:00"}
        b5 = {"id": "b5", "topic": "Undated", "start_time": "12:00"}

        for b in [b1, b2, b3, b4]:
            save_study_block_json(b, directory=self.temp_dir)

        grouped = get_study_blocks_by_date(directory=self.temp_dir)
        self.assertIn("2026-10-10", grouped)
        self.assertIn("2026-10-16", grouped)
        self.assertIn("2026-10-25", grouped)

        # Chronological sort check for 2026-10-10: 09:00 before 10:00
        self.assertEqual(len(grouped["2026-10-10"]), 2)
        self.assertEqual(grouped["2026-10-10"][0]["id"], "b2")
        self.assertEqual(grouped["2026-10-10"][1]["id"], "b1")

        # Test undated fallback with raw dictionary list
        raw_grouped = get_study_blocks_by_date(blocks=[b1, b5])
        self.assertIn("undated", raw_grouped)
        self.assertEqual(raw_grouped["undated"][0]["id"], "b5")

    def test_get_calendar_month_data(self):
        from scheduler import get_calendar_month_data

        data = get_calendar_month_data(2026, 10)
        self.assertEqual(data["year"], 2026)
        self.assertEqual(data["month"], 10)
        self.assertEqual(data["month_name"], "October")
        self.assertEqual(data["days_in_month"], 31)
        self.assertEqual(data["prev_year"], 2026)
        self.assertEqual(data["prev_month"], 9)
        self.assertEqual(data["next_year"], 2026)
        self.assertEqual(data["next_month"], 11)
        self.assertTrue(len(data["weeks"]) >= 4)
        for week in data["weeks"]:
            self.assertEqual(len(week), 7)

        # Boundary month: January
        jan = get_calendar_month_data(2026, 1)
        self.assertEqual(jan["prev_year"], 2025)
        self.assertEqual(jan["prev_month"], 12)

        # Boundary month: December
        dec = get_calendar_month_data(2026, 12)
        self.assertEqual(dec["next_year"], 2027)
        self.assertEqual(dec["next_month"], 1)

    def test_upcoming_and_calendar_categorization(self):
        from scheduler import is_block_ended, sort_study_blocks_chronologically

        today_str = "2026-10-16"
        blocks = [
            {"id": "b_past", "session_date": "2026-10-10", "topic": "Past Block"},
            {"id": "b_today_active", "session_date": "2026-10-16", "start_time": "23:00", "end_time": "23:59", "topic": "Today Active"},
            {"id": "b_upcoming_1", "session_date": "2026-10-20", "start_time": "14:00", "topic": "Upcoming 1"},
            {"id": "b_upcoming_2", "session_date": "2026-10-18", "start_time": "09:00", "topic": "Upcoming 2"},
        ]

        reinstated_ids = set()
        hist_ids = set()

        simulated_now = datetime(2026, 10, 16, 12, 0)
        today_blocks = [
            b for b in blocks
            if b.get("session_date") == today_str
            and (not is_block_ended(b, now=simulated_now) or b.get("id") in reinstated_ids)
            and not b.get("is_historical")
            and b.get("id") not in hist_ids
        ]
        upcoming_blocks = [
            b for b in blocks
            if b.get("session_date") and b.get("session_date") > today_str
            and not b.get("is_historical")
            and b.get("id") not in hist_ids
        ]
        upcoming_blocks = sort_study_blocks_chronologically(upcoming_blocks)

        historical_blocks = [
            b for b in blocks
            if (b.get("id") not in reinstated_ids)
            and (
                (b.get("session_date") and b.get("session_date") < today_str)
                or (b.get("session_date") == today_str and is_block_ended(b, now=simulated_now))
                or b.get("is_historical")
                or b.get("id") in hist_ids
            )
        ]

        self.assertEqual(len(today_blocks), 1)
        self.assertEqual(today_blocks[0]["id"], "b_today_active")

        self.assertEqual(len(upcoming_blocks), 2)
        # Chronological sort: 2026-10-18 before 2026-10-20
        self.assertEqual(upcoming_blocks[0]["id"], "b_upcoming_2")
        self.assertEqual(upcoming_blocks[1]["id"], "b_upcoming_1")

        self.assertEqual(len(historical_blocks), 1)
        self.assertEqual(historical_blocks[0]["id"], "b_past")


if __name__ == "__main__":
    unittest.main()


