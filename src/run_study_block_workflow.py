"""
End-to-End Prototype Workflow Runner for Study Block Session.

Executes the 4-step prototype workflow:
1. Generate a 2-hour study schedule (45m focus / 15m breaks) starting at 14:00.
2. Search offline Wikipedia archive for 'Machine Learning' for reference summary.
3. Save session plan and Wikipedia summary as a local Markdown note under data/notes/.
4. Verifies the generated note and preview output.
"""

import os
import sys
from scheduler import generate_study_schedule, StudyBlockSchedule
from wiki_server import get_wikipedia_summary
from notes_manager import save_study_session_note, load_study_note, list_study_notes


def run_prototype_workflow(
    topic: str = "Machine Learning",
    start_time: str = "14:00",
    total_duration_minutes: int = 120,
    focus_duration_minutes: int = 45,
    break_duration_minutes: int = 15,
) -> dict:
    """Runs the prototype study block workflow and returns execution artifacts."""
    print("=" * 60)
    print("🚀 EXECUTING PROTOTYPE STUDY BLOCK WORKFLOW")
    print("=" * 60)

    # -------------------------------------------------------------------------
    # STEP 1: Generate pure-Python schedule intervals
    # -------------------------------------------------------------------------
    print(f"\n[Step 1] Generating schedule starting at {start_time} for topic '{topic}'...")
    schedule: StudyBlockSchedule = generate_study_schedule(
        start_time=start_time,
        total_duration_minutes=total_duration_minutes,
        focus_duration_minutes=focus_duration_minutes,
        break_duration_minutes=break_duration_minutes,
        topic=topic,
    )
    print(f" -> Time Window: {schedule.start_time} to {schedule.end_time} ({schedule.total_duration_minutes} min)")
    print(f" -> Allocation: {schedule.total_focus_minutes}m Focus ({schedule.focus_intervals_count} intervals) | {schedule.total_break_minutes}m Breaks ({schedule.break_intervals_count} intervals)")
    print("\nScheduled Intervals:")
    for interval in schedule.intervals:
        icon = "🎯 [FOCUS]" if interval.interval_type == "focus" else "☕ [BREAK]"
        print(f"    {interval.index}. {icon:10} {interval.start_time} - {interval.end_time} ({interval.duration_minutes}m): {interval.title}")

    # -------------------------------------------------------------------------
    # STEP 2: Search offline Wikipedia archive
    # -------------------------------------------------------------------------
    print(f"\n[Step 2] Searching offline Wikipedia archive for '{topic}'...")
    wiki_result = get_wikipedia_summary(topic, max_paragraphs=3)
    if wiki_result.get("found"):
        print(f" -> Match Found: '{wiki_result['title']}'")
        print(f" -> Source: {wiki_result['source']}")
        print(f" -> Summary Length: {len(wiki_result['summary'])} characters")
        print("\nReference Summary:")
        for idx, para in enumerate(wiki_result.get("paragraphs", []), 1):
            print(f"    [P{idx}] {para[:140]}...")
    else:
        print(f" [!] Warning: Could not find reference summary: {wiki_result.get('summary')}")

    # -------------------------------------------------------------------------
    # STEP 3: Save session plan and Wikipedia summary as local Markdown note
    # -------------------------------------------------------------------------
    print(f"\n[Step 3] Saving session note under study notes directory...")
    saved_path = save_study_session_note(
        schedule=schedule,
        wiki_summary=wiki_result,
    )
    print(f" -> Successfully saved Markdown note to:\n    {saved_path}")

    # -------------------------------------------------------------------------
    # STEP 4: Verify saved note contents
    # -------------------------------------------------------------------------
    print(f"\n[Step 4] Verifying saved Markdown note content...")
    note_content = load_study_note(saved_path)
    print(f" -> Verified note size: {len(note_content)} characters across {len(note_content.splitlines())} lines.")

    print("\n" + "=" * 60)
    print("✅ PROTOTYPE WORKFLOW COMPLETED SUCCESSFULLY")
    print("=" * 60)

    return {
        "schedule": schedule,
        "wiki_result": wiki_result,
        "saved_path": saved_path,
        "note_content": note_content,
    }


if __name__ == "__main__":
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    run_prototype_workflow()

