"""
Ultimate Study Application - Streamlit Frontend Interface.

This frontend is strictly file-based and stores/retrieves notes exclusively from local .md files.
All SQLite database dependencies and queries have been removed.
"""

from datetime import datetime, timedelta
from html import escape
import os
import re
import time
from typing import Any, Dict, List, Optional
import uuid
import altair as alt
import pandas as pd
import streamlit as st
from streamlit_option_menu import option_menu

from urllib.parse import urlsplit

from demo_data import seed_examples
from local_ai_search import ask_ollama, configured_ollama
from scheduler import (
    generate_study_schedule,
    split_interval_minutes,
    StudyBlockSchedule,
    StudyInterval,
    save_study_block_json,
    load_study_blocks,
    load_study_blocks_json,
    delete_study_block,
    clear_study_blocks,
    get_study_block_by_id,
    dict_to_study_block_schedule,
    get_block_chronological_key,
    sort_study_blocks_chronologically,
    is_block_ended,
    mark_study_block_historical,
    mark_study_blocks_historical,
    unmark_study_block_historical,
    activate_historical_study_block,
    get_study_blocks_by_date,
    get_calendar_month_data,
)
from wiki_server import configured_zim_path, get_wikipedia_summary, wikipedia_available
from notes_manager import (
    save_generic_markdown_note,
    save_course_note_file,
    delete_study_note,
    load_study_note,
    list_study_notes,
    search_markdown_notes,
    get_all_markdown_notes_hierarchy,
    get_subject_folder_tree,
    list_subject_folders,
    create_subject_folder,
    organize_notes_into_subject_folders,
    parse_note_metadata,
    format_note_with_frontmatter,
    move_note_to_subject_folder,
    rename_subject_folder,
    delete_subject_folder,
    sanitize_latex_markdown,
)
from ai_notes_author import (
    generate_ai_study_note,
    enhance_existing_note,
    get_template_names,
    get_template,
    get_snippet_names,
    get_snippet,
)
from course_manager import (
    load_courses,
    save_course,
    delete_course,
    compute_course_status,
)
from settings_manager import (
    load_settings,
    save_settings,
    get_notes_vault_dir,
    get_time_format,
    set_time_format,
    get_date_format,
    set_date_format,
    format_time,
    format_date,
    format_date_short,
    format_datetime,
    create_vault_archive_zip,
    find_orphaned_study_blocks,
    cleanup_orphaned_study_blocks,
    get_vault_stats,
    get_storage_paths,
    validate_storage_paths,
)

STOPWORDS = {
    "what", "is", "are", "the", "a", "an", "of", "in", "for", "to", "on", "and",
    "my", "notes", "according", "tell", "me", "about", "how", "why", "can", "you",
    "does", "explain", "describe", "main", "some", "give", "list",
}

# ==============================================================================
# STREAMLIT CACHING (Performance Construction)
# ==============================================================================

@st.cache_data(show_spinner=False)
def cached_wikipedia_lookup(query: str, max_paragraphs: int, zim_path: str, archive_mtime: int) -> Dict[str, Any]:
    return get_wikipedia_summary(query, max_paragraphs=max_paragraphs, zim_path=zim_path)


def cached_get_wikipedia_summary(query: str, max_paragraphs: int = 1) -> Dict[str, Any]:
    if not wiki_available:
        return {}
    try:
        result = cached_wikipedia_lookup(query, max_paragraphs, zim_path, os.stat(zim_path).st_mtime_ns)
    except OSError:
        return {}
    return result if result.get("found") else {}


@st.dialog("Metric details")
def show_kpi_modal(kpi: Dict[str, Any]) -> None:
    st.metric(kpi["lbl"], kpi["val"])
    st.caption(kpi["sub"])
    data = kpi.get("data")
    if not data:
        return

    color = kpi["color"]
    today = datetime.now()
    active_date_fmt = st.session_state.get("app_settings", {}).get("date_format", "YYYY-MM-DD")
    day_labels = [format_date(today - timedelta(days=len(data) - 1 - i), active_date_fmt) for i in range(len(data))]
    df = pd.DataFrame({"Date": day_labels, "Value": data})

    larger_chart = (
        alt.Chart(df)
        .mark_line(color=color, strokeWidth=3, point=alt.OverlayMarkDef(color=color, size=60))
        .encode(
            x=alt.X("Date:N", sort=None, axis=alt.Axis(labelAngle=-25, title=f"Timeline ({active_date_fmt})")),
            y=alt.Y("Value:Q", axis=alt.Axis(title="Metric Value")),
            tooltip=[
                alt.Tooltip("Date:N", title="Date"),
                alt.Tooltip("Value:Q", title="Value"),
            ],
        )
        .properties(height=200)
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(larger_chart, use_container_width=True, theme="streamlit")
    st.markdown("**Daily breakdown**")
    st.dataframe(df, use_container_width=True, hide_index=True)


def extract_search_keywords(query: str) -> str:
    words = re.findall(r"\b\w+\b", query.lower())
    clean_words = [w for w in words if w not in STOPWORDS and len(w) > 2]
    return " ".join(clean_words) if clean_words else query


def evaluate_notes_sufficiency(query: str, personal_notes_text: str) -> bool:
    if not personal_notes_text or not personal_notes_text.strip():
        return False
    eval_prompt = f"""You are an academic evaluator. Determine if the student's personal notes contain enough theoretical explanation, formulas, or derivations to answer the question.
If the notes contain the relevant concepts or derivation, respond YES.
If the notes lack the necessary information, respond NO.

Question: {query}

Notes Context:
{personal_notes_text}

Response (YES or NO):"""
    decision = ask_ollama(None, eval_prompt).strip().upper()
    first_token = decision.split()[0] if decision.split() else ""
    return "YES" in first_token or "YES" in decision[:25]



# ==============================================================================
# PAGE SETUP & MODERN NAVIGATION
# ==============================================================================

st.set_page_config(page_title="Ultimate Study Application", page_icon="🎓", layout="wide")

# Directory & Assets Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
STYLES_FILE = os.path.join(ASSETS_DIR, "styles.css")


def load_css(filepath: str) -> None:
    """Injects custom CSS from assets/styles.css."""
    target_path = filepath
    if not os.path.exists(target_path):
        target_path = os.path.join("assets", "styles.css")
    if os.path.exists(target_path):
        with open(target_path, "r", encoding="utf-8") as f:
            css_content = f.read()
            st.markdown(f"<style>\n{css_content}\n</style>", unsafe_allow_html=True)


# Inject modern dark-mode compatible styling from assets/styles.css
load_css(STYLES_FILE)

# ==============================================================================
# STATE INITIALIZATION & SIDEBAR CORE NAVIGATION
# ==============================================================================
if "active_tab" not in st.session_state:
    st.session_state["active_tab"] = "Dashboard"

# Normalize legacy or alternative active_tab names
legacy_tab_map = {
    "Main Dashboard": "Dashboard",
    "Study Block Session": "Study Cockpit",
    "Today's Schedule": "Study Cockpit",
    "Course Notes": "Notes Vault",
    "Course Notes Vault": "Notes Vault",
    "Study Buddy": "AI Tutor",
    "Study Buddy AI": "AI Tutor",
}
app_settings = load_settings()
previous_storage = st.session_state.get("study_storage_paths")
storage_paths = get_storage_paths(app_settings)
if previous_storage is not None and previous_storage != storage_paths:
    current_tab = st.session_state.get("active_tab", "Dashboard")
    st.session_state.clear()
    st.session_state["active_tab"] = current_tab
    st.cache_data.clear()
st.session_state["study_storage_paths"] = storage_paths
st.session_state["app_settings"] = app_settings
seed_examples(app_settings)
ollama_url, ollama_model = configured_ollama(app_settings)
zim_path = configured_zim_path(app_settings)
wiki_available = wikipedia_available(app_settings)
tutor_notes_enabled = app_settings.get("tutor_notes_enabled", True)
time_fmt = app_settings.get("time_format", "24h")
date_fmt = app_settings.get("date_format", "YYYY-MM-DD")

if st.session_state.get("active_tab") in legacy_tab_map:
    st.session_state["active_tab"] = legacy_tab_map[st.session_state["active_tab"]]
if st.session_state.get("active_tab") not in ["Dashboard", "Study Cockpit", "Notes Vault", "Courses", "AI Tutor", "Settings"]:
    st.session_state["active_tab"] = "Dashboard"

today_dt = datetime.now()
today_str = today_dt.strftime("%Y-%m-%d")
formatted_today = f"{today_dt.strftime('%A')}, {format_date(today_dt, date_fmt)}"


# --- Sidebar Header ---
st.sidebar.markdown(
    """
<div class="sidebar-brand-header">
    <span class="brand-icon-wrapper material-symbols-rounded" aria-hidden="true">school</span>
    <span class="brand-title">Ultimate Study App</span>
</div>
""",
    unsafe_allow_html=True,
)

# --- Core Navigation (streamlit_option_menu) ---
nav_options = ["Dashboard", "Study Cockpit", "Notes Vault", "Courses", "AI Tutor", "Settings"]
nav_icons = ["kanban", "bullseye", "journal-bookmark", "mortarboard", "robot", "gear"]

# Detect programmatic tab changes from in-app buttons vs user menu clicks
if "_last_rendered_tab" not in st.session_state:
    st.session_state["_last_rendered_tab"] = st.session_state.get("active_tab", "Dashboard")

curr_tab = st.session_state.get("active_tab", "Dashboard")
curr_idx = nav_options.index(curr_tab) if curr_tab in nav_options else 0

# Only pass manual_select when an internal action changed active_tab programmatically
nav_manual_select = None
if curr_tab != st.session_state.get("_last_rendered_tab"):
    nav_manual_select = curr_idx

with st.sidebar:
    selected_tab = option_menu(
        menu_title=None,
        options=nav_options,
        icons=nav_icons,
        default_index=curr_idx,
        manual_select=nav_manual_select,
        key="main_app_sidebar_nav",
        styles={
            "container": {
                "padding": "0 !important",
                "margin": "0 !important",
                "background-color": "#1e1e24 !important",
                "border": "none !important",
                "border-radius": "0 !important",
                "box-shadow": "none !important",
                "cursor": "default !important",
            },
            "icon": {
                "color": "#818cf8",
                "font-size": "16px",
                "width": "16px",
            },
            "nav-link": {
                "font-size": "14px",
                "line-height": "20px",
                "text-align": "left",
                "margin": "0 0 4px !important",
                "padding": "8px 12px !important",
                "border-radius": "8px !important",
                "color": "var(--text-color, inherit) !important",
                "border": "1px solid rgba(128, 128, 128, 0.16) !important",
                "background-color": "#262730 !important",
                "transition": "background-color 150ms ease-out, border-color 150ms ease-out, color 150ms ease-out !important",
                "--hover-color": "rgba(99, 102, 241, 0.12)",
            },
            "nav-link-selected": {
                "background": "linear-gradient(90deg, rgba(99, 102, 241, 0.25) 0%, rgba(139, 92, 246, 0.15) 100%) !important",
                "border": "1px solid rgba(99, 102, 241, 0.55) !important",
                "border-left": "4px solid #6366f1 !important",
                "padding-left": "9px !important",
                "color": "#818cf8 !important",
                "font-weight": "600 !important",
                "box-shadow": "0 2px 10px rgba(99, 102, 241, 0.15) !important",
            },
        },
    )

if selected_tab:
    st.session_state["active_tab"] = selected_tab
    st.session_state["_last_rendered_tab"] = selected_tab

# --- Contextual Session Status (Progressive Disclosure) ---
if "study_block_schedule" in st.session_state:
    active_sched_sb: StudyBlockSchedule = st.session_state["study_block_schedule"]
    completed_intervals_sb = st.session_state.get("session_completed_intervals", set())
    active_ids_sb = st.session_state.get("active_study_block_ids", set())
    if len(active_ids_sb) > 1:
        card_header_sb = f"Active timeline ({len(active_ids_sb)} blocks)"
        card_title_sb = f"{active_sched_sb.topic} + {len(active_ids_sb) - 1} more"
    else:
        card_header_sb = "Active study block"
        card_title_sb = active_sched_sb.topic

    st.sidebar.markdown(
        f"""
<div class="sidebar-context-card">
    <div style="font-size:12px; font-weight:600; color:#3b82f6; margin-bottom:4px;">
        {card_header_sb}
    </div>
    <div style="font-weight:600; font-size:0.86rem; color:var(--text-color, inherit);">
        {card_title_sb}
    </div>
    <div style="color:rgba(156,163,175,0.9); font-size:0.75rem; margin-top:2px;">
        {format_time(active_sched_sb.start_time, time_fmt)} - {format_time(active_sched_sb.end_time, time_fmt)} &bull; {len(completed_intervals_sb)} milestones done
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

# --- Sidebar Footer / System Status ---
st.sidebar.markdown(
    """
<div class="sidebar-footer">
    <div class="footer-status-pill">
        <span style="display:inline-block; width:6px; height:6px; background:#10b981; border-radius:50%;"></span>
        <span>Offline mode</span>
    </div>
    <div class="footer-detail">
        Notes are stored locally as Markdown files
    </div>
</div>
""",
    unsafe_allow_html=True,
)

active_tab = st.session_state["active_tab"]

# ==============================================================================
# VIEW 0: MAIN DASHBOARD (Read-Only Summary & Vault Health)
# ==============================================================================
if active_tab in ["Dashboard", "Main Dashboard"]:
    vault_tree_dash = get_subject_folder_tree(storage_paths["notes_vault_dir"])
    total_subj_count = len(vault_tree_dash)
    all_notes_dash = [n for notes in vault_tree_dash.values() for n in notes]
    total_notes_count = len(all_notes_dash)
    total_math_count = sum(1 for n in all_notes_dash if n.get("has_math"))

    active_sched_dash = st.session_state.get("study_block_schedule")
    completed_intervals = st.session_state.get("session_completed_intervals", set())
    active_block_data = st.session_state.get("study_block_data") or {}
    active_b_id = active_block_data.get("id", "")

    # 1. Course Management Data: Load and filter active courses
    all_courses = load_courses()
    active_courses = [c for c in all_courses if c.get("status") == "Active"]

    # 7-day trend for active courses count
    courses_sparkline_data = [
        sum(1 for c in all_courses if compute_course_status(c, today_dt - timedelta(days=6 - i)) == "Active")
        for i in range(7)
    ]

    # 2. Calculate real completed study minutes for each of the last 7 days from load_study_blocks()
    all_saved_blocks = load_study_blocks()
    hist_ids = st.session_state.get("historical_block_ids", set())
    reinstated_ids = st.session_state.get("manually_reinstated_block_ids", set())

    # Active session completed minutes and completed intervals count
    active_completed_mins = 0
    active_total_mins = active_sched_dash.total_duration_minutes if active_sched_dash else 0
    active_done_count = 0
    if active_sched_dash and hasattr(active_sched_dash, "intervals"):
        for inter in active_sched_dash.intervals:
            idx = getattr(inter, "index", 0)
            dur = int(getattr(inter, "duration_minutes", 0))
            uid = f"{active_b_id}_{idx}" if active_b_id else ""
            if uid in completed_intervals or idx in completed_intervals or str(idx) in completed_intervals:
                active_completed_mins += dur
                active_done_count += 1

    # 7-day velocity calculation
    velocity_days = []
    velocity_mins = []

    for i in range(7):
        day_dt = today_dt - timedelta(days=6 - i)
        day_str = day_dt.strftime("%Y-%m-%d")
        velocity_days.append(format_date_short(day_dt, date_fmt))

        if day_str != today_str:
            # Past dates: sum total_duration_minutes for blocks completed on that date
            past_mins = sum(
                int(b.get("total_duration_minutes", 0))
                for b in all_saved_blocks
                if b.get("session_date") == day_str
            )
            velocity_mins.append(past_mins)
        else:
            # Today: calculate completed minutes from completed intervals of active/ongoing blocks,
            # plus total duration of ended/completed blocks for today
            today_mins = 0
            counted_block_ids = set()

            for b in all_saved_blocks:
                if b.get("session_date") != today_str:
                    continue
                b_id = b.get("id", "")
                is_ended = (
                    is_block_ended(b)
                    or b.get("is_historical")
                    or (b_id and b_id in hist_ids)
                ) and (b_id not in reinstated_ids)

                if is_ended and b_id != active_b_id:
                    today_mins += int(b.get("total_duration_minutes", 0))
                    if b_id:
                        counted_block_ids.add(b_id)
                else:
                    # Ongoing / active block: calculate minutes from completed intervals
                    for inter in b.get("intervals", []):
                        if isinstance(inter, dict):
                            idx = inter.get("index", 0)
                            dur = int(inter.get("duration_minutes", 0))
                        else:
                            idx = getattr(inter, "index", 0)
                            dur = int(getattr(inter, "duration_minutes", 0))
                        uid = f"{b_id}_{idx}" if b_id else ""
                        if uid in completed_intervals or idx in completed_intervals or str(idx) in completed_intervals:
                            today_mins += dur
                    if b_id:
                        counted_block_ids.add(b_id)

            # If active_sched_dash is running but not yet saved in all_saved_blocks
            if active_sched_dash and (not active_b_id or active_b_id not in counted_block_ids):
                for inter in getattr(active_sched_dash, "intervals", []):
                    idx = getattr(inter, "index", 0)
                    dur = int(getattr(inter, "duration_minutes", 0))
                    uid = f"{active_b_id}_{idx}" if active_b_id else ""
                    if uid in completed_intervals or idx in completed_intervals or str(idx) in completed_intervals:
                        today_mins += dur

            velocity_mins.append(today_mins)

    today_completed_mins = velocity_mins[-1]

    # Dashboard header
    st.markdown(
        f"""
<div class="minimal-hero-card dashboard-heading">
    <div class="page-heading-row">
        <div>
            <h2 class="minimal-hero-title"><span class="material-symbols-rounded" aria-hidden="true">dashboard</span> Study dashboard</h2>
            <p class="minimal-hero-sub">See your current courses, study activity, and recent notes</p>
        </div>
        <span class="badge-exam"><span class="material-symbols-rounded" aria-hidden="true">calendar_today</span> {formatted_today}</span>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # Dashboard metrics
    if active_sched_dash:
        active_sched_title = active_sched_dash.topic
        active_sched_sub = f"{active_completed_mins} / {active_total_mins} min • {active_done_count}/{len(active_sched_dash.intervals)} intervals"
    else:
        active_sched_title = "No active block"
        if today_completed_mins > 0:
            active_sched_sub = f"{today_completed_mins} min completed today"
        else:
            active_sched_sub = "No time logged today"

    registered_course_label = "course" if len(all_courses) == 1 else "courses"

    kpi_configs = [
        {
            "id": "notes",
            "val": str(total_notes_count),
            "lbl": "Course notes",
            "sub": f"Across {total_subj_count} subject {'folder' if total_subj_count == 1 else 'folders'}",
        },
        {
            "id": "subjs",
            "val": str(total_subj_count),
            "lbl": "Subject folders",
            "sub": "In the notes vault",
        },
        {
            "id": "math",
            "val": str(total_math_count),
            "lbl": "Notes with LaTeX",
            "sub": f"Out of {total_notes_count} notes",
        },
        {
            "id": "block",
            "val": active_sched_title,
            "lbl": "Study block",
            "sub": active_sched_sub,
            "data": velocity_mins,
            "color": "#3b82f6",
            "is_text_val": True,
        },
        {
            "id": "current_courses",
            "val": str(len(active_courses)),
            "lbl": "Current courses",
            "sub": f"{len(all_courses)} registered {registered_course_label}",
            "data": courses_sparkline_data,
            "color": "#10b981",
        },
    ]

    with st.container(key="dashboard_metrics"):
        for col, kpi in zip(st.columns(5), kpi_configs):
            with col, st.container(border=True, key=f"dashboard_metric_{kpi['id']}"):
                value_class = " dash-metric-val-text" if kpi.get("is_text_val") else ""
                st.html(
                    f"""
<div class="dash-metric-lbl">{kpi["lbl"]}</div>
<div class="dash-metric-val{value_class}">{escape(kpi["val"])}</div>
<div class="dash-metric-sub">{kpi["sub"]}</div>
"""
                )
                details_label = f"View {kpi['lbl']} details"
                if st.button(
                    details_label,
                    icon=":material/open_in_new:",
                    key=f"kpi_expand_{kpi['id']}",
                    help=details_label,
                    type="tertiary",
                ):
                    show_kpi_modal(kpi)

    # Dashboard summary
    with st.container(key="dashboard_summary"):
        col_dash_left, col_dash_right = st.columns([1.15, 1])

    with col_dash_left:
        # Card A: Current Courses
        with st.container(border=True):
            st.markdown(
                f"""
<div class="dash-card-header">
    <h3 class="dash-card-title"><span class="material-symbols-rounded" aria-hidden="true">school</span> Current courses</h3>
    <span class="badge-diff-1">{len(active_courses)} active</span>
</div>
""",
                unsafe_allow_html=True,
            )
            if not active_courses:
                st.markdown(
                    """
<div style="color: rgba(156, 163, 175, 0.9); font-size: 0.88rem; padding: 6px 0;">
    No active courses. Add or activate a course in Courses
</div>
""",
                    unsafe_allow_html=True,
                )
            else:
                with st.container(key="dashboard_course_list", gap="xsmall"):
                    for course in active_courses:
                        c_name = course.get("name", "Untitled Course")
                        code, _, title = c_name.partition(" ")
                        if title.strip() and re.fullmatch(r"[A-Z][A-Z0-9]*-[A-Z0-9]*\d[A-Z0-9]*", code):
                            course_title_html = f'<span class="course-code">{escape(code)}</span><span>{escape(title.strip())}</span>'
                        else:
                            course_title_html = escape(c_name)
                        c_linked = course.get("linked_subject", "")
                        c_schedule = course.get("schedule", [])
                        c_exam = course.get("exam_date", "")
                        c_start = course.get("start_date", "")
                        c_end = course.get("end_date", "")

                        # Linked Subject Badge
                        linked_badge = ""
                        if c_linked and c_linked != "None":
                            linked_badge = f'<span class="badge-tag">{escape(c_linked)}</span>'

                        # Weekly schedule summary
                        sched_parts = []
                        if isinstance(c_schedule, list):
                            for blk in c_schedule:
                                if isinstance(blk, dict):
                                    day_raw = str(blk.get("day", "")).strip()
                                    st_t = blk.get("start_time", "")
                                    en_t = blk.get("end_time", "")
                                    loc = blk.get("location", "")
                                    day_short = day_raw[:3] if day_raw else ""
                                    t_range = f"{format_time(st_t, time_fmt)}–{format_time(en_t, time_fmt)}" if (st_t and en_t) else (format_time(st_t, time_fmt) or format_time(en_t, time_fmt) or "")
                                    part = f"{day_short} {t_range}".strip()
                                    if loc:
                                        part += f" ({loc})"
                                    if part:
                                        sched_parts.append(part)
                                elif isinstance(blk, str) and blk.strip():
                                    sched_parts.append(blk.strip())

                        sched_summary = ", ".join(sched_parts) if sched_parts else ""

                        # Exam date or term dates badge
                        date_badge_html = ""
                        if c_exam:
                            date_badge_html = f'<span class="badge-exam">Exam: {escape(format_date(c_exam, date_fmt))}</span>'
                        elif c_start or c_end:
                            start_str = format_date(c_start, date_fmt) if c_start else "Start"
                            end_str = format_date(c_end, date_fmt) if c_end else "End"
                            date_badge_html = f'<span class="badge-diff-2">{escape(start_str)} → {escape(end_str)}</span>'

                        meta_sub_parts = []
                        if sched_summary:
                            meta_sub_parts.append(
                                f'<span><span class="material-symbols-rounded" aria-hidden="true">schedule</span> '
                                f'{escape(sched_summary)}</span>'
                            )
                        if date_badge_html:
                            meta_sub_parts.append(date_badge_html)

                        meta_sub_html = "".join(meta_sub_parts) or "<span>No weekly schedule set</span>"

                        st.html(
                            f"""
<article class="dash-quick-row course-row">
    <div class="course-title">{course_title_html}</div>
    <div class="course-meta">
        {meta_sub_html}
        {linked_badge}
    </div>
</article>
"""
                        )

        # Card B: Active Study Session Status
        with st.container(border=True):
            st.markdown(
                """
<div class="dash-card-header">
    <h3 class="dash-card-title"><span class="material-symbols-rounded" aria-hidden="true">schedule</span> Study activity</h3>
    <span class="badge-exam">Last 7 days</span>
</div>
""",
                unsafe_allow_html=True,
            )
            # Study minutes over the last seven days
            df_velocity = pd.DataFrame({"day": velocity_days, "minutes": velocity_mins})

            velocity_chart = (
                alt.Chart(df_velocity)
                .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2, color="#818cf8")
                .encode(
                    x=alt.X(
                        "day:N",
                        sort=None,
                        axis=alt.Axis(
                            labelAngle=0,
                            orient="bottom",
                            ticks=False,
                            domain=False,
                            grid=False,
                            title=None,
                            labelFontSize=11,
                            labelPadding=4,
                        ),
                    ),
                    y=alt.Y(
                        "minutes:Q",
                        scale=alt.Scale(domainMin=0, domainMax=max(15, max(velocity_mins)), zero=True),
                        axis=alt.Axis(
                            ticks=False,
                            domain=False,
                            grid=True,
                            gridOpacity=0.2,
                            title="Minutes",
                            tickCount=4,
                        ),
                    ),
                    tooltip=[
                        alt.Tooltip("day:N", title="Day"),
                        alt.Tooltip("minutes:Q", title="Study Minutes"),
                    ],
                )
                .properties(height=152, padding={"top": 4, "right": 4, "bottom": 4, "left": 4})
                .configure_view(strokeWidth=0)
            )
            st.altair_chart(velocity_chart, use_container_width=True, theme="streamlit", key="velocity_chart")

            with st.container():
                if active_sched_dash:
                    total_blocks = len(active_sched_dash.intervals)
                    focus_pct = round((active_sched_dash.total_focus_minutes / max(1, active_sched_dash.total_duration_minutes)) * 100)
                    break_pct = 100 - focus_pct

                    st.markdown(
                        f"""
<div style="font-size: 0.95rem; font-weight: 700; color: var(--text-color, inherit);">
    Topic: {active_sched_dash.topic}
</div>
<div style="font-size: 0.82rem; color: rgba(156, 163, 175, 0.9); margin-top: 2px;">
    Window: {format_time(active_sched_dash.start_time, time_fmt)} - {format_time(active_sched_dash.end_time, time_fmt)} &bull; {active_sched_dash.total_duration_minutes} min total
</div>
<div style="margin-top: 8px; font-size: 0.85rem; font-weight: 600; color: #818cf8; display: flex; align-items: center; gap: 4px;">
    <span class="material-symbols-rounded" style="font-size: 1rem;">timer</span> {active_completed_mins} / {active_total_mins} min completed
</div>
<div style="margin-top: 8px;">
    <div style="display: flex; justify-content: space-between; font-size: 0.72rem; font-weight: 600; color: rgba(156, 163, 175, 0.9); margin-bottom: 3px;">
        <span>Focus: {focus_pct}% ({active_sched_dash.total_focus_minutes}m)</span>
        <span>Recovery: {break_pct}% ({active_sched_dash.total_break_minutes}m)</span>
    </div>
    <div class="timeline-ratio-track" style="margin-bottom: 12px;">
        <div class="ratio-focus-fill" style="width: {focus_pct}%;"></div>
        <div class="ratio-break-fill" style="width: {break_pct}%;"></div>
    </div>
</div>
""",
                        unsafe_allow_html=True,
                    )
                    prog_ratio = active_done_count / total_blocks if total_blocks else 0.0
                    st.progress(prog_ratio, text=f"Progress: {active_completed_mins} / {active_total_mins} min • {active_done_count} / {total_blocks} intervals finished ({round(prog_ratio * 100)}%)")
                else:
                    if today_completed_mins > 0:
                        st.markdown(
                            f"""
<div style="font-size: 0.92rem; font-weight: 600; color: var(--text-color, inherit); margin-bottom: 4px;">
    No active session currently in progress
</div>
<div style="color: #10b981; font-size: 0.88rem; font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; gap: 4px;">
    <span class="material-symbols-rounded" style="font-size: 1.05rem;">check_circle</span> {today_completed_mins} min study completed today
</div>
<div style="color: rgba(156, 163, 175, 0.9); font-size: 0.82rem;">
    Schedule or start your next block in Study Cockpit
</div>
""",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            """
<div style="font-size: 0.92rem; font-weight: 600; color: var(--text-color, inherit); margin-bottom: 4px;">
    No active study block
</div>
<div style="color: rgba(156, 163, 175, 0.9); font-size: 0.88rem; padding: 4px 0;">
    0 min completed today &bull; Schedule or start a session in Study Cockpit
</div>
""",
                            unsafe_allow_html=True,
                        )

    with col_dash_right:
        # Card C: Subject Trees Distribution
        with st.container(border=True):
            st.markdown(
                """
<div class="dash-card-header">
    <h3 class="dash-card-title"><span class="material-symbols-rounded" aria-hidden="true">folder_open</span> Notes by subject</h3>
</div>
""",
                unsafe_allow_html=True,
            )
            if not vault_tree_dash:
                st.markdown("<div style='color: rgba(156, 163, 175, 0.9); font-size: 0.88rem;'>No subject folders created yet.</div>", unsafe_allow_html=True)
            else:
                col_chart, col_list = st.columns([1, 1.35], vertical_alignment="center")

                with col_chart:
                    with st.container():
                        tree_chart_data = [
                            {"subject": s_name, "count": len(n_list)}
                            for s_name, n_list in vault_tree_dash.items()
                        ]
                        df_tree = pd.DataFrame(tree_chart_data)
                        donut_chart = (
                            alt.Chart(df_tree)
                            .mark_arc(innerRadius=40)
                            .encode(
                                theta=alt.Theta("count:Q"),
                                color=alt.Color("subject:N", legend=None),
                                tooltip=[
                                    alt.Tooltip("subject:N", title="Subject"),
                                    alt.Tooltip("count:Q", title="Notes"),
                                ],
                            )
                            .properties(height=152, padding=0)
                            .configure(padding=0)
                            .configure_view(strokeOpacity=0)
                            .configure_axis(grid=False)
                        )
                        st.altair_chart(donut_chart, use_container_width=True, theme="streamlit")

                with col_list:
                    with st.container(key="dashboard_subject_list", gap="xsmall"):
                        for s_name, n_list in vault_tree_dash.items():
                            s_size = sum(n["size_bytes"] for n in n_list)
                            note_count = len(n_list)
                            st.html(
                                f"""
<div class="dash-subject-row">
    <span class="material-symbols-rounded" aria-hidden="true">folder</span>
    <div class="dash-subject-name">
        <strong>{escape(s_name)}</strong>
        <div class="dash-list-meta">{s_size / 1024:.1f} KB</div>
    </div>
    <span class="dash-list-meta">{note_count} {'note' if note_count == 1 else 'notes'}</span>
</div>
"""
                            )

        # Card D: Recent Course Notes in Vault
        with st.container(border=True):
            st.markdown(
                """
<div class="dash-card-header">
    <h3 class="dash-card-title"><span class="material-symbols-rounded" aria-hidden="true">description</span> Recent course notes</h3>
</div>
""",
                unsafe_allow_html=True,
            )
            if not all_notes_dash:
                st.markdown("<div style='color: rgba(156, 163, 175, 0.9); font-size: 0.88rem;'>No course notes found in vault.</div>", unsafe_allow_html=True)
            else:
                recent_sorted = sorted(all_notes_dash, key=lambda x: x.get("modified_at", ""), reverse=True)
                with st.container(key="dashboard_recent_notes", gap="xsmall"):
                    for r_note in recent_sorted:
                        diff_level = r_note.get("difficulty", 3)
                        diff_class = f"badge-diff-{diff_level}"
                        math_tag = '<span class="badge-tag">LaTeX</span> &bull; ' if r_note.get("has_math") else ""

                        st.html(
                            f"""
<div class="dash-quick-row">
    <div>
        <div class="dash-list-meta">
            {escape(r_note.get('subject', 'General'))}
        </div>
        <div style="font-weight: 600; font-size: 0.92rem; color: var(--text-color, inherit);">
            {escape(r_note.get('title', r_note['name']))}
        </div>
        <div style="font-size: 0.72rem; color: rgba(156, 163, 175, 0.8); margin-top: 2px;">
            {math_tag}<span class="{diff_class}">Level {diff_level}</span> &bull; {r_note.get('modified_at', '')}
        </div>
    </div>
</div>
"""
                        )

# ==============================================================================
# VIEW: COURSES (Academic Course Roster & Studio)
# ==============================================================================
elif active_tab == "Courses":
    all_courses = load_courses()
    count_active = sum(1 for c in all_courses if c.get("status") == "Active")
    count_upcoming = sum(1 for c in all_courses if c.get("status") == "Upcoming")
    count_past = sum(1 for c in all_courses if c.get("status") == "Past")

    st.markdown(
        f"""
<div class="minimal-hero-card">
    <div class="page-heading-row">
        <div>
            <h2 class="minimal-hero-title"><span class="material-symbols-rounded" aria-hidden="true">school</span> Courses</h2>
            <p class="minimal-hero-sub">
                Manage course schedules, teaching teams, and exam dates
            </p>
        </div>
        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">calendar_today</span>{formatted_today}</span>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # Minimalist Metric Grid matching Study Cockpit (light background container cards)
    st.markdown(
        f"""
<div class="dash-metric-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 20px;">
    <div class="dash-metric-card">
        <div class="dash-metric-val">{len(all_courses)}</div>
        <div class="dash-metric-lbl">
            <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px; color: #818cf8;">school</span>
            Total Courses
        </div>
        <div class="dash-metric-sub">Registered curricula</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val" style="color: #10b981;">{count_active}</div>
        <div class="dash-metric-lbl">
            <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px; color: #10b981;">play_circle</span>
            Active Courses
        </div>
        <div class="dash-metric-sub">Currently in progress</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val" style="color: #6366f1;">{count_upcoming}</div>
        <div class="dash-metric-lbl">
            <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px; color: #6366f1;">schedule</span>
            Upcoming Courses
        </div>
        <div class="dash-metric-sub">Future academic terms</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val" style="color: #94a3b8;">{count_past}</div>
        <div class="dash-metric-lbl">
            <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px; color: #94a3b8;">history</span>
            Past Courses
        </div>
        <div class="dash-metric-sub">Completed & archived</div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # Section Selector Tabs with Streamlit Materials
    tab_roster, tab_editor = st.tabs([":material/list_alt: Course Roster", ":material/edit_note: Course Editor"])

    # --------------------------------------------------------------------------
    # TAB 1: COURSE ROSTER
    # --------------------------------------------------------------------------
    with tab_roster:
        if not all_courses:
            st.info("No courses registered yet. Switch to the **Course Editor** tab above to add your first course!", icon=":material/school:")
        else:
            # Group courses by their dynamically computed status
            active_courses = [c for c in all_courses if c.get("status") == "Active"]
            upcoming_courses = [c for c in all_courses if c.get("status") == "Upcoming"]
            past_courses = [c for c in all_courses if c.get("status") == "Past"]

            # Sort courses within each group alphabetically
            active_courses.sort(key=lambda x: x.get("name", "").lower())
            upcoming_courses.sort(key=lambda x: x.get("name", "").lower())
            past_courses.sort(key=lambda x: x.get("name", "").lower())

            roster_sections = [
                {
                    "title": "Active courses",
                    "status": "Active",
                    "icon": ":material/play_circle:",
                    "badge_color": "#10b981",
                    "courses": active_courses,
                    "expanded": True,  # Active Courses are opened by default
                    "empty_text": "No active courses",
                },
                {
                    "title": "Upcoming courses",
                    "status": "Upcoming",
                    "icon": ":material/schedule:",
                    "badge_color": "#818cf8",
                    "courses": upcoming_courses,
                    "expanded": False,  # Upcoming courses are closed by default
                    "empty_text": "No upcoming courses",
                },
                {
                    "title": "Past courses",
                    "status": "Past",
                    "icon": ":material/history:",
                    "badge_color": "#94a3b8",
                    "courses": past_courses,
                    "expanded": False,  # Past courses are closed by default
                    "empty_text": "No past courses",
                },
            ]

            for s_idx, sec in enumerate(roster_sections):
                # Section Title with Material Icon and Count Pill
                st.markdown(
                    f"""<div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; margin-bottom:12px;">
                        <h4 class="dash-card-title">
                            <span class="material-symbols-rounded" aria-hidden="true" style="color:{sec['badge_color']};">{sec['icon'].replace(':material/', '').replace(':', '')}</span>
                            {sec['title']}
                        </h4>
                        <span style="font-size:0.8rem; font-weight:600; padding:2px 10px; border-radius:12px; background:rgba(128,128,128,0.12); color:var(--text-color, inherit);">
                            {len(sec['courses'])} course{'s' if len(sec['courses']) != 1 else ''}
                        </span>
                    </div>""",
                    unsafe_allow_html=True,
                )

                if not sec["courses"]:
                    st.caption(f":material/info: {sec['empty_text']}")
                else:
                    grid_cols = st.columns(2)
                    for idx, c in enumerate(sec["courses"]):
                        c_id = c.get("course_id", "")
                        c_name = c.get("name", "Untitled Course")
                        c_status = c.get("status", sec["status"])
                        c_linked = c.get("linked_subject", "")
                        c_start = c.get("start_date", "")
                        c_end = c.get("end_date", "")
                        c_exam = c.get("exam_date", "")

                        with grid_cols[idx % 2]:
                            with st.expander(f"{c_name}", icon=":material/school:", expanded=sec["expanded"]):
                                # Pill Badges: Status, Linked Subject, and Term Dates
                                if c_status == "Active":
                                    status_badge = '<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600; background:rgba(16, 185, 129, 0.16); color:#10b981; border:1px solid rgba(16, 185, 129, 0.35);"><span class="material-symbols-rounded" style="font-size:0.85rem;">play_circle</span> Active</span>'
                                elif c_status == "Upcoming":
                                    status_badge = '<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600; background:rgba(99, 102, 241, 0.16); color:#818cf8; border:1px solid rgba(99, 102, 241, 0.35);"><span class="material-symbols-rounded" style="font-size:0.85rem;">schedule</span> Upcoming</span>'
                                else:
                                    status_badge = '<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600; background:rgba(148, 163, 184, 0.16); color:#94a3b8; border:1px solid rgba(148, 163, 184, 0.35);"><span class="material-symbols-rounded" style="font-size:0.85rem;">history</span> Past</span>'

                                if c_linked and c_linked != "None":
                                    subject_badge = f'<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600; background:rgba(139, 92, 246, 0.14); color:#a78bfa; border:1px solid rgba(139, 92, 246, 0.3);"><span class="material-symbols-rounded" style="font-size:0.85rem;">folder</span> Vault: {c_linked}</span>'
                                else:
                                    subject_badge = '<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600; background:rgba(128, 128, 128, 0.10); color:rgba(156, 163, 175, 0.8); border:1px solid rgba(128, 128, 128, 0.2);"><span class="material-symbols-rounded" style="font-size:0.85rem;">folder_off</span> No Vault Link</span>'

                                date_badge = ""
                                if c_start or c_end:
                                    start_disp = format_date(c_start, date_fmt) if c_start else "Start"
                                    end_disp = format_date(c_end, date_fmt) if c_end else "End"
                                    date_badge = f'<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:9999px; font-size:0.75rem; font-weight:600; background:rgba(59, 130, 246, 0.14); color:#60a5fa; border:1px solid rgba(59, 130, 246, 0.3);"><span class="material-symbols-rounded" style="font-size:0.85rem;">date_range</span> {start_disp} &rarr; {end_disp}</span>'

                                st.markdown(
                                    f'<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:10px;">{status_badge}{subject_badge}{date_badge}</div>',
                                    unsafe_allow_html=True,
                                )

                                # Weekly Schedule Section (using :material/location_on: icon)
                                st.markdown("##### :material/calendar_today: Weekly Schedule")
                                sched_blocks = c.get("schedule", [])
                                if sched_blocks and isinstance(sched_blocks, list):
                                    for blk in sched_blocks:
                                        if isinstance(blk, dict):
                                            b_day = blk.get("day", "Day")
                                            b_st = blk.get("start_time", "")
                                            b_et = blk.get("end_time", "")
                                            b_loc = blk.get("location", "")
                                            time_display = f"{format_time(b_st, time_fmt)} – {format_time(b_et, time_fmt)}" if (b_st and b_et) else (format_time(b_st, time_fmt) or format_time(b_et, time_fmt) or "TBD")
                                            loc_display = f" &bull; :material/location_on: `{b_loc}`" if b_loc else ""
                                            st.markdown(f"- **{b_day}**: {time_display}{loc_display}")
                                else:
                                    st.caption(":material/info: No weekly schedule blocks registered.")

                                st.divider()

                                # Mini Columns for Teaching Team and Assessment/Timeline
                                p_col, a_col = st.columns([1.6, 1.4])
                                with p_col:
                                    st.markdown("##### :material/group: Teaching Team")
                                    # Instructors with mailto links
                                    inst_list = c.get("instructors", [])
                                    if inst_list and isinstance(inst_list, list):
                                        inst_links = [
                                            f"[{i['name']}](mailto:{i['email']})" if i.get("email") else i.get("name", "Instructor")
                                            for i in inst_list
                                            if i.get("name") or i.get("email")
                                        ]
                                        st.markdown(f"- **Instructor(s):** {', '.join(inst_links) if inst_links else 'None listed'}")
                                    else:
                                        st.markdown("- **Instructor(s):** None listed")

                                    # Teaching Assistants with mailto links
                                    tas_list = c.get("tas", [])
                                    if tas_list and isinstance(tas_list, list):
                                        ta_links = [
                                            f"[{t['name']}](mailto:{t['email']})" if t.get("email") else t.get("name", "TA")
                                            for t in tas_list
                                            if t.get("name") or t.get("email")
                                        ]
                                        st.markdown(f"- **TA(s):** {', '.join(ta_links) if ta_links else 'None assigned'}")
                                    else:
                                        st.markdown("- **TA(s):** None assigned")

                                with a_col:
                                    st.markdown("##### :material/event: Timeline & Exam")
                                    if c_start or c_end:
                                        st.markdown(f"**Term Period:**  \n:material/date_range: `{c_start or 'TBD'}` &rarr; `{c_end or 'TBD'}`")
                                    if c_exam:
                                        st.markdown(f"**Target Exam:**  \n:material/calendar_month: `{c_exam}`")
                                    if not c_start and not c_end and not c_exam:
                                        st.caption(":material/info: No term dates or exam scheduled.")

                                st.divider()

                                # Action Buttons
                                act_c1, act_c2 = st.columns(2)
                                with act_c1:
                                    if st.button("Edit Course", icon=":material/edit:", key=f"btn_roster_edit_{c_id}", use_container_width=True):
                                        st.session_state["editing_course_id"] = c_id
                                        st.toast(f"Loaded '{c_name}' into Course Editor", icon=":material/edit:")
                                        st.info("Switch to the **Course Editor** tab above to make edits.", icon=":material/info:")
                                with act_c2:
                                    if st.button("Delete", icon=":material/delete:", key=f"btn_roster_del_{c_id}", use_container_width=True):
                                        delete_course(c_id)
                                        st.toast(f"Deleted course '{c_name}'", icon=":material/delete:")
                                        time.sleep(0.2)
                                        st.rerun()

                # Add clear divider between sections (except after the last section)
                if s_idx < len(roster_sections) - 1:
                    st.markdown("<div style='margin-top: 14px; margin-bottom: 14px;'></div>", unsafe_allow_html=True)
                    st.divider()

    # --------------------------------------------------------------------------
    # TAB 2: COURSE EDITOR
    # --------------------------------------------------------------------------
    with tab_editor:
        courses = load_courses()

        # Selector to switch between Adding New Course or Editing Existing Course
        course_selector_options = [("__new__", "+ Add New Course")] + [
            (c["course_id"], f"{c['name']} ({c.get('status', 'Active')}) — ID: {c['course_id']}")
            for c in courses
            if c.get("course_id")
        ]
        opt_keys = [opt[0] for opt in course_selector_options]

        # Handle routing from Course Roster "Edit Course" button
        if "editing_course_id" in st.session_state:
            routed_c_id = st.session_state.pop("editing_course_id")
            if routed_c_id in opt_keys:
                st.session_state["course_editor_select_course_option"] = routed_c_id

        # Ensure selector state is initialized and valid
        if "course_editor_select_course_option" not in st.session_state or st.session_state["course_editor_select_course_option"] not in opt_keys:
            st.session_state["course_editor_select_course_option"] = opt_keys[0]

        selected_course_id = st.selectbox(
            ":material/sync_alt: Select Course to Edit or Create New",
            options=opt_keys,
            format_func=lambda k: dict(course_selector_options).get(k, k),
            key="course_editor_select_course_option",
            help="Choose '+ Add New Course' to configure a new profile or select an existing course to edit.",
        )

        is_editing = (selected_course_id != "__new__")
        selected_course = next((c for c in courses if c.get("course_id") == selected_course_id), None) if is_editing else None

        st.markdown("<div style='margin-top: 6px;'></div>", unsafe_allow_html=True)

        if is_editing and selected_course:
            st.info(f"Editing profile for: **{selected_course['name']}** (`{selected_course['course_id']}`)", icon=":material/edit:")
        else:
            st.info("Fill out the form below to register a new academic course.", icon=":material/add_circle:")

        form_key = f"course_form_{selected_course['course_id'] if selected_course else 'new'}"
        with st.form(key=form_key, clear_on_submit=False):
            # Row 1: Course Name and Linked Vault Subject
            c_col_name, c_col_vault = st.columns([2.5, 1.5])
            with c_col_name:
                init_name = selected_course.get("name", "") if selected_course else ""
                c_name = st.text_input(":material/menu_book: Course Name", value=init_name, placeholder="e.g. CS 101: Introduction to Computer Science")
            with c_col_vault:
                subject_folders = list_subject_folders()
                vault_options = ["None"] + subject_folders
                curr_linked = selected_course.get("linked_subject", "") if selected_course else ""
                if not curr_linked or curr_linked not in subject_folders:
                    vault_idx = 0
                else:
                    vault_idx = vault_options.index(curr_linked)

                c_linked_subject = st.selectbox(
                    ":material/folder: Linked Vault Subject",
                    options=vault_options,
                    index=vault_idx,
                    help="Connect this course to a top-level Subject Folder in your Notes Vault.",
                )

            # Row 2: Term Dates, Target Exam Date, and Status
            col_start, col_end, col_exam, col_status = st.columns([1.2, 1.2, 1.2, 1.1])
            with col_start:
                init_start_str = selected_course.get("start_date", "") if selected_course else ""
                parsed_start_date = None
                if init_start_str:
                    try:
                        parsed_start_date = datetime.strptime(init_start_str[:10], "%Y-%m-%d").date()
                    except Exception:
                        parsed_start_date = None
                c_start_date = st.date_input(
                    ":material/date_range: Start Date",
                    value=parsed_start_date,
                    help="Course start date. If today is before this date, course status will automatically be Upcoming.",
                )

            with col_end:
                init_end_str = selected_course.get("end_date", "") if selected_course else ""
                parsed_end_date = None
                if init_end_str:
                    try:
                        parsed_end_date = datetime.strptime(init_end_str[:10], "%Y-%m-%d").date()
                    except Exception:
                        parsed_end_date = None
                c_end_date = st.date_input(
                    ":material/event_available: End Date",
                    value=parsed_end_date,
                    help="Course end date. If today is past this date, course status will automatically be Past.",
                )

            with col_exam:
                init_exam_str = selected_course.get("exam_date", "") if selected_course else ""
                parsed_exam_date = None
                if init_exam_str:
                    try:
                        parsed_exam_date = datetime.strptime(init_exam_str[:10], "%Y-%m-%d").date()
                    except Exception:
                        parsed_exam_date = None
                c_exam_date = st.date_input(
                    ":material/event: Exam Date",
                    value=parsed_exam_date,
                    help="Target examination date for this course.",
                )

            with col_status:
                status_options = ["Active", "Upcoming", "Past"]
                init_status = selected_course.get("status", "Active") if selected_course else "Active"
                status_idx = status_options.index(init_status) if init_status in status_options else 0
                c_status = st.selectbox(
                    ":material/flag: Status",
                    options=status_options,
                    index=status_idx,
                    help="Course status. Dynamically determined by Start & End Dates when dates are set.",
                )

            st.divider()

            # Instructors (st.data_editor to add/delete rows for multiple instructors)
            st.markdown("#### :material/person: Instructors")
            st.caption("Add or remove instructors with their primary contact email addresses.")
            raw_instructors = selected_course.get("instructors", []) if selected_course else []
            if raw_instructors and isinstance(raw_instructors, list):
                inst_records = [{"name": str(i.get("name", "")), "email": str(i.get("email", ""))} for i in raw_instructors if isinstance(i, dict)]
                inst_df = pd.DataFrame(inst_records) if inst_records else pd.DataFrame([{"name": "", "email": ""}])
            else:
                inst_df = pd.DataFrame([{"name": "", "email": ""}])

            edited_inst_df = st.data_editor(
                inst_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "name": st.column_config.TextColumn("Instructor Name", required=True),
                    "email": st.column_config.TextColumn("Instructor Email", required=False),
                },
                key=f"inst_editor_{selected_course['course_id'] if selected_course else 'new'}",
            )

            st.divider()

            # Schedule (st.data_editor for adding specific weekly blocks: Day of Week, Start Time, End Time, Location)
            st.markdown("#### :material/calendar_today: Weekly Schedule Blocks")
            st.caption("Configure recurring weekly class meetings, lectures, and lab locations.")
            raw_sched = selected_course.get("schedule", []) if selected_course else []
            if raw_sched and isinstance(raw_sched, list):
                sched_records = [
                    {
                        "day": str(s.get("day", "Monday")),
                        "start_time": format_time(str(s.get("start_time", "")), time_fmt),
                        "end_time": format_time(str(s.get("end_time", "")), time_fmt),
                        "location": str(s.get("location", "")),
                    }
                    for s in raw_sched
                    if isinstance(s, dict)
                ]
                sched_df = pd.DataFrame(sched_records) if sched_records else pd.DataFrame([{"day": "Monday", "start_time": "", "end_time": "", "location": ""}])
            else:
                sched_df = pd.DataFrame([{"day": "Monday", "start_time": "", "end_time": "", "location": ""}])

            edited_sched_df = st.data_editor(
                sched_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "day": st.column_config.SelectboxColumn(
                        "Day of Week",
                        options=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                        required=True,
                    ),
                    "start_time": st.column_config.TextColumn("Start Time", help=f"e.g. {format_time('14:15', time_fmt)}"),
                    "end_time": st.column_config.TextColumn("End Time", help=f"e.g. {format_time('16:00', time_fmt)}"),
                    "location": st.column_config.TextColumn("Location", help="e.g. Hall B / Zoom"),
                },
                key=f"sched_editor_{selected_course['course_id'] if selected_course else 'new'}",
            )

            st.divider()

            # Teaching Assistants (st.data_editor)
            st.markdown("#### :material/group: Teaching Assistants (TAs)")
            st.caption("Use the table below to dynamically add or edit Teaching Assistants and their email addresses.")
            initial_tas = selected_course.get("tas", []) if selected_course else []
            if initial_tas and isinstance(initial_tas, list):
                ta_records = [{"name": str(t.get("name", "")), "email": str(t.get("email", ""))} for t in initial_tas if isinstance(t, dict)]
                ta_df = pd.DataFrame(ta_records) if ta_records else pd.DataFrame([{"name": "", "email": ""}])
            else:
                ta_df = pd.DataFrame([{"name": "", "email": ""}])

            edited_tas_df = st.data_editor(
                ta_df,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "name": st.column_config.TextColumn("TA Name", required=True),
                    "email": st.column_config.TextColumn("TA Email", required=False),
                },
                key=f"ta_editor_{selected_course['course_id'] if selected_course else 'new'}",
            )

            # Form Submission
            submit_btn_label = "Update Course" if is_editing else "Add Course"
            submit_icon = ":material/save:" if is_editing else ":material/add_circle:"
            submitted = st.form_submit_button(submit_btn_label, type="primary", icon=submit_icon, use_container_width=True)

            if submitted:
                if not c_name.strip():
                    st.error("Course name is required.", icon=":material/error:")
                else:
                    # Clean Instructors
                    cleaned_instructors = []
                    if edited_inst_df is not None:
                        for row in edited_inst_df.to_dict(orient="records"):
                            i_n = str(row.get("name") or "").strip() if pd.notna(row.get("name")) else ""
                            i_e = str(row.get("email") or "").strip() if pd.notna(row.get("email")) else ""
                            if i_n or i_e:
                                cleaned_instructors.append({"name": i_n, "email": i_e})

                    # Clean Schedule
                    cleaned_schedule = []
                    if edited_sched_df is not None:
                        for row in edited_sched_df.to_dict(orient="records"):
                            s_d = str(row.get("day") or "Monday").strip() if pd.notna(row.get("day")) else "Monday"
                            s_st = str(row.get("start_time") or "").strip() if pd.notna(row.get("start_time")) else ""
                            s_et = str(row.get("end_time") or "").strip() if pd.notna(row.get("end_time")) else ""
                            s_loc = str(row.get("location") or "").strip() if pd.notna(row.get("location")) else ""
                            if s_st or s_et or s_loc:
                                cleaned_schedule.append({
                                    "day": s_d,
                                    "start_time": format_time(s_st, "24h"),
                                    "end_time": format_time(s_et, "24h"),
                                    "location": s_loc,
                                })

                    # Clean TAs
                    cleaned_tas = []
                    if edited_tas_df is not None:
                        for row in edited_tas_df.to_dict(orient="records"):
                            t_n = str(row.get("name") or "").strip() if pd.notna(row.get("name")) else ""
                            t_e = str(row.get("email") or "").strip() if pd.notna(row.get("email")) else ""
                            if t_n or t_e:
                                cleaned_tas.append({"name": t_n, "email": t_e})

                    start_date_str = c_start_date.strftime("%Y-%m-%d") if (c_start_date and hasattr(c_start_date, "strftime")) else ""
                    end_date_str = c_end_date.strftime("%Y-%m-%d") if (c_end_date and hasattr(c_end_date, "strftime")) else ""
                    exam_date_str = c_exam_date.strftime("%Y-%m-%d") if (c_exam_date and hasattr(c_exam_date, "strftime")) else ""
                    course_id_to_use = selected_course.get("course_id") if (is_editing and selected_course) else f"course_{uuid.uuid4().hex[:8]}"

                    # Automatically calculate status based on start_date and end_date
                    effective_status = compute_course_status({
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                        "status": c_status,
                    })

                    course_payload = {
                        "course_id": course_id_to_use,
                        "name": c_name.strip(),
                        "status": effective_status,
                        "instructors": cleaned_instructors,
                        "tas": cleaned_tas,
                        "schedule": cleaned_schedule,
                        "linked_subject": c_linked_subject if c_linked_subject != "None" else "",
                        "start_date": start_date_str,
                        "end_date": end_date_str,
                        "exam_date": exam_date_str,
                    }

                    save_course(course_payload)
                    st.toast(f"Course '{c_name.strip()}' saved successfully!", icon=":material/check_circle:")
                    st.session_state["editing_course_id"] = course_id_to_use
                    # Clear data editor session state so that on rerun, fresh saved data is loaded
                    st.session_state.pop(f"inst_editor_{course_id_to_use}", None)
                    st.session_state.pop(f"sched_editor_{course_id_to_use}", None)
                    st.session_state.pop(f"ta_editor_{course_id_to_use}", None)
                    st.session_state.pop("inst_editor_new", None)
                    st.session_state.pop("sched_editor_new", None)
                    st.session_state.pop("ta_editor_new", None)
                    time.sleep(0.3)
                    st.rerun()

        # Delete button when editing an existing course
        if is_editing and selected_course:
            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            if st.button("Delete this Course", icon=":material/delete:", type="secondary", key=f"btn_del_editor_{selected_course['course_id']}", use_container_width=True):
                delete_course(selected_course["course_id"])
                st.toast(f"Course '{selected_course['name']}' deleted.", icon=":material/delete:")
                st.session_state["editing_course_id"] = "__new__"
                st.session_state.pop(f"inst_editor_{selected_course['course_id']}", None)
                st.session_state.pop(f"sched_editor_{selected_course['course_id']}", None)
                st.session_state.pop(f"ta_editor_{selected_course['course_id']}", None)
                time.sleep(0.3)
                st.rerun()

# ==============================================================================
# VIEW 1: STUDY COCKPIT (Focus Orchestration, Live Timeline & History)
# ==============================================================================
elif active_tab == "Study Cockpit":
    if "session_completed_intervals" not in st.session_state:
        st.session_state["session_completed_intervals"] = set()

    # Minimalist Workspace Header (Global Material Iconography)
    st.markdown(
        f"""
<div class="minimal-hero-card">
    <div class="page-heading-row">
        <div>
            <h2 class="minimal-hero-title"><span class="material-symbols-rounded" aria-hidden="true">speed</span> Study Cockpit</h2>
            <p class="minimal-hero-sub">
                Plan study blocks and track focus and break intervals
            </p>
        </div>
        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">calendar_today</span>{formatted_today}</span>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # Load persistent study blocks from JSON registry
    all_saved_blocks = load_study_blocks()
    today_saved_blocks = [b for b in all_saved_blocks if b.get("session_date") == today_str]

    if "manually_reinstated_block_ids" not in st.session_state:
        st.session_state["manually_reinstated_block_ids"] = {
            b.get("id") for b in all_saved_blocks if b.get("id") and b.get("manually_reinstated")
        }

    if "historical_block_ids" not in st.session_state:
        st.session_state["historical_block_ids"] = {
            b.get("id") for b in all_saved_blocks if b.get("id") and b.get("is_historical")
        }

    # Move any active study blocks whose end time has passed (or marked historical) to historical registry
    # Also strictly ensure that upcoming study blocks (session_date != today_str) do not appear in active timeline
    # Unless explicitly reinstated by the user pressing 'Activate in Timeline'
    if "active_study_block_ids" in st.session_state:
        reinstated_ids = st.session_state["manually_reinstated_block_ids"]
        block_id_lookup = {b.get("id"): b for b in all_saved_blocks if b.get("id")}
        
        expired_ids = set()
        for active_id in list(st.session_state["active_study_block_ids"]):
            blk = block_id_lookup.get(active_id)
            if blk:
                is_expired_or_historical = (
                    is_block_ended(blk)
                    or blk.get("is_historical")
                    or active_id in st.session_state.get("historical_block_ids", set())
                )
                if is_expired_or_historical and active_id not in reinstated_ids:
                    expired_ids.add(active_id)
                # Upcoming/past blocks whose session_date != today_str must never appear in today's active timeline
                if blk.get("session_date") != today_str:
                    expired_ids.add(active_id)
            else:
                expired_ids.add(active_id)

        if expired_ids:
            st.session_state["active_study_block_ids"].difference_update(expired_ids)
            st.session_state["session_completed_intervals"] = {
                item for item in st.session_state.get("session_completed_intervals", set())
                if not any(isinstance(item, str) and item.startswith(f"{exp_id}_") for exp_id in expired_ids)
            }

    # Initialize active study block IDs on first load
    # CRITICAL: Ended blocks or blocks moved to historical are in historical registry and NOT reinstated again,
    # unless user presses 'Activate in Timeline'. Only today's blocks can be active.
    if "active_study_block_ids" not in st.session_state:
        st.session_state["active_study_block_ids"] = set()
        reinstated_init = st.session_state.get("manually_reinstated_block_ids", set())
        active_today = [
            b["id"] for b in today_saved_blocks
            if b.get("id")
            and (not is_block_ended(b) or b.get("id") in reinstated_init)
            and not b.get("is_historical")
            and b.get("id") not in st.session_state.get("historical_block_ids", set())
        ]
        if active_today:
            st.session_state["active_study_block_ids"] = set(active_today)

    if "session_completed_intervals" not in st.session_state:
        st.session_state["session_completed_intervals"] = set()

    # Synchronize active study blocks - strictly for today's date
    active_ids = st.session_state["active_study_block_ids"]
    block_map = {b["id"]: b for b in all_saved_blocks if b.get("id")}
    if "study_block_data" in st.session_state and st.session_state["study_block_data"].get("id"):
        curr_b_id = st.session_state["study_block_data"]["id"]
        if curr_b_id in active_ids and st.session_state["study_block_data"].get("session_date") == today_str:
            block_map[curr_b_id] = st.session_state["study_block_data"]

    active_blocks = [
        block_map[bid] for bid in active_ids
        if bid in block_map and block_map[bid].get("session_date") == today_str
    ]
    active_blocks = sort_study_blocks_chronologically(active_blocks)

    if active_blocks:
        st.session_state["study_block_schedule"] = dict_to_study_block_schedule(active_blocks[0])
        st.session_state["study_block_data"] = active_blocks[0]
    else:
        st.session_state.pop("study_block_schedule", None)
        st.session_state.pop("study_block_data", None)

    # Minimalist Study KPI Strip
    completed_intervals = st.session_state.get("session_completed_intervals", set())
    total_active_intervals = sum(len(b.get("intervals", [])) for b in active_blocks)
    active_completed_count = 0
    for b in active_blocks:
        b_id = b.get("id", "")
        for inter in b.get("intervals", []):
            idx = inter.get("index", 0)
            uid = f"{b_id}_{idx}"
            if uid in completed_intervals or (len(active_blocks) == 1 and idx in completed_intervals):
                active_completed_count += 1

    if active_blocks:
        if len(active_blocks) == 1:
            active_sched_title = active_blocks[0].get("topic", "Active Session")
            active_sched_sub = f"{active_blocks[0].get('total_duration_minutes', 0)} min • {len(active_blocks[0].get('intervals', []))} intervals"
        else:
            total_active_dur = sum(b.get("total_duration_minutes", 0) for b in active_blocks)
            active_sched_title = f"{len(active_blocks)} Active Blocks"
            active_sched_sub = f"{total_active_dur} min total across {len(active_blocks)} sessions"
        intervals_done_str = f"{active_completed_count} / {total_active_intervals}"
    else:
        active_sched_title = "Ready to Plan"
        active_sched_sub = "No active session planned"
        intervals_done_str = "0"

    reinstated_kpi = st.session_state.get("manually_reinstated_block_ids", set())
    today_active_upcoming_count = len([
        b for b in today_saved_blocks
        if (not is_block_ended(b) or b.get("id") in reinstated_kpi)
        and not b.get("is_historical")
        and b.get("id") not in st.session_state.get("historical_block_ids", set())
    ])
    upcoming_kpi_count = len([
        b for b in all_saved_blocks
        if b.get("session_date") and b.get("session_date") > today_str
        and not b.get("is_historical")
        and b.get("id") not in st.session_state.get("historical_block_ids", set())
    ])
    agenda_kpi_sub = f"Today: {today_active_upcoming_count} &bull; Upcoming: {upcoming_kpi_count}" if upcoming_kpi_count > 0 else f"Active &bull; {today_str}"

    st.markdown(
        f"""
<div class="dash-metric-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 20px;">
    <div class="dash-metric-card">
        <div class="dash-metric-val" style="font-size: 1.15rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
            {active_sched_title}
        </div>
        <div class="dash-metric-lbl">Active Session</div>
        <div class="dash-metric-sub">{active_sched_sub}</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val">{intervals_done_str}</div>
        <div class="dash-metric-lbl">Intervals Finished</div>
        <div class="dash-metric-sub">Focus milestone progress</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val">{today_active_upcoming_count}</div>
        <div class="dash-metric-lbl">Today's Agenda</div>
        <div class="dash-metric-sub">{agenda_kpi_sub}</div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    valid_cockpit_tabs = [
        ":material/timer: Study Timeline",
        ":material/calendar_today: Daily Agenda & History",
        ":material/edit_calendar: Study Block Creator & Editor",
        ":material/calendar_month: Study Calendar",
    ]
    cockpit_tab_default = st.session_state.get("cockpit_active_tab", valid_cockpit_tabs[0])
    if cockpit_tab_default not in valid_cockpit_tabs:
        cockpit_tab_default = valid_cockpit_tabs[0]
        st.session_state["cockpit_active_tab"] = cockpit_tab_default

    tab_timeline, tab_agenda, tab_editor, tab_calendar = st.tabs(
        valid_cockpit_tabs,
        default=cockpit_tab_default,
        key="schedule_workspace_tabs",
    )

    # -------------------------------------------------------------------------
    # TAB 1: :material/timer: STUDY TIMELINE
    # -------------------------------------------------------------------------
    with tab_timeline:
        if not active_blocks:
            st.markdown(
                """
<div class="timeline-card" style="border-left: 4px solid #6366f1; margin-top: 14px;">
    <div style="font-weight: 600; font-size: 1.02rem; color: var(--text-color, inherit);">
        <span class="material-symbols-rounded" style="vertical-align: -3px; font-size: 1.2rem; color: #818cf8;">info</span> No Active Study Blocks Loaded
    </div>
    <div style="font-size: 0.86rem; color: rgba(156, 163, 175, 0.9); margin-top: 6px; line-height: 1.5;">
        There are currently no active study blocks in your timeline. You can activate blocks from <strong>Daily Agenda & History</strong>, or create and register new study blocks in the <strong>Study Block Creator & Editor</strong> tab.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)
            col_empty_act1, col_empty_act2 = st.columns(2)
            with col_empty_act1:
                reinstated_empty = st.session_state.get("manually_reinstated_block_ids", set())
                today_active_unexpired = [
                    b for b in today_saved_blocks
                    if (not is_block_ended(b) or b.get("id") in reinstated_empty)
                    and not b.get("is_historical")
                    and b.get("id") not in st.session_state.get("historical_block_ids", set())
                ]
                if today_active_unexpired:
                    if st.button("Activate Today's Blocks", icon=":material/playlist_play:", type="primary", key="btn_empty_act_today", use_container_width=True):
                        ids = {b["id"] for b in today_active_unexpired if b.get("id")}
                        st.session_state["active_study_block_ids"].update(ids)
                        st.session_state["manually_reinstated_block_ids"].update(ids)
                        st.toast("Activated today's study blocks!", icon=":material/check:")
                        st.rerun()
                elif all_saved_blocks:
                    if st.button("Activate Most Recent Block for Today", icon=":material/play_arrow:", type="primary", key="btn_empty_act_latest", use_container_width=True):
                        latest_id = all_saved_blocks[0].get("id")
                        if latest_id:
                            activate_historical_study_block(latest_id, target_date=today_str)
                            st.session_state["active_study_block_ids"] = {latest_id}
                            st.session_state["manually_reinstated_block_ids"].add(latest_id)
                            st.toast("Activated block for today's timeline!", icon=":material/check:")
                        st.rerun()
            with col_empty_act2:
                if st.button("Create New Study Block", icon=":material/add_circle:", key="btn_empty_create_new", use_container_width=True):
                    st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                    st.session_state["block_editor_mode_selector"] = "__new__"
                    st.rerun()
        else:
            total_active_blocks = len(active_blocks)
            total_active_dur = sum(b.get("total_duration_minutes", 0) for b in active_blocks)
            total_active_focus = sum(b.get("total_focus_minutes", 0) for b in active_blocks)
            total_active_break = sum(b.get("total_break_minutes", 0) for b in active_blocks)

            total_focus_intervals = sum(
                len([i for i in b.get("intervals", []) if i.get("interval_type") == "focus"])
                for b in active_blocks
            )
            total_break_intervals = sum(
                len([i for i in b.get("intervals", []) if i.get("interval_type") == "break"])
                for b in active_blocks
            )

            # Earliest start to latest end
            first_block = active_blocks[0]
            last_block = active_blocks[-1]
            if first_block.get("session_date") == last_block.get("session_date"):
                time_span_str = f"{format_time(first_block.get('start_time', ''), time_fmt)} — {format_time(last_block.get('end_time', ''), time_fmt)}"
            else:
                time_span_str = f"{format_date(first_block.get('session_date', ''), date_fmt)} {format_time(first_block.get('start_time', ''), time_fmt)} → {format_date(last_block.get('session_date', ''), date_fmt)} {format_time(last_block.get('end_time', ''), time_fmt)}"

            st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
            col_title_l, col_title_r = st.columns([3.2, 1.8])
            with col_title_l:
                if total_active_blocks == 1:
                    st.markdown(f"#### :material/analytics: Session Overview: **{first_block.get('topic')}**")
                else:
                    st.markdown(f"#### :material/analytics: Multi-Block Study Timeline: **{total_active_blocks} Active Sessions**")
            with col_title_r:
                top_b1, top_b2 = st.columns(2)
                with top_b1:
                    if st.button("Reset Checks", icon=":material/restart_alt:", key="btn_reset_timeline_checks", use_container_width=True, help="Reset completed status for active intervals"):
                        st.session_state["session_completed_intervals"] = set()
                        st.rerun()
                with top_b2:
                    if st.button("Clear All", icon=":material/clear_all:", key="btn_clear_all_active", use_container_width=True, help="Deactivate all blocks from timeline"):
                        active_ids_list = list(st.session_state.get("active_study_block_ids", set()))
                        if active_ids_list:
                            mark_study_blocks_historical(active_ids_list)
                            st.session_state.setdefault("historical_block_ids", set()).update(active_ids_list)
                            st.session_state.get("manually_reinstated_block_ids", set()).difference_update(active_ids_list)
                        st.session_state["active_study_block_ids"] = set()
                        st.toast("Moved active study blocks to Historical Study Block Registry", icon=":material/history:")
                        st.rerun()

            # Minimalist Metric Grid
            st.markdown(
                f"""
<div class="dash-metric-grid">
    <div class="dash-metric-card">
        <div class="dash-metric-val">{total_active_dur} min</div>
        <div class="dash-metric-lbl">Total Schedule</div>
        <div class="dash-metric-sub">{total_active_blocks} session block{'s' if total_active_blocks > 1 else ''}</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val">{total_active_focus} min</div>
        <div class="dash-metric-lbl">Deep Focus Time</div>
        <div class="dash-metric-sub">{total_focus_intervals} intervals</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val">{total_active_break} min</div>
        <div class="dash-metric-lbl">Rest & Recovery</div>
        <div class="dash-metric-sub">{total_break_intervals} intervals</div>
    </div>
    <div class="dash-metric-card">
        <div class="dash-metric-val">{time_span_str}</div>
        <div class="dash-metric-lbl">Timeline Window</div>
        <div class="dash-metric-sub">Earliest to Latest</div>
    </div>
</div>
""",
                unsafe_allow_html=True,
            )

            # Segmented Focus-to-Break Ratio Bar
            total_mins = max(1, total_active_dur)
            focus_pct = round((total_active_focus / total_mins) * 100)
            break_pct = 100 - focus_pct

            st.markdown(
                f"""
<div style="margin-top: 10px; margin-bottom: 12px;">
    <div style="display: flex; justify-content: space-between; font-size: 0.74rem; font-weight: 600; color: rgba(156, 163, 175, 0.9); margin-bottom: 4px;">
        <span><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.95rem;">psychology</span> Deep Focus: {focus_pct}% ({total_active_focus}m)</span>
        <span><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.95rem;">coffee</span> Recovery: {break_pct}% ({total_active_break}m)</span>
    </div>
    <div class="timeline-ratio-track">
        <div class="ratio-focus-fill" style="width: {focus_pct}%;"></div>
        <div class="ratio-break-fill" style="width: {break_pct}%;"></div>
    </div>
</div>
""",
                unsafe_allow_html=True,
            )

            # Interactive Real-Time Execution Progress
            prog_ratio = active_completed_count / total_active_intervals if total_active_intervals else 0.0
            st.progress(
                prog_ratio,
                text=f":material/check_circle: Overall Timeline Progress: {active_completed_count} of {total_active_intervals} blocks completed ({round(prog_ratio * 100)}%)"
            )

            # --- Card-Based Main Study Timeline ---
            st.markdown("<div style='margin-top: 18px;'></div>", unsafe_allow_html=True)
            st.markdown("#### :material/timeline: Chronological Study Timeline")

            for block_idx, block_dict in enumerate(active_blocks):
                b_id = block_dict.get("id", f"block_{block_idx}")
                b_topic = block_dict.get("topic", "General Study")
                b_date = block_dict.get("session_date", "")
                b_start = block_dict.get("start_time", "")
                b_end = block_dict.get("end_time", "")
                b_dur = block_dict.get("total_duration_minutes", 0)
                b_focus = block_dict.get("total_focus_minutes", b_dur)
                b_break = block_dict.get("total_break_minutes", 0)

                # Visual Divider between different study blocks
                if block_idx > 0:
                    st.markdown(
                        f"""
<div style="margin: 36px 0 24px 0; display: flex; align-items: center; text-align: center;">
    <div style="flex-grow: 1; height: 1.5px; background: linear-gradient(to right, transparent, rgba(99, 102, 241, 0.25), rgba(99, 102, 241, 0.75));"></div>
    <div style="margin: 0 16px; padding: 4px 12px; background: rgba(99, 102, 241, 0.12); border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 8px; font-size: 0.8rem; font-weight: 600; color: #818cf8; display: flex; align-items: center; gap: 8px; box-shadow: 0 2px 10px rgba(99, 102, 241, 0.1);">
        <span class="material-symbols-rounded" style="font-size: 1.05rem;">arrow_downward</span>
        Next study block &bull; {b_start} &bull; {b_topic}
    </div>
    <div style="flex-grow: 1; height: 1.5px; background: linear-gradient(to left, transparent, rgba(99, 102, 241, 0.25), rgba(99, 102, 241, 0.75));"></div>
</div>
""",
                        unsafe_allow_html=True,
                    )

                # Linked notes pills for this study block
                raw_linked_notes = block_dict.get("linked_notes")
                if isinstance(raw_linked_notes, list):
                    b_notes = [n for n in raw_linked_notes if n]
                elif block_dict.get("linked_note"):
                    b_notes = [n.strip() for n in block_dict["linked_note"].split(",") if n.strip()]
                else:
                    b_notes = []

                notes_pills_html = "".join([
                    f'<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; background: rgba(99, 102, 241, 0.12); border: 1px solid rgba(99, 102, 241, 0.35); border-radius: 6px; font-size: 0.78rem; color: #818cf8;"><span class="material-symbols-rounded" style="font-size: 0.85rem;">link</span><strong>{note}</strong></span>'
                    for note in b_notes
                ])
                notes_row_html = (
                    f'<div style="margin-top: 6px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px;"><span style="font-size: 0.78rem; color: rgba(156, 163, 175, 0.9); font-weight: 600;"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.9rem; color: #818cf8;">auto_stories</span> Linked:</span>{notes_pills_html}</div>'
                    if notes_pills_html else ""
                )

                # Study Block Header Card
                hdr_col_info, hdr_col_act = st.columns([3.8, 1.4])
                with hdr_col_info:
                    seq_badge = f"Block {block_idx + 1} of {total_active_blocks}"
                    b_date_hist = block_dict.get("date_history", [])

                    badges_list = [
                        f'<span style="padding: 2px 8px; background: rgba(99, 102, 241, 0.18); border-radius: 4px; font-size: 0.72rem; font-weight: 700; color: #a5b4fc;">{seq_badge}</span>',
                        f'<strong style="font-size: 1.05rem; color: var(--text-color, inherit);">{b_topic}</strong>',
                        f'<span class="time-badge"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.82rem;">schedule</span> {b_start} — {b_end}</span>',
                        f'<span class="time-badge"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.82rem;">calendar_today</span> {b_date}</span>',
                        f'<span class="time-badge">{b_dur} min ({b_focus}m / {b_break}m)</span>',
                    ]
                    if b_date_hist:
                        badges_list.append(
                            f'<span class="time-badge" style="border-color: rgba(129, 140, 248, 0.4); background: rgba(99, 102, 241, 0.15); color: #a5b4fc;"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.82rem;">history</span> Rescheduled from {b_date_hist[-1].get("previous_date")}</span>'
                        )

                    top_row_html = f'<div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">{"".join(badges_list)}</div>'

                    inner_card_items = [top_row_html]
                    if notes_row_html:
                        inner_card_items.append(notes_row_html)
                    if b_date_hist:
                        date_hist_items = " &bull; ".join([f"<code>{h.get('previous_date')}</code> → <code>{h.get('new_date')}</code> ({h.get('changed_at', '')})" for h in b_date_hist])
                        inner_card_items.append(f'<div style="margin-top: 6px; font-size: 0.77rem; color: #a5b4fc;"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.85rem;">history</span> <strong>Date Change History:</strong> {date_hist_items}</div>')

                    card_html = f'<div style="background: rgba(99, 102, 241, 0.05); border: 1px solid rgba(99, 102, 241, 0.22); border-left: 4px solid #6366f1; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px;">{"".join(inner_card_items)}</div>'

                    st.markdown(card_html, unsafe_allow_html=True)
                with hdr_col_act:
                    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                    b_act1, b_act2 = st.columns(2)
                    with b_act1:
                        if st.button("Edit", icon=":material/edit:", key=f"tl_edit_{b_id}_{block_idx}", use_container_width=True, help="Edit this block in Block Creator & Editor"):
                            st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                            st.session_state["block_editor_mode_selector"] = b_id
                            st.rerun()
                    with b_act2:
                        if st.button("Remove", icon=":material/visibility_off:", key=f"tl_remove_{b_id}_{block_idx}", use_container_width=True, help="Remove this block from active timeline"):
                            mark_study_block_historical(b_id)
                            st.session_state.setdefault("historical_block_ids", set()).add(b_id)
                            st.session_state["active_study_block_ids"].discard(b_id)
                            st.session_state.get("manually_reinstated_block_ids", set()).discard(b_id)
                            st.toast(f"Moved '{b_topic}' to Historical Study Block Registry", icon=":material/history:")
                            st.rerun()

                # Sequential Intervals for this block
                block_sched = dict_to_study_block_schedule(block_dict)
                for interval in block_sched.intervals:
                    interval_uid = f"{b_id}_{interval.index}"
                    is_completed = (interval_uid in completed_intervals) or (total_active_blocks == 1 and interval.index in completed_intervals)
                    is_focus = interval.interval_type == "focus"

                    card_type_class = "timeline-card-focus" if is_focus else "timeline-card-break"
                    completed_class = "timeline-card-completed" if is_completed else ""
                    badge_type_class = "badge-focus" if is_focus else "badge-break"

                    type_label = "Deep Focus" if is_focus else "Rest & Recovery"
                    type_icon_name = "psychology" if is_focus else "coffee"
                    seq_text = f"#{interval.index:02d} {type_label.upper()}"

                    status_pill = (
                        '<span class="badge-complete"><span class="material-symbols-rounded" style="vertical-align: -3px; font-size: 0.85rem;">check_circle</span> Completed</span>'
                        if is_completed
                        else f'<span class="{badge_type_class}"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.85rem;">{type_icon_name}</span> {type_label}</span>'
                    )

                    card_col, chk_col = st.columns([5, 1])

                    with card_col:
                        st.markdown(
                            f"""
<div class="timeline-card {card_type_class} {completed_class}">
    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
        <div style="display: flex; align-items: center; gap: 8px;">
            <span class="step-seq-pill">{seq_text}</span>
            <span class="time-badge"><span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 0.82rem;">schedule</span> {format_time(interval.start_time, time_fmt)} — {format_time(interval.end_time, time_fmt)}</span>
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
            <span class="time-badge">{interval.duration_minutes} min</span>
            {status_pill}
        </div>
    </div>
    <div style="margin-top: 8px;">
        <span style="font-size: 1.02rem; font-weight: 700; color: var(--text-color, inherit);">
            {interval.title}
        </span>
        <div style="color: rgba(156, 163, 175, 0.9); font-size: 0.85rem; margin-top: 3px;">
            {interval.description or f"Focus block allocated for {b_topic}."}
        </div>
    </div>
</div>
""",
                            unsafe_allow_html=True,
                        )

                    with chk_col:
                        st.markdown("<div style='height: 18px;'></div>", unsafe_allow_html=True)
                        check_val = st.checkbox(
                            "Done",
                            value=is_completed,
                            key=f"chk_{b_id}_{interval.index}",
                            help=f"Mark interval #{interval.index} of '{b_topic}' as completed",
                        )
                        if check_val != is_completed:
                            if check_val:
                                st.session_state["session_completed_intervals"].add(interval_uid)
                            else:
                                st.session_state["session_completed_intervals"].discard(interval_uid)
                                st.session_state["session_completed_intervals"].discard(interval.index)
                            st.rerun()

    # -------------------------------------------------------------------------
    # TAB 2: :material/calendar_today: DAILY AGENDA & HISTORY
    # -------------------------------------------------------------------------
    with tab_agenda:
        st.markdown(f"#### :material/calendar_today: Today's Revision Agenda ({formatted_today})")
        st.caption(f"Target Date Stamp: `{today_str}` &bull; Scanned directly from study block registry")

        all_blocks = load_study_blocks()
        hist_ids = st.session_state.get("historical_block_ids", set())
        reinstated_ids = st.session_state.get("manually_reinstated_block_ids", set())
        today_blocks = [
            b for b in all_blocks
            if b.get("session_date") == today_str
            and (not is_block_ended(b) or b.get("id") in reinstated_ids)
            and not b.get("is_historical")
            and b.get("id") not in hist_ids
        ]
        today_blocks = sort_study_blocks_chronologically(today_blocks)

        upcoming_blocks = [
            b for b in all_blocks
            if b.get("session_date") and b.get("session_date") > today_str
            and not b.get("is_historical")
            and b.get("id") not in hist_ids
        ]
        upcoming_blocks = sort_study_blocks_chronologically(upcoming_blocks)

        historical_blocks = [
            b for b in all_blocks
            if (b.get("id") not in reinstated_ids)
            and (
                (b.get("session_date") and b.get("session_date") < today_str)
                or (b.get("session_date") == today_str and is_block_ended(b))
                or b.get("is_historical")
                or b.get("id") in hist_ids
            )
        ]
        historical_blocks = sort_study_blocks_chronologically(historical_blocks)

        if not today_blocks:
            st.markdown(
                f"""
<div class="timeline-card" style="border-left: 4px solid #6366f1;">
    <div style="font-weight: 600; font-size: 0.95rem;">
        <span class="material-symbols-rounded" style="vertical-align: -2px; font-size: 1.1rem; color: #818cf8;">event_busy</span>
        No Active Study Blocks Scheduled for Today
    </div>
    <div style="font-size: 0.82rem; color: rgba(156, 163, 175, 0.9); margin-top: 3px;">
        No active or upcoming study blocks found for timestamp <code>{today_str}</code>. Completed and past sessions have moved to the Historical Study Block Registry below.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            if st.button("Plan a Study Block for Today", icon=":material/add_circle:", type="primary", key="btn_jump_create_today"):
                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                st.session_state["block_editor_mode_selector"] = "__new__"
                st.rerun()
        else:
            st.success(f"Found {len(today_blocks)} active study block(s) for today!", icon=":material/check_circle:")
            
            # Batch actions for Today's agenda
            today_ids = {b.get("id") for b in today_blocks if b.get("id")}
            col_tag_act1, col_tag_act2 = st.columns([2, 3])
            with col_tag_act1:
                all_today_active = today_ids.issubset(active_ids)
                if not all_today_active:
                    if st.button("Activate All Today's Blocks", icon=":material/playlist_play:", key="btn_act_all_today", use_container_width=True):
                        st.session_state["active_study_block_ids"].update(today_ids)
                        st.session_state["manually_reinstated_block_ids"].update(today_ids)
                        st.toast("Activated all today's blocks in Study Timeline!", icon=":material/check:")
                        st.rerun()
                else:
                    if st.button("Deactivate Today's Blocks", icon=":material/remove_circle_outline:", key="btn_deact_all_today", use_container_width=True):
                        st.session_state["active_study_block_ids"].difference_update(today_ids)
                        st.session_state["manually_reinstated_block_ids"].difference_update(today_ids)
                        st.toast("Deactivated today's blocks from Study Timeline", icon=":material/remove_circle_outline:")
                        st.rerun()
            with col_tag_act2:
                if st.button("Go to Study Timeline", icon=":material/timer:", key="btn_jump_timeline_from_agenda"):
                    st.session_state["cockpit_active_tab"] = ":material/timer: Study Timeline"
                    st.rerun()

            for b in today_blocks:
                b_id = b.get("id", "unknown")
                b_topic = b.get("topic", "General")
                b_window = f"{format_time(b.get('start_time'), time_fmt)} - {format_time(b.get('end_time'), time_fmt)}"
                b_dur = b.get("total_duration_minutes", 0)
                b_focus = b.get("total_focus_minutes", b_dur)
                b_break = b.get("total_break_minutes", 0)
                b_intervals_cnt = len(b.get("intervals", []))

                raw_b_notes = b.get("linked_notes")
                if isinstance(raw_b_notes, list):
                    b_notes_clean = [n for n in raw_b_notes if n]
                elif b.get("linked_note"):
                    b_notes_clean = [n.strip() for n in b["linked_note"].split(",") if n.strip()]
                else:
                    b_notes_clean = []

                b_date_hist = b.get("date_history", [])
                hist_badge = f" &bull; :material/history: Rescheduled from {b_date_hist[-1].get('previous_date')}" if b_date_hist else ""

                with st.expander(f":material/data_object: **{b_topic}** ({b_window} &bull; {b_dur} min &bull; {b_intervals_cnt} intervals{hist_badge})", expanded=False):
                    col_info, col_act = st.columns([3.4, 1.6])
                    with col_info:
                        if b_notes_clean:
                            notes_html = "".join([f"<span style='display:inline-block; margin-right:6px; margin-bottom:4px; padding:2px 8px; background:rgba(99,102,241,0.12); border:1px solid rgba(99,102,241,0.3); border-radius:6px; font-size:0.8rem; color:#818cf8;'><code>{n}</code></span>" for n in b_notes_clean])
                            st.markdown(
                                f'<div style="margin-bottom: 8px; font-size: 0.84rem; color: #818cf8;"><span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px;">link</span> Linked Vault Notes ({len(b_notes_clean)}): {notes_html}</div>',
                                unsafe_allow_html=True,
                            )
                        if b_date_hist:
                            hist_entries_html = []
                            for h in b_date_hist:
                                p_date = h.get("previous_date", "Unknown")
                                n_date = h.get("new_date", "Unknown")
                                c_at = h.get("changed_at", "")
                                time_part = f" &bull; <span style='color: rgba(156, 163, 175, 0.8); font-size: 0.75rem;'>{c_at}</span>" if c_at else ""
                                hist_entries_html.append(
                                    f"<div style='font-size: 0.79rem; color: rgba(226, 232, 240, 0.95); margin-top: 3px; display: flex; align-items: center; gap: 6px;'><span class='material-symbols-rounded' style='font-size: 0.85rem; color: #818cf8;'>calendar_month</span><span><code>{p_date}</code> → <code>{n_date}</code>{time_part}</span></div>"
                                )
                            st.markdown(
                                f'<div style="margin-top: 6px; margin-bottom: 8px; padding: 6px 12px; background: rgba(99, 102, 241, 0.08); border-left: 3px solid #818cf8; border-radius: 6px;"><div style="font-size: 0.8rem; font-weight: 600; color: #a5b4fc; display: flex; align-items: center; gap: 5px;"><span class="material-symbols-rounded" style="font-size: 0.95rem;">history</span> Date Change History ({len(b_date_hist)})</div>{"".join(hist_entries_html)}</div>',
                                unsafe_allow_html=True,
                            )
                        st.caption(f"Focus: {b_focus}m | Break: {b_break}m | Created: {b.get('created_at', 'N/A')}")
                    with col_act:
                        is_b_active = b_id in active_ids
                        if is_b_active:
                            if st.button("Active in Timeline", icon=":material/check_circle:", key=f"btn_activate_today_{b_id}", use_container_width=True, help="Click to remove from active timeline"):
                                st.session_state["active_study_block_ids"].discard(b_id)
                                st.session_state["manually_reinstated_block_ids"].discard(b_id)
                                st.toast(f"Removed '{b_topic}' from Study Timeline", icon=":material/visibility_off:")
                                st.rerun()
                        else:
                            if st.button("Activate in Timeline", icon=":material/play_arrow:", key=f"btn_activate_today_{b_id}", use_container_width=True, help="Add to active Study Timeline"):
                                st.session_state["active_study_block_ids"].add(b_id)
                                st.session_state["manually_reinstated_block_ids"].add(b_id)
                                st.toast(f"Activated '{b_topic}' in Study Timeline!", icon=":material/check:")
                                st.rerun()

                        b_sub1, b_sub2 = st.columns(2)
                        with b_sub1:
                            if st.button("Edit", icon=":material/edit:", key=f"btn_edit_today_{b_id}", use_container_width=True):
                                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                                st.session_state["block_editor_mode_selector"] = b_id
                                st.rerun()
                        with b_sub2:
                            if st.button("Delete", icon=":material/delete:", key=f"btn_del_today_{b_id}", use_container_width=True):
                                delete_study_block(b_id)
                                st.session_state.get("active_study_block_ids", set()).discard(b_id)
                                st.session_state.get("manually_reinstated_block_ids", set()).discard(b_id)
                                if st.session_state.get("study_block_data", {}).get("id") == b_id:
                                    st.session_state.pop("study_block_schedule", None)
                                    st.session_state.pop("study_block_data", None)
                                    st.session_state.pop("session_completed_intervals", None)
                                st.session_state["block_editor_mode_selector"] = "__new__"
                                st.toast(f"Deleted study block '{b_topic}'", icon=":material/delete:")
                                st.rerun()

                    st.markdown("**Raw Study Block Data Object (JSON):**")
                    st.json(b, expanded=False)

        # ---------------------------------------------------------------------
        # Section 2: Upcoming Study Blocks (Future Dates > today_str)
        # ---------------------------------------------------------------------
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        col_up_h, col_up_btn = st.columns([3.2, 1.8])
        with col_up_h:
            st.markdown("#### :material/event_upcoming: Upcoming Study Blocks")
            st.caption("Scheduled study sessions for future dates.")
        with col_up_btn:
            if st.button("Schedule Future Block", icon=":material/add_circle:", key="btn_jump_create_upcoming", use_container_width=True):
                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                st.session_state["block_editor_mode_selector"] = "__new__"
                st.rerun()

        if not upcoming_blocks:
            st.info("No upcoming study blocks scheduled. You can plan future sessions using the Study Block Creator or the Study Calendar.", icon=":material/info:")
        else:
            st.success(f"Found {len(upcoming_blocks)} upcoming study block(s) across future dates!", icon=":material/event_available:")
            for u_block in upcoming_blocks:
                u_id = u_block.get("id", "unknown")
                u_topic = u_block.get("topic", "General")
                u_date = format_date(u_block.get("session_date", "Future Date"), date_fmt)
                u_window = f"{format_time(u_block.get('start_time'), time_fmt)} - {format_time(u_block.get('end_time'), time_fmt)}"
                u_dur = u_block.get("total_duration_minutes", 0)
                u_focus = u_block.get("total_focus_minutes", u_dur)
                u_break = u_block.get("total_break_minutes", 0)
                u_intervals_cnt = len(u_block.get("intervals", []))

                raw_u_notes = u_block.get("linked_notes")
                if isinstance(raw_u_notes, list):
                    u_notes_clean = [n for n in raw_u_notes if n]
                elif u_block.get("linked_note"):
                    u_notes_clean = [n.strip() for n in u_block["linked_note"].split(",") if n.strip()]
                else:
                    u_notes_clean = []

                u_date_hist = u_block.get("date_history", [])
                u_hist_badge = f" &bull; :material/history: Rescheduled from {u_date_hist[-1].get('previous_date')}" if u_date_hist else ""

                with st.expander(
                    f":material/event: **{u_date}** &bull; **{u_topic}** ({u_window} &bull; {u_dur} min &bull; {u_intervals_cnt} intervals{u_hist_badge})",
                    expanded=False,
                ):
                    col_info, col_act = st.columns([3.4, 1.6])
                    with col_info:
                        if u_notes_clean:
                            notes_html = "".join([
                                f"<span style='display:inline-block; margin-right:6px; margin-bottom:4px; padding:2px 8px; background:rgba(99,102,241,0.12); border:1px solid rgba(99,102,241,0.3); border-radius:6px; font-size:0.8rem; color:#818cf8;'><code>{n}</code></span>"
                                for n in u_notes_clean
                            ])
                            st.markdown(
                                f'<div style="margin-bottom: 8px; font-size: 0.84rem; color: #818cf8;"><span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px;">link</span> Linked Vault Notes ({len(u_notes_clean)}): {notes_html}</div>',
                                unsafe_allow_html=True,
                            )
                        if u_date_hist:
                            hist_entries_html = []
                            for h in u_date_hist:
                                p_date = h.get("previous_date", "Unknown")
                                n_date = h.get("new_date", "Unknown")
                                c_at = h.get("changed_at", "")
                                time_part = f" &bull; <span style='color: rgba(156, 163, 175, 0.8); font-size: 0.75rem;'>{c_at}</span>" if c_at else ""
                                hist_entries_html.append(
                                    f"<div style='font-size: 0.79rem; color: rgba(226, 232, 240, 0.95); margin-top: 3px; display: flex; align-items: center; gap: 6px;'><span class='material-symbols-rounded' style='font-size: 0.85rem; color: #818cf8;'>calendar_month</span><span><code>{p_date}</code> → <code>{n_date}</code>{time_part}</span></div>"
                                )
                            st.markdown(
                                f'<div style="margin-top: 6px; margin-bottom: 8px; padding: 6px 12px; background: rgba(99, 102, 241, 0.08); border-left: 3px solid #818cf8; border-radius: 6px;"><div style="font-size: 0.8rem; font-weight: 600; color: #a5b4fc; display: flex; align-items: center; gap: 5px;"><span class="material-symbols-rounded" style="font-size: 0.95rem;">history</span> Date Change History ({len(u_date_hist)})</div>{"".join(hist_entries_html)}</div>',
                                unsafe_allow_html=True,
                            )
                        st.caption(f"Scheduled: {u_date} | Focus: {u_focus}m | Break: {u_break}m | Created: {u_block.get('created_at', 'N/A')}")
                    with col_act:
                        is_u_active = u_id in active_ids
                        if is_u_active:
                            if st.button("Active in Timeline", icon=":material/check_circle:", key=f"btn_act_up_{u_id}", use_container_width=True, help="Click to remove from active timeline"):
                                st.session_state["active_study_block_ids"].discard(u_id)
                                st.session_state["manually_reinstated_block_ids"].discard(u_id)
                                st.toast(f"Removed '{u_topic}' from Study Timeline", icon=":material/visibility_off:")
                                st.rerun()
                        else:
                            if st.button("Activate in Timeline", icon=":material/play_arrow:", key=f"btn_act_up_{u_id}", use_container_width=True, help="Activate this study block for today and add to Study Timeline"):
                                activate_historical_study_block(u_id, target_date=today_str)
                                st.session_state["active_study_block_ids"].add(u_id)
                                st.session_state["manually_reinstated_block_ids"].add(u_id)
                                st.toast(f"Moved '{u_topic}' to Today's Agenda ({u_date} → {today_str}) and activated in Study Timeline!", icon=":material/update:")
                                st.rerun()

                        b_sub1, b_sub2 = st.columns(2)
                        with b_sub1:
                            if st.button("Edit", icon=":material/edit:", key=f"btn_edit_up_{u_id}", use_container_width=True):
                                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                                st.session_state["block_editor_mode_selector"] = u_id
                                st.rerun()
                        with b_sub2:
                            if st.button("Delete", icon=":material/delete:", key=f"btn_del_up_{u_id}", use_container_width=True):
                                delete_study_block(u_id)
                                st.session_state.get("active_study_block_ids", set()).discard(u_id)
                                st.session_state.get("manually_reinstated_block_ids", set()).discard(u_id)
                                st.toast(f"Deleted upcoming study block '{u_topic}'", icon=":material/delete:")
                                st.rerun()

                    st.markdown("**Raw Study Block Data Object (JSON):**")
                    st.json(u_block, expanded=False)

        # Historical Session Directory Explorer
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        col_hist_h, col_hist_btn = st.columns([3.2, 1.8])
        with col_hist_h:
            st.markdown("#### :material/history: Historical Study Block Registry")
            st.caption("Archive of completed and past study blocks.")
        with col_hist_btn:
            if historical_blocks:
                if st.button(
                    "Clear Historical Registry",
                    icon=":material/delete_sweep:",
                    key="btn_clear_entire_hist_reg",
                    use_container_width=True,
                    help="Delete all entries in the Historical Study Block Registry (does not affect active blocks)",
                ):
                    hist_ids = [b.get("id") for b in historical_blocks if b.get("id")]
                    cleared_count = clear_study_blocks(hist_ids)
                    st.session_state.get("historical_block_ids", set()).difference_update(hist_ids)
                    st.toast(f"Cleared {cleared_count} historical study block(s) from registry!", icon=":material/delete_sweep:")
                    st.rerun()

        if not historical_blocks:
            st.info("No historical study block data objects in registry.", icon=":material/info:")
        else:
            for s_block in historical_blocks:
                sb_id = s_block.get("id", "unknown")
                sb_topic = s_block.get("topic", "General")
                sb_date = format_date(s_block.get("session_date", "Unknown Date"), date_fmt)
                sb_dur = s_block.get("total_duration_minutes", 0)
                sb_focus = s_block.get("total_focus_minutes", sb_dur)
                sb_break = s_block.get("total_break_minutes", 0)
                sb_window = f"{format_time(s_block.get('start_time'), time_fmt)} - {format_time(s_block.get('end_time'), time_fmt)}"
                sb_intervals_cnt = len(s_block.get("intervals", []))

                raw_sb_notes = s_block.get("linked_notes")
                if isinstance(raw_sb_notes, list):
                    sb_notes_clean = [n for n in raw_sb_notes if n]
                elif s_block.get("linked_note"):
                    sb_notes_clean = [n.strip() for n in s_block["linked_note"].split(",") if n.strip()]
                else:
                    sb_notes_clean = []

                sb_date_hist = s_block.get("date_history", [])
                sb_hist_badge = f" &bull; :material/history: Rescheduled from {sb_date_hist[-1].get('previous_date')}" if sb_date_hist else ""

                # Collapsable container matching Today's Revision Agenda structure exactly
                with st.expander(
                    f":material/data_object: **{sb_topic}** ({sb_date} &bull; {sb_window} &bull; {sb_dur} min &bull; {sb_intervals_cnt} intervals{sb_hist_badge})",
                    expanded=False,
                ):
                    col_info, col_act = st.columns([3.4, 1.6])
                    with col_info:
                        if sb_notes_clean:
                            notes_html = "".join([
                                f"<span style='display:inline-block; margin-right:6px; margin-bottom:4px; padding:2px 8px; background:rgba(99,102,241,0.12); border:1px solid rgba(99,102,241,0.3); border-radius:6px; font-size:0.8rem; color:#818cf8;'><code>{n}</code></span>"
                                for n in sb_notes_clean
                            ])
                            st.markdown(
                                f'<div style="margin-bottom: 8px; font-size: 0.84rem; color: #818cf8;"><span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px;">link</span> Linked Vault Notes ({len(sb_notes_clean)}): {notes_html}</div>',
                                unsafe_allow_html=True,
                            )
                        if sb_date_hist:
                            hist_entries_html = []
                            for h in sb_date_hist:
                                p_date = h.get("previous_date", "Unknown")
                                n_date = h.get("new_date", "Unknown")
                                c_at = h.get("changed_at", "")
                                time_part = f" &bull; <span style='color: rgba(156, 163, 175, 0.8); font-size: 0.75rem;'>{c_at}</span>" if c_at else ""
                                hist_entries_html.append(
                                    f"<div style='font-size: 0.79rem; color: rgba(226, 232, 240, 0.95); margin-top: 3px; display: flex; align-items: center; gap: 6px;'><span class='material-symbols-rounded' style='font-size: 0.85rem; color: #818cf8;'>calendar_month</span><span><code>{p_date}</code> → <code>{n_date}</code>{time_part}</span></div>"
                                )
                            st.markdown(
                                f'<div style="margin-top: 6px; margin-bottom: 8px; padding: 6px 12px; background: rgba(99, 102, 241, 0.08); border-left: 3px solid #818cf8; border-radius: 6px;"><div style="font-size: 0.8rem; font-weight: 600; color: #a5b4fc; display: flex; align-items: center; gap: 5px;"><span class="material-symbols-rounded" style="font-size: 0.95rem;">history</span> Date Change History ({len(sb_date_hist)})</div>{"".join(hist_entries_html)}</div>',
                                unsafe_allow_html=True,
                            )
                        st.caption(f"Date: {sb_date} | Focus: {sb_focus}m | Break: {sb_break}m | Created: {s_block.get('created_at', 'N/A')}")
                    with col_act:
                        is_sb_reinstated = sb_id in active_ids
                        if is_sb_reinstated:
                            if st.button("Active in Timeline", icon=":material/check_circle:", key=f"btn_act_hist_{sb_id}", use_container_width=True, help="Click to remove from active timeline"):
                                mark_study_block_historical(sb_id)
                                st.session_state.setdefault("historical_block_ids", set()).add(sb_id)
                                st.session_state["active_study_block_ids"].discard(sb_id)
                                st.session_state["manually_reinstated_block_ids"].discard(sb_id)
                                st.toast(f"Removed '{sb_topic}' from Study Timeline", icon=":material/visibility_off:")
                                st.rerun()
                        else:
                            if st.button("Activate in Timeline", icon=":material/play_arrow:", key=f"btn_act_hist_{sb_id}", use_container_width=True, help="Activate this study block and move it to Today's Revision Agenda"):
                                updated_b = activate_historical_study_block(sb_id, target_date=today_str)
                                st.session_state["active_study_block_ids"].add(sb_id)
                                st.session_state["manually_reinstated_block_ids"].add(sb_id)
                                st.session_state.get("historical_block_ids", set()).discard(sb_id)
                                old_d = s_block.get("session_date")
                                if old_d and old_d != today_str:
                                    st.toast(f"Moved '{sb_topic}' to Today's Revision Agenda ({old_d} → {today_str}) and activated in Study Timeline!", icon=":material/update:")
                                else:
                                    st.toast(f"Moved '{sb_topic}' to Today's Revision Agenda and activated in Study Timeline!", icon=":material/check:")
                                st.rerun()

                        b_sub1, b_sub2 = st.columns(2)
                        with b_sub1:
                            if st.button("Edit", icon=":material/edit:", key=f"btn_edit_hist_{sb_id}", use_container_width=True):
                                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                                st.session_state["block_editor_mode_selector"] = sb_id
                                st.rerun()
                        with b_sub2:
                            if st.button("Delete", icon=":material/delete:", key=f"btn_del_hist_{sb_id}", use_container_width=True):
                                delete_study_block(sb_id)
                                st.session_state.get("historical_block_ids", set()).discard(sb_id)
                                # CRITICAL: Entries in Historical Registry do NOT connect to Active Study Blocks!
                                # So deleting them does not change active blocks in timeline.
                                st.toast(f"Deleted historical study block '{sb_topic}'", icon=":material/delete:")
                                st.rerun()

                    st.markdown("**Raw Study Block Data Object (JSON):**")
                    st.json(s_block, expanded=False)

    # -------------------------------------------------------------------------
    # TAB 3: :material/edit_calendar: STUDY BLOCK CREATOR & EDITOR
    # -------------------------------------------------------------------------
    with tab_editor:
        st.markdown("#### :material/edit_calendar: Study Block Creator & Editor")
        st.caption("Plan sequential focus and recovery intervals, link multiple vault notes, or edit and delete registered study blocks.")

        all_registered_blocks = load_study_blocks()

        # Operation Mode / Block Selector
        block_selector_options = [("__new__", "Create New Study Block")] + [
            (
                b["id"],
                f"{b.get('topic', 'General')} — {format_date(b.get('session_date', ''), date_fmt)} ({format_time(b.get('start_time', ''), time_fmt)} - {format_time(b.get('end_time', ''), time_fmt)}, {b.get('total_duration_minutes', 0)} min)"
            )
            for b in all_registered_blocks
            if b.get("id")
        ]

        opt_keys = [opt[0] for opt in block_selector_options]

        # One-time router check if an external tab routed here
        if "editor_selected_block_id" in st.session_state:
            routed_id = st.session_state.pop("editor_selected_block_id")
            if routed_id in opt_keys:
                st.session_state["block_editor_mode_selector"] = routed_id

        # Ensure selector state is initialized and valid
        if "block_editor_mode_selector" not in st.session_state or st.session_state["block_editor_mode_selector"] not in opt_keys:
            st.session_state["block_editor_mode_selector"] = opt_keys[0]

        selected_mode_id = st.selectbox(
            ":material/tune: Operation Mode / Select Study Block",
            options=opt_keys,
            format_func=lambda k: dict(block_selector_options).get(k, k),
            key="block_editor_mode_selector",
            help="Select an existing study block to edit or delete, or choose 'Create New Study Block' to configure a fresh session.",
        )

        is_edit_mode = selected_mode_id != "__new__"
        target_block = next((b for b in all_registered_blocks if b.get("id") == selected_mode_id), None) if is_edit_mode else None

        if is_edit_mode and target_block:
            default_topic = target_block.get("topic", "Machine Learning")
            default_start_time = target_block.get("start_time", "14:00")
            default_date_str = target_block.get("session_date", today_str)

            intervals = target_block.get("intervals", [])
            focus_ints = [i for i in intervals if i.get("interval_type") == "focus"]
            break_ints = [i for i in intervals if i.get("interval_type") == "break"]

            default_total_focus = target_block.get("total_focus_minutes") or sum(i.get("duration_minutes", 0) for i in focus_ints) or 90
            default_num_focus = len(focus_ints) or target_block.get("focus_intervals_count") or 3
            default_total_break = target_block.get("total_break_minutes") or sum(i.get("duration_minutes", 0) for i in break_ints) or 30
            default_num_breaks = len(break_ints) if break_ints else target_block.get("break_intervals_count", 2)

            default_duration = int(target_block.get("total_duration_minutes", 120))
            default_focus = int(target_block.get("focus_duration_minutes", 45))
            default_break = int(target_block.get("break_duration_minutes", 15))
            default_alloc_mode = target_block.get("allocation_mode", "Split Minutes into Intervals")

            raw_def_notes = target_block.get("linked_notes")
            if isinstance(raw_def_notes, list):
                default_notes = [n for n in raw_def_notes if n]
            elif target_block.get("linked_note"):
                default_notes = [n.strip() for n in target_block["linked_note"].split(",") if n.strip()]
            else:
                default_notes = []

            mode_banner_text = f"Editing existing study block: **{default_topic}** (`{selected_mode_id}`)"
            st.info(mode_banner_text, icon=":material/edit:")
            tb_date_hist = target_block.get("date_history", [])
            if tb_date_hist:
                hist_lines = " &bull; ".join([f"<code>{h.get('previous_date')}</code> → <code>{h.get('new_date')}</code> ({h.get('changed_at', '')})" for h in tb_date_hist])
                st.caption(f":material/history: **Date Change History:** {hist_lines}")
        else:
            default_topic = "Machine Learning"
            default_start_time = "14:00"
            if "editor_preset_date" in st.session_state:
                default_date_str = st.session_state.pop("editor_preset_date")
                try:
                    st.session_state["editor_date___new__"] = datetime.strptime(default_date_str, "%Y-%m-%d").date()
                except Exception:
                    pass
            else:
                default_date_str = today_str
            default_total_focus = 90
            default_num_focus = 3
            default_total_break = 30
            default_num_breaks = 2
            default_duration = 120
            default_focus = 45
            default_break = 15
            default_alloc_mode = "Split Minutes into Intervals"
            default_notes = []

        c1, c2 = st.columns([1.5, 1.5])
        with c1:
            topic_input = st.text_input(
                ":material/menu_book: Study Topic",
                value=default_topic,
                help="Topic for scheduling and offline Wikipedia grounding",
                key=f"editor_topic_{selected_mode_id}",
            )
            time_c1, time_c2 = st.columns(2)
            with time_c1:
                start_time_input = st.text_input(
                    ":material/schedule: Start Time (24h)",
                    value=default_start_time,
                    help="Start time formatted as HH:MM",
                    key=f"editor_start_{selected_mode_id}",
                )
            with time_c2:
                try:
                    parsed_init_date = datetime.strptime(default_date_str, "%Y-%m-%d").date()
                except Exception:
                    parsed_init_date = datetime.now().date()

                selected_date_val = st.date_input(
                    ":material/calendar_today: Session Date",
                    value=parsed_init_date,
                    key=f"editor_date_{selected_mode_id}",
                    help="Date assigned to this study block",
                )
                target_date_str = selected_date_val.strftime("%Y-%m-%d")

            # Query existing notes from Markdown vault for multi-linking
            vault_notes = list_study_notes()
            vault_note_options = [n.get("relative_path", n["name"]) for n in vault_notes]
            for dn in default_notes:
                if dn not in vault_note_options:
                    vault_note_options.append(dn)

            selected_linked_notes = st.multiselect(
                ":material/link: Linked Vault Notes",
                options=vault_note_options,
                default=default_notes,
                help="Select one or more existing notes from your Markdown vault to link to this study session",
                key=f"editor_multiselect_{selected_mode_id}",
            )
            if selected_linked_notes:
                st.caption(f":material/description: {len(selected_linked_notes)} note(s) linked to this study block.")

        with c2:
            alloc_options = ["Split Minutes into Intervals", "Fixed Block Duration"]
            alloc_idx = alloc_options.index(default_alloc_mode) if default_alloc_mode in alloc_options else 0
            alloc_mode = st.radio(
                ":material/tune: Interval Allocation Method",
                options=alloc_options,
                index=alloc_idx,
                horizontal=True,
                key=f"editor_alloc_mode_{selected_mode_id}",
                help="Split total focus and break minutes into multiple sequential intervals, or use fixed block lengths.",
            )

            if alloc_mode == "Split Minutes into Intervals":
                f_sub1, f_sub2 = st.columns([1.2, 1])
                with f_sub1:
                    focus_total_val = st.number_input(
                        ":material/psychology: Total Focus Minutes",
                        min_value=10,
                        max_value=600,
                        value=int(default_total_focus),
                        step=5,
                        key=f"editor_total_focus_{selected_mode_id}",
                        help="Total deep focus minutes to distribute across intervals",
                    )
                with f_sub2:
                    focus_intervals_val = st.number_input(
                        ":material/splitscreen: Split Focus Into",
                        min_value=1,
                        max_value=12,
                        value=max(1, int(default_num_focus)),
                        step=1,
                        key=f"editor_num_focus_{selected_mode_id}",
                        help="Number of focus intervals to split the total focus minutes into",
                    )

                b_sub1, b_sub2 = st.columns([1.2, 1])
                with b_sub1:
                    break_total_val = st.number_input(
                        ":material/coffee: Total Break Minutes",
                        min_value=0,
                        max_value=180,
                        value=int(default_total_break),
                        step=5,
                        key=f"editor_total_break_{selected_mode_id}",
                        help="Total recovery minutes to distribute across intervals",
                    )
                with b_sub2:
                    break_intervals_val = st.number_input(
                        ":material/splitscreen: Split Breaks Into",
                        min_value=0,
                        max_value=12,
                        value=max(0, int(default_num_breaks)),
                        step=1,
                        key=f"editor_num_break_{selected_mode_id}",
                        help="Number of break intervals to split the total break minutes into (0 for no breaks)",
                    )

                f_splits = split_interval_minutes(focus_total_val, focus_intervals_val)
                b_splits = split_interval_minutes(break_total_val, break_intervals_val) if break_intervals_val > 0 else []
                f_desc = f"{focus_intervals_val} × {f_splits[0]}m" if len(set(f_splits)) <= 1 else f"{focus_intervals_val} blocks (~{focus_total_val//focus_intervals_val}m)"
                b_desc = f"{break_intervals_val} × {b_splits[0]}m" if len(set(b_splits)) <= 1 and b_splits else f"{break_intervals_val} blocks (~{break_total_val//break_intervals_val}m)" if b_splits else "0 blocks (None)"
                tot_calc_mins = focus_total_val + break_total_val

                st.markdown(
                    f"""
<div style="margin-top: 4px; margin-bottom: 12px; padding: 6px 12px; background: rgba(99, 102, 241, 0.08); border: 1px solid rgba(99, 102, 241, 0.25); border-radius: 8px; font-size: 0.82rem; color: #818cf8;">
    <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px;">calculate</span>
    <strong>Total Session: {tot_calc_mins} min</strong> &bull; Focus: {f_desc} ({focus_total_val}m) &bull; Breaks: {b_desc} ({break_total_val}m)
</div>
""",
                    unsafe_allow_html=True,
                )
                duration_val = tot_calc_mins
                focus_val = focus_total_val // focus_intervals_val
                break_val = break_total_val // break_intervals_val if break_intervals_val > 0 else 0
            else:
                duration_val = st.number_input(
                    ":material/timer: Total Duration (minutes)",
                    min_value=15,
                    max_value=480,
                    value=default_duration,
                    step=15,
                    key=f"editor_dur_{selected_mode_id}",
                )
                focus_val = st.number_input(
                    ":material/psychology: Focus Interval (minutes)",
                    min_value=10,
                    max_value=180,
                    value=default_focus,
                    step=5,
                    key=f"editor_focus_{selected_mode_id}",
                )
                break_val = st.number_input(
                    ":material/coffee: Break Interval (minutes)",
                    min_value=0,
                    max_value=60,
                    value=default_break,
                    step=5,
                    key=f"editor_break_{selected_mode_id}",
                )
                focus_total_val = None
                break_total_val = None
                focus_intervals_val = None
                break_intervals_val = None

            if is_edit_mode and target_block:
                btn_update_clicked = st.button(
                    "Update Study Block",
                    icon=":material/save:",
                    type="primary",
                    use_container_width=True,
                    key=f"btn_update_block_{selected_mode_id}",
                )
                ed_c1, ed_c2 = st.columns(2)
                with ed_c1:
                    btn_activate_edit_clicked = st.button(
                        "Activate in Timeline",
                        icon=":material/play_arrow:",
                        use_container_width=True,
                        key=f"btn_activate_edit_{selected_mode_id}",
                    )
                with ed_c2:
                    btn_delete_edit_clicked = st.button(
                        "Delete Study Block",
                        icon=":material/delete:",
                        type="secondary",
                        use_container_width=True,
                        key=f"btn_delete_edit_{selected_mode_id}",
                    )
            else:
                generate_clicked = st.button(
                    "Generate Study Block",
                    icon=":material/add_circle:",
                    type="primary",
                    use_container_width=True,
                    key="btn_gen_study_block",
                )
                btn_update_clicked = False
                btn_activate_edit_clicked = False
                btn_delete_edit_clicked = False

        # Live Interval Sequence Preview
        try:
            if alloc_mode == "Split Minutes into Intervals":
                preview_sched = generate_study_schedule(
                    start_time=start_time_input,
                    topic=topic_input,
                    session_date=target_date_str,
                    total_focus_minutes=focus_total_val,
                    total_break_minutes=break_total_val,
                    num_focus_intervals=focus_intervals_val,
                    num_break_intervals=break_intervals_val,
                )
            else:
                preview_sched = generate_study_schedule(
                    start_time=start_time_input,
                    total_duration_minutes=duration_val,
                    focus_duration_minutes=focus_val,
                    break_duration_minutes=break_val,
                    topic=topic_input,
                    session_date=target_date_str,
                )
        except Exception:
            preview_sched = None

        if preview_sched and preview_sched.intervals:
            with st.expander(
                f":material/preview: Live Interval Breakdown Preview ({len(preview_sched.intervals)} intervals &bull; {preview_sched.total_duration_minutes} min &bull; {format_time(preview_sched.start_time, time_fmt)} - {format_time(preview_sched.end_time, time_fmt)})",
                expanded=True,
            ):
                preview_pills = []
                for item in preview_sched.intervals:
                    is_f = item.interval_type == "focus"
                    bg_col = "rgba(99, 102, 241, 0.12)" if is_f else "rgba(16, 185, 129, 0.12)"
                    border_col = "rgba(99, 102, 241, 0.35)" if is_f else "rgba(16, 185, 129, 0.35)"
                    text_col = "#818cf8" if is_f else "#34d399"
                    icon_name = "psychology" if is_f else "coffee"
                    type_str = "Focus" if is_f else "Break"
                    preview_pills.append(
                        f'<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; background: {bg_col}; border: 1px solid {border_col}; border-radius: 8px; font-size: 0.82rem; color: {text_col}; margin: 3px 4px 3px 0;">'
                        f'<span class="material-symbols-rounded" style="font-size: 0.95rem;">{icon_name}</span>'
                        f'<span><strong>#{item.index} {type_str}</strong> ({item.duration_minutes}m &bull; {format_time(item.start_time, time_fmt)} - {format_time(item.end_time, time_fmt)})</span>'
                        f'</div>'
                    )
                st.markdown(
                    f'<div style="display: flex; flex-wrap: wrap; gap: 4px; padding: 4px 0;">{"".join(preview_pills)}</div>',
                    unsafe_allow_html=True,
                )

        # --- Handle Action Executions ---
        if is_edit_mode and target_block:
            if btn_update_clicked:
                with st.spinner("Recalculating intervals and updating study block..."):
                    try:
                        if alloc_mode == "Split Minutes into Intervals":
                            sched = generate_study_schedule(
                                start_time=start_time_input,
                                topic=topic_input,
                                session_date=target_date_str,
                                total_focus_minutes=focus_total_val,
                                total_break_minutes=break_total_val,
                                num_focus_intervals=focus_intervals_val,
                                num_break_intervals=break_intervals_val,
                            )
                        else:
                            sched = generate_study_schedule(
                                start_time=start_time_input,
                                total_duration_minutes=duration_val,
                                focus_duration_minutes=focus_val,
                                break_duration_minutes=break_val,
                                topic=topic_input,
                                session_date=target_date_str,
                            )

                        block_dict = sched.to_dict()
                        block_dict["id"] = selected_mode_id
                        block_dict["session_date"] = target_date_str
                        block_dict["allocation_mode"] = alloc_mode
                        block_dict["created_at"] = target_block.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                        block_dict["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        block_dict["linked_notes"] = selected_linked_notes
                        block_dict["linked_note"] = ", ".join(selected_linked_notes) if selected_linked_notes else None
                        if "is_historical" in target_block:
                            block_dict["is_historical"] = target_block["is_historical"]
                        if "status" in target_block:
                            block_dict["status"] = target_block["status"]
                        if "date_history" in target_block:
                            block_dict["date_history"] = list(target_block["date_history"])
                        if "manually_reinstated" in target_block:
                            block_dict["manually_reinstated"] = target_block["manually_reinstated"]

                        # Check if session date changed during this manual edit
                        prev_date = target_block.get("session_date")
                        if prev_date and prev_date != target_date_str:
                            if "date_history" not in block_dict or not isinstance(block_dict["date_history"], list):
                                block_dict["date_history"] = []
                            block_dict["date_history"].append({
                                "previous_date": prev_date,
                                "new_date": target_date_str,
                                "changed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "action": "Date modified in Study Block Creator & Editor",
                            })

                        if target_block.get("wiki_summary"):
                            block_dict["wiki_summary"] = target_block["wiki_summary"]
                        else:
                            wiki = cached_get_wikipedia_summary(topic_input, max_paragraphs=1)
                            if wiki:
                                block_dict["wiki_summary"] = wiki

                        save_study_block_json(block_dict, target_date_str)

                        # If currently active block in Timeline was this block, update session state
                        if selected_mode_id in st.session_state.get("active_study_block_ids", set()):
                            if target_date_str != today_str:
                                st.session_state["active_study_block_ids"].discard(selected_mode_id)
                                st.session_state.get("manually_reinstated_block_ids", set()).discard(selected_mode_id)
                                if st.session_state.get("study_block_data", {}).get("id") == selected_mode_id:
                                    st.session_state.pop("study_block_schedule", None)
                                    st.session_state.pop("study_block_data", None)
                            else:
                                if st.session_state.get("study_block_data", {}).get("id") == selected_mode_id:
                                    st.session_state["study_block_schedule"] = sched
                                    st.session_state["study_block_data"] = block_dict

                        st.toast(f"Updated study block '{topic_input}' successfully!", icon=":material/check:")
                        st.success(f"Study block **{topic_input}** updated successfully!", icon=":material/check_circle:")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error updating study block: {e}", icon=":material/error:")

            if btn_activate_edit_clicked:
                if "active_study_block_ids" not in st.session_state:
                    st.session_state["active_study_block_ids"] = set()
                if "manually_reinstated_block_ids" not in st.session_state:
                    st.session_state["manually_reinstated_block_ids"] = set()

                b_date = target_block.get("session_date")
                if b_date and b_date != today_str:
                    target_block = activate_historical_study_block(selected_mode_id, target_date=today_str) or target_block

                st.session_state["active_study_block_ids"].add(selected_mode_id)
                st.session_state["manually_reinstated_block_ids"].add(selected_mode_id)
                st.session_state["study_block_schedule"] = dict_to_study_block_schedule(target_block)
                st.session_state["study_block_data"] = target_block
                st.session_state["cockpit_active_tab"] = ":material/timer: Study Timeline"
                st.toast(f"Activated {target_block.get('topic')} in Study Timeline!", icon=":material/check:")
                st.rerun()

            if btn_delete_edit_clicked:
                deleted = delete_study_block(selected_mode_id)
                if selected_mode_id in st.session_state.get("active_study_block_ids", set()):
                    st.session_state["active_study_block_ids"].discard(selected_mode_id)
                    st.session_state.get("manually_reinstated_block_ids", set()).discard(selected_mode_id)
                    if st.session_state.get("study_block_data", {}).get("id") == selected_mode_id:
                        st.session_state.pop("study_block_schedule", None)
                        st.session_state.pop("study_block_data", None)
                        st.session_state.pop("session_completed_intervals", None)
                st.session_state["editor_selected_block_id"] = "__new__"
                st.toast(f"Deleted study block '{target_block.get('topic')}'", icon=":material/delete:")
                st.rerun()

        elif not is_edit_mode:
            if generate_clicked:
                with st.spinner("Calculating schedule and generating study block data..."):
                    try:
                        if alloc_mode == "Split Minutes into Intervals":
                            sched = generate_study_schedule(
                                start_time=start_time_input,
                                topic=topic_input,
                                session_date=target_date_str,
                                total_focus_minutes=focus_total_val,
                                total_break_minutes=break_total_val,
                                num_focus_intervals=focus_intervals_val,
                                num_break_intervals=break_intervals_val,
                            )
                        else:
                            sched = generate_study_schedule(
                                start_time=start_time_input,
                                total_duration_minutes=duration_val,
                                focus_duration_minutes=focus_val,
                                break_duration_minutes=break_val,
                                topic=topic_input,
                                session_date=target_date_str,
                            )

                        wiki = cached_get_wikipedia_summary(topic_input, max_paragraphs=1)

                        # Construct raw study block data object (JSON-serializable)
                        block_dict = sched.to_dict()
                        block_id = f"block_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        block_dict["id"] = block_id
                        block_dict["session_date"] = target_date_str
                        block_dict["allocation_mode"] = alloc_mode
                        block_dict["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        block_dict["linked_notes"] = selected_linked_notes
                        block_dict["linked_note"] = ", ".join(selected_linked_notes) if selected_linked_notes else None
                        if wiki:
                            block_dict["wiki_summary"] = wiki

                        # Persist raw study block JSON object to registry
                        save_study_block_json(block_dict, target_date_str)

                        # Only current day's blocks are activated in Study Timeline
                        if target_date_str == today_str:
                            if "active_study_block_ids" not in st.session_state:
                                st.session_state["active_study_block_ids"] = set()
                            if "manually_reinstated_block_ids" not in st.session_state:
                                st.session_state["manually_reinstated_block_ids"] = set()
                            st.session_state["active_study_block_ids"].add(block_id)
                            st.session_state["manually_reinstated_block_ids"].add(block_id)

                            st.session_state["study_block_schedule"] = sched
                            st.session_state["study_block_data"] = block_dict
                            st.session_state["study_block_wiki"] = wiki
                            success_msg = f"Study block registered successfully for **{topic_input}**! Active in Study Timeline."
                        else:
                            st.session_state.get("active_study_block_ids", set()).discard(block_id)
                            st.session_state.get("manually_reinstated_block_ids", set()).discard(block_id)
                            success_msg = f"Upcoming study block registered for **{topic_input}** on {target_date_str}! Viewable in Upcoming Blocks & Study Calendar."

                        # Use router key to avoid modifying block_editor_mode_selector widget after instantiation
                        st.session_state["editor_selected_block_id"] = block_id

                        st.toast(f"Study block created for {topic_input}!", icon=":material/check:")
                        st.success(success_msg, icon=":material/check_circle:")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error generating study block: {e}", icon=":material/error:")

        # Display raw JSON data object of currently selected / active study block
        inspected_data = target_block if is_edit_mode else st.session_state.get("study_block_data")
        if inspected_data:
            st.markdown("<div style='margin-top: 20px;'></div>", unsafe_allow_html=True)
            st.markdown("#### :material/data_object: Study Block Data Object (JSON)")
            st.caption("Raw study block data structure registered in the session registry:")
            st.json(inspected_data, expanded=False)

    # -------------------------------------------------------------------------
    # TAB 4: :material/calendar_month: STUDY CALENDAR
    # -------------------------------------------------------------------------
    with tab_calendar:
        st.markdown("#### :material/calendar_month: Study Calendar")
        st.caption("Visual monthly agenda of scheduled, active, and completed study blocks across dates.")

        now_cal = datetime.now()
        if "cal_view_year" not in st.session_state:
            st.session_state["cal_view_year"] = now_cal.year
        if "cal_view_month" not in st.session_state:
            st.session_state["cal_view_month"] = now_cal.month
        if "cal_selected_date_str" not in st.session_state:
            st.session_state["cal_selected_date_str"] = today_str

        cal_year = st.session_state["cal_view_year"]
        cal_month = st.session_state["cal_view_month"]
        month_meta = get_calendar_month_data(cal_year, cal_month)

        all_cal_blocks = load_study_blocks()
        blocks_by_date = get_study_blocks_by_date(all_cal_blocks)

        # Month Navigation Header
        nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([1.2, 3.2, 1.2, 1.4], vertical_alignment="center")
        with nav_col1:
            if st.button("Previous", icon=":material/chevron_left:", key="btn_cal_prev_month", use_container_width=True):
                st.session_state["cal_view_year"] = month_meta["prev_year"]
                st.session_state["cal_view_month"] = month_meta["prev_month"]
                st.rerun()
        with nav_col2:
            st.markdown(
                f'<div style="text-align: center; font-size: 1.25rem; font-weight: 700; color: var(--text-color, #e2e8f0); display: flex; align-items: center; justify-content: center; gap: 8px;"><span class="material-symbols-rounded" style="font-size: 1.4rem; color: #818cf8;">calendar_month</span> {month_meta["month_name"]} {cal_year}</div>',
                unsafe_allow_html=True,
            )
        with nav_col3:
            if st.button("Next", icon=":material/chevron_right:", key="btn_cal_next_month", use_container_width=True):
                st.session_state["cal_view_year"] = month_meta["next_year"]
                st.session_state["cal_view_month"] = month_meta["next_month"]
                st.rerun()
        with nav_col4:
            if st.button("Jump to Today", icon=":material/today:", key="btn_cal_jump_today", use_container_width=True):
                st.session_state["cal_view_year"] = now_cal.year
                st.session_state["cal_view_month"] = now_cal.month
                st.session_state["cal_selected_date_str"] = today_str
                st.rerun()

        # Monthly Metrics Summary Banner
        month_prefix = f"{cal_year:04d}-{cal_month:02d}"
        month_blocks = [b for b in all_cal_blocks if b.get("session_date", "").startswith(month_prefix)]
        total_month_mins = sum(b.get("total_duration_minutes", 0) for b in month_blocks)
        total_month_focus = sum(b.get("total_focus_minutes", b.get("total_duration_minutes", 0)) for b in month_blocks)
        focus_hours_str = f"{total_month_focus // 60}h {total_month_focus % 60}m" if total_month_focus >= 60 else f"{total_month_focus}m"

        st.markdown(
            f"""
<div style="display: flex; gap: 12px; margin-top: 12px; margin-bottom: 16px; flex-wrap: wrap;">
    <div style="background: rgba(99, 102, 241, 0.08); border: 1px solid rgba(99, 102, 241, 0.25); border-radius: 8px; padding: 6px 14px; font-size: 0.82rem; color: #a5b4fc; display: flex; align-items: center; gap: 6px;">
        <span class="material-symbols-rounded" style="font-size: 1rem; color: #818cf8;">event_note</span>
        <span><strong>{len(month_blocks)}</strong> Total Sessions in {month_meta["month_name"]}</span>
    </div>
    <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 8px; padding: 6px 14px; font-size: 0.82rem; color: #6ee7b7; display: flex; align-items: center; gap: 6px;">
        <span class="material-symbols-rounded" style="font-size: 1rem; color: #10b981;">timer</span>
        <span><strong>{focus_hours_str}</strong> Focus Time Planned</span>
    </div>
    <div style="background: rgba(148, 163, 184, 0.08); border: 1px solid rgba(148, 163, 184, 0.2); border-radius: 8px; padding: 6px 14px; font-size: 0.82rem; color: #94a3b8; display: flex; align-items: center; gap: 6px;">
        <span class="material-symbols-rounded" style="font-size: 1rem; color: #818cf8;">today</span>
        <span>Today: <strong>{today_str}</strong></span>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )

        # Visual Month Calendar Grid
        grid_html_parts = [
            '<div class="calendar-view-container">',
            '<div class="calendar-grid-header">',
            '<div class="calendar-weekday-label">Mon</div>',
            '<div class="calendar-weekday-label">Tue</div>',
            '<div class="calendar-weekday-label">Wed</div>',
            '<div class="calendar-weekday-label">Thu</div>',
            '<div class="calendar-weekday-label">Fri</div>',
            '<div class="calendar-weekday-label">Sat</div>',
            '<div class="calendar-weekday-label">Sun</div>',
            '</div>',
            '<div class="calendar-grid-body">',
        ]

        curr_selected = st.session_state.get("cal_selected_date_str", today_str)

        for week in month_meta["weeks"]:
            for day in week:
                if day == 0:
                    grid_html_parts.append('<div class="calendar-day-box is-empty"></div>')
                else:
                    date_key = f"{cal_year:04d}-{cal_month:02d}-{day:02d}"
                    day_blocks = blocks_by_date.get(date_key, [])
                    box_classes = ["calendar-day-box"]
                    if date_key == today_str:
                        box_classes.append("is-today")
                    if date_key == curr_selected:
                        box_classes.append("is-selected")

                    chips_html = []
                    for b in day_blocks[:2]:
                        t_name = b.get("topic", "Study")
                        if len(t_name) > 13:
                            t_name = t_name[:12] + "…"
                        start_t = format_time(b.get("start_time", ""), time_fmt)
                        label_text = f"{start_t} {t_name}" if start_t else t_name

                        if date_key == today_str:
                            c_cls = "calendar-chip calendar-chip-active"
                        elif date_key > today_str:
                            c_cls = "calendar-chip calendar-chip-upcoming"
                        else:
                            c_cls = "calendar-chip calendar-chip-past"

                        chips_html.append(f'<div class="{c_cls}" title="{b.get("topic")} ({b.get("total_duration_minutes")}m)">{label_text}</div>')

                    if len(day_blocks) > 2:
                        extra_count = len(day_blocks) - 2
                        chips_html.append(f'<div class="calendar-chip calendar-chip-past" style="font-size:0.68rem; text-align:center;">+{extra_count} more</div>')

                    chips_rendered = "".join(chips_html)
                    today_dot = ' <span style="display:inline-block; width:5px; height:5px; background:#818cf8; border-radius:50%; margin-left:3px; vertical-align:middle;"></span>' if date_key == today_str else ''

                    grid_html_parts.append(
                        f'<div class="{" ".join(box_classes)}">'
                        f'<div class="calendar-day-header">'
                        f'<span class="calendar-day-number">{day}{today_dot}</span>'
                        f'</div>'
                        f'<div style="overflow:hidden; display:flex; flex-direction:column; gap:2px;">{chips_rendered}</div>'
                        f'</div>'
                    )

        grid_html_parts.append('</div>')
        grid_html_parts.append('</div>')

        st.markdown("".join(grid_html_parts), unsafe_allow_html=True)

        # Day Inspector & Session Details Section
        st.markdown("<div style='margin-top: 18px;'></div>", unsafe_allow_html=True)
        st.markdown("##### :material/date_range: Day Inspector & Session Details")
        st.caption("Select a date from the calendar to inspect its study sessions, activate blocks, or plan new study time.")

        try:
            init_picker_date = datetime.strptime(curr_selected, "%Y-%m-%d").date()
        except Exception:
            init_picker_date = now_cal.date()

        insp_col1, insp_col2 = st.columns([2, 3], vertical_alignment="bottom")
        with insp_col1:
            picked_date = st.date_input(
                ":material/event: Inspect Date",
                value=init_picker_date,
                key="cal_day_inspector_picker",
                help="Choose any date to see scheduled blocks or add a session",
            )
            picked_date_str = picked_date.strftime("%Y-%m-%d")
            if picked_date_str != st.session_state.get("cal_selected_date_str"):
                st.session_state["cal_selected_date_str"] = picked_date_str
                st.session_state["cal_view_year"] = picked_date.year
                st.session_state["cal_view_month"] = picked_date.month
                st.rerun()

        with insp_col2:
            if st.button(
                f"Plan Study Block on {picked_date.strftime('%b %d, %Y')}",
                icon=":material/add_circle:",
                type="primary",
                key=f"btn_cal_plan_for_{picked_date_str}",
                use_container_width=True,
            ):
                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                st.session_state["block_editor_mode_selector"] = "__new__"
                st.session_state["editor_preset_date"] = picked_date_str
                st.rerun()

        # Render Sessions for Selected Date
        sel_day_blocks = blocks_by_date.get(picked_date_str, [])
        cal_active_ids = st.session_state.get("active_study_block_ids", set())

        if not sel_day_blocks:
            st.info(f"No study blocks scheduled for **{picked_date.strftime('%A')}, {format_date(picked_date, date_fmt)}** (`{picked_date_str}`). Use the button above to plan a study session for this day.", icon=":material/info:")
        else:
            st.success(f"Found {len(sel_day_blocks)} study block(s) scheduled for **{picked_date.strftime('%A')}, {format_date(picked_date, date_fmt)}**", icon=":material/event_available:")
            for s_blk in sel_day_blocks:
                b_id = s_blk.get("id", "unknown")
                b_topic = s_blk.get("topic", "General")
                b_window = f"{format_time(s_blk.get('start_time'), time_fmt)} - {format_time(s_blk.get('end_time'), time_fmt)}"
                b_dur = s_blk.get("total_duration_minutes", 0)
                b_focus = s_blk.get("total_focus_minutes", b_dur)
                b_break = s_blk.get("total_break_minutes", 0)
                b_intervals_cnt = len(s_blk.get("intervals", []))

                raw_b_notes = s_blk.get("linked_notes")
                if isinstance(raw_b_notes, list):
                    b_notes_clean = [n for n in raw_b_notes if n]
                elif s_blk.get("linked_note"):
                    b_notes_clean = [n.strip() for n in s_blk["linked_note"].split(",") if n.strip()]
                else:
                    b_notes_clean = []

                b_date_hist = s_blk.get("date_history", [])
                hist_badge = f" &bull; :material/history: Rescheduled from {b_date_hist[-1].get('previous_date')}" if b_date_hist else ""

                with st.expander(
                    f":material/event: **{b_topic}** ({b_window} &bull; {b_dur} min &bull; {b_intervals_cnt} intervals{hist_badge})",
                    expanded=False,
                ):
                    c_info, c_act = st.columns([3.4, 1.6])
                    with c_info:
                        if b_notes_clean:
                            notes_html = "".join([
                                f"<span style='display:inline-block; margin-right:6px; margin-bottom:4px; padding:2px 8px; background:rgba(99,102,241,0.12); border:1px solid rgba(99,102,241,0.3); border-radius:6px; font-size:0.8rem; color:#818cf8;'><code>{n}</code></span>"
                                for n in b_notes_clean
                            ])
                            st.markdown(
                                f'<div style="margin-bottom: 8px; font-size: 0.84rem; color: #818cf8;"><span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px;">link</span> Linked Vault Notes ({len(b_notes_clean)}): {notes_html}</div>',
                                unsafe_allow_html=True,
                            )
                        if b_date_hist:
                            hist_entries_html = []
                            for h in b_date_hist:
                                p_date = h.get("previous_date", "Unknown")
                                n_date = h.get("new_date", "Unknown")
                                c_at = h.get("changed_at", "")
                                time_part = f" &bull; <span style='color: rgba(156, 163, 175, 0.8); font-size: 0.75rem;'>{c_at}</span>" if c_at else ""
                                hist_entries_html.append(
                                    f"<div style='font-size: 0.79rem; color: rgba(226, 232, 240, 0.95); margin-top: 3px; display: flex; align-items: center; gap: 6px;'><span class='material-symbols-rounded' style='font-size: 0.85rem; color: #818cf8;'>calendar_month</span><span><code>{p_date}</code> → <code>{n_date}</code>{time_part}</span></div>"
                                )
                            st.markdown(
                                f'<div style="margin-top: 6px; margin-bottom: 8px; padding: 6px 12px; background: rgba(99, 102, 241, 0.08); border-left: 3px solid #818cf8; border-radius: 6px;"><div style="font-size: 0.8rem; font-weight: 600; color: #a5b4fc; display: flex; align-items: center; gap: 5px;"><span class="material-symbols-rounded" style="font-size: 0.95rem;">history</span> Date Change History ({len(b_date_hist)})</div>{"".join(hist_entries_html)}</div>',
                                unsafe_allow_html=True,
                            )
                        st.caption(f"Session Date: {picked_date_str} | Focus: {b_focus}m | Break: {b_break}m | Created: {s_blk.get('created_at', 'N/A')}")
                    with c_act:
                        is_cal_act = b_id in cal_active_ids
                        if is_cal_act:
                            if st.button("Active in Timeline", icon=":material/check_circle:", key=f"btn_cal_act_{b_id}", use_container_width=True, help="Click to remove from active timeline"):
                                st.session_state["active_study_block_ids"].discard(b_id)
                                st.session_state["manually_reinstated_block_ids"].discard(b_id)
                                st.toast(f"Removed '{b_topic}' from Study Timeline", icon=":material/visibility_off:")
                                st.rerun()
                        else:
                            if st.button("Activate in Timeline", icon=":material/play_arrow:", key=f"btn_cal_act_{b_id}", use_container_width=True, help="Activate this study block and add to Study Timeline"):
                                activate_historical_study_block(b_id, target_date=today_str)
                                st.session_state["active_study_block_ids"].add(b_id)
                                st.session_state["manually_reinstated_block_ids"].add(b_id)
                                if picked_date_str != today_str:
                                    st.toast(f"Moved '{b_topic}' to Today's Agenda ({picked_date_str} → {today_str}) and activated in Study Timeline!", icon=":material/update:")
                                else:
                                    st.toast(f"Activated '{b_topic}' in Study Timeline!", icon=":material/check:")
                                st.rerun()

                        sub1, sub2 = st.columns(2)
                        with sub1:
                            if st.button("Edit", icon=":material/edit:", key=f"btn_cal_edit_{b_id}", use_container_width=True):
                                st.session_state["cockpit_active_tab"] = ":material/edit_calendar: Study Block Creator & Editor"
                                st.session_state["block_editor_mode_selector"] = b_id
                                st.rerun()
                        with sub2:
                            if st.button("Delete", icon=":material/delete:", key=f"btn_cal_del_{b_id}", use_container_width=True):
                                delete_study_block(b_id)
                                st.session_state.get("active_study_block_ids", set()).discard(b_id)
                                st.session_state.get("manually_reinstated_block_ids", set()).discard(b_id)
                                st.toast(f"Deleted study block '{b_topic}'", icon=":material/delete:")
                                st.rerun()

                    st.markdown("**Raw Study Block Data Object (JSON):**")
                    st.json(s_blk, expanded=False)

# ==============================================================================
# VIEW 3: AI TUTOR (Intelligent Tutor, Dual Search & Sufficiency)
# ==============================================================================
elif active_tab == "AI Tutor":
    # Minimalist Workspace Header
    st.markdown(
        f"""
<div class="minimal-hero-card">
    <div class="page-heading-row">
        <div>
            <h2 class="minimal-hero-title"><span class="material-symbols-rounded" aria-hidden="true">smart_toy</span> AI Tutor</h2>
            <p class="minimal-hero-sub">
                Ask your local model questions and search your notes
            </p>
        </div>
        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span class="badge-diff-1">{"Wikipedia enabled" if wiki_available else "Local model and notes" if tutor_notes_enabled else "Local model only"}</span>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    tab_chat, tab_search, tab_coverage = st.tabs([
        ":material/chat: AI Tutor Conversation",
        ":material/search: Knowledge Search",
        ":material/analytics: Notes Coverage & Sufficiency Evaluator",
    ], key="study_buddy_tabs")

    # -------------------------------------------------------------------------
    # TAB 1: :material/chat: AI TUTOR CONVERSATION
    # -------------------------------------------------------------------------
    with tab_chat:
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Toolbar Row
        tb_col1, tb_col2 = st.columns([3.5, 1.2])
        with tb_col2:
            if st.button("Clear Chat History", icon=":material/delete:", key="btn_clear_chat", use_container_width=True):
                st.session_state.messages = []
                st.rerun()

        # Display Message History inside a dedicated container above chat input
        chat_container = st.container()
        with chat_container:
            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        # Chat Input & Prompt Handling
        chat_prompt = st.chat_input("Ask a study question, request a derivation, or query your notes...")

        if chat_prompt:
            active_query = chat_prompt
            with chat_container:
                st.chat_message("user").markdown(active_query)
                st.session_state.messages.append({"role": "user", "content": active_query})

                with st.chat_message("assistant"):
                    personal_context = ""
                    wiki_context = ""
                    clean_query = extract_search_keywords(active_query)

                    if tutor_notes_enabled:
                        with st.spinner(":material/search: Searching local Markdown notes..."):
                            local_hits = search_markdown_notes(active_query, limit=3)
                            if local_hits:
                                for hit in local_hits:
                                    personal_context += (
                                        f"\n[Course: {hit.get('course', 'General')} | Topic: {hit['topic']} | Note: {hit['concept_name']}]\n"
                                        f"{hit['content']}\n"
                                    )

                    if wiki_available:
                        sufficient = personal_context and evaluate_notes_sufficiency(active_query, personal_context)
                        if not sufficient:
                            with st.spinner("Searching offline Wikipedia..."):
                                reference = cached_get_wikipedia_summary(clean_query, max_paragraphs=3)
                                wiki_context = reference.get("summary", "")

                    sources = []
                    if personal_context:
                        sources.append("Markdown notes")
                    if wiki_context:
                        sources.append("offline Wikipedia")
                    source_tag = "**Context:** " + (", ".join(sources) or "Local model knowledge") + "\n\n"

                    with st.spinner("Generating reply..."):
                        synthesis_prompt = f"""You are an expert academic tutor. Answer the student directly.
- Preserve any LaTeX math equations accurately using $...$ or $$...$$.
- Match the user's requested style and structure.
- Use supplied context only if it directly helps answer the question.

Personal Notes:
{personal_context if personal_context else "None"}

Wikipedia Context:
{wiki_context if wiki_context else "None"}

Question:
{active_query}"""
                        raw_answer = ask_ollama(None, synthesis_prompt)
                        final_answer = raw_answer if raw_answer.startswith("Ollama connection error:") else source_tag + raw_answer

                    st.markdown(final_answer)
                    st.session_state.messages.append({"role": "assistant", "content": final_answer})

            st.rerun()

    # -------------------------------------------------------------------------
    # TAB 2: :material/search: DUAL-SOURCE KNOWLEDGE SEARCH
    # -------------------------------------------------------------------------
    with tab_search:
        st.markdown("#### :material/search: Knowledge Search Engine")
        st.markdown(
            "Search your Markdown notes and offline Wikipedia side by side."
            if wiki_available else "Search your Markdown notes. Wikipedia is optional in Settings ? Local AI & Wikipedia."
        )

        search_kw_input = st.text_input(
            "Search Academic Knowledge Base",
            placeholder="e.g. quantum entanglement, loss functions, eigenvalues, neural networks...",
            key="dual_search_input",
        ).strip()

        if search_kw_input:
            clean_search_kw = extract_search_keywords(search_kw_input)
            if wiki_available:
                col_notes_res, col_wiki_res = st.columns(2)
            else:
                col_notes_res = st.container()

            with col_notes_res:
                st.markdown("##### :material/folder: Personal Markdown Notes")
                with st.spinner("Scanning notes vault..."):
                    hits = search_markdown_notes(search_kw_input, limit=4)

                if not hits:
                    st.info(f"No notes in your vault match '{search_kw_input}'.")
                else:
                    st.success(f"Found {len(hits)} matching note(s) in personal vault.")
                    for hit in hits:
                        subj_name = hit.get("course", "General")
                        note_title = hit.get("concept_name", hit.get("topic", "Note"))
                        content_snip = hit.get("content", "")[:260] + "..." if len(hit.get("content", "")) > 260 else hit.get("content", "")

                        st.markdown(
                            f"""
<div class="search-card-notes">
    <div style="font-size: 0.75rem; color: rgba(156, 163, 175, 0.9); margin-bottom: 2px;">
        <span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 4px;">folder</span><strong>{subj_name}</strong>
    </div>
    <div style="font-weight: 700; font-size: 1.0rem; color: var(--text-color, inherit);">
        <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px;">description</span>{note_title}
    </div>
    <div style="font-size: 0.82rem; color: rgba(156, 163, 175, 0.95); margin-top: 5px; line-height: 1.4;">
        {content_snip}
    </div>
</div>
""",
                            unsafe_allow_html=True,
                        )

            wiki_lead = {}
            if wiki_available:
                with col_wiki_res:
                    st.markdown("##### :material/public: Offline Wikipedia")
                    with st.spinner("Searching offline Wikipedia..."):
                        wiki_lead = cached_get_wikipedia_summary(clean_search_kw, max_paragraphs=2)

                    if not wiki_lead:
                        st.info(f"No Wikipedia article found for '{clean_search_kw}'.")
                    else:
                        st.success(f"Verified article: {wiki_lead.get('title')}")
                        st.markdown(
                            f"""
    <div class="search-card-wiki">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-weight: 700; font-size: 1.0rem; color: var(--text-color, inherit);">
                <span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px;">public</span>{wiki_lead.get('title')}
            </span>
            <span class="badge-diff-1">● Offline Kiwix ZIM</span>
        </div>
        <div style="font-size: 0.84rem; line-height: 1.45; color: var(--text-color, inherit);">
            {wiki_lead.get('summary')}
        </div>
    </div>
    """,
                            unsafe_allow_html=True,
                        )

                # Comparative Coverage Analysis
                st.markdown("---")
            if hits and wiki_lead:
                st.markdown(
                    """
<div class="coverage-card-pass">
    <strong><span class="material-symbols-rounded" style="font-size: 1rem; vertical-align: -2px; margin-right: 4px; color: #10b981;">check_circle</span>Complete Dual Coverage:</strong> This concept exists both in your personal lecture notes 
    and in the offline Wikipedia encyclopedia. You have ample material for review!
</div>
""",
                    unsafe_allow_html=True,
                )
            elif not hits and wiki_lead:
                st.markdown(
                    f"""
<div class="coverage-card-gap">
    <strong><span class="material-symbols-rounded" style="font-size: 1rem; vertical-align: -2px; margin-right: 4px; color: #f59e0b;">warning</span>Knowledge Gap Detected:</strong> Concept <em>"{search_kw_input}"</em> was found in offline Wikipedia 
    but does <strong>not exist</strong> in your personal course notes yet.
</div>
""",
                    unsafe_allow_html=True,
                )
                if st.button("Draft Note for this Concept in Authoring Studio", icon=":material/edit_note:", key="btn_draft_gap_note", type="primary"):
                    gap_title = wiki_lead.get("title", search_kw_input)
                    gap_content = f"# {wiki_lead.get('title')}\n\n## Overview\n{wiki_lead.get('summary')}\n\n## Detailed Notes\n"
                    st.session_state["author_subject"] = "General"
                    st.session_state["author_title"] = gap_title
                    st.session_state["author_content"] = gap_content
                    st.session_state["author_pending_subject"] = "General"
                    st.session_state["author_pending_title"] = gap_title
                    st.session_state["author_pending_content"] = gap_content
                    st.session_state["active_tab"] = "Notes Vault"
                    st.session_state["notes_workspace_tab"] = ":material/edit_note: Note Authoring Studio"
                    st.toast("Draft transferred to Authoring Studio!", icon=":material/check:")
                    st.rerun()

    # -------------------------------------------------------------------------
    # TAB 3: :material/analytics: NOTES COVERAGE & SUFFICIENCY EVALUATOR
    # -------------------------------------------------------------------------
    with tab_coverage:
        st.markdown("#### :material/analytics: Notes Coverage & Sufficiency Evaluator")
        st.markdown(
            "Test an upcoming exam question or syllabus learning objective against your personal notes. "
            "Your local model will evaluate if your vault has sufficient theoretical depth to answer it."
        )

        eval_query = st.text_input(
            "Exam Question or Learning Objective to Evaluate",
            placeholder="e.g. How does backpropagation calculate gradients via the chain rule in matrix calculus?",
            key="evaluator_query_input",
        ).strip()

        if st.button("Run Vault Sufficiency Evaluation", icon=":material/search:", type="primary", key="btn_run_evaluator"):
            if not eval_query:
                st.error("Please enter a question or learning objective.")
            else:
                with st.spinner("Checking notes coverage..."):
                    local_hits = search_markdown_notes(eval_query, limit=4)
                    retrieved_notes_text = ""
                    if local_hits:
                        for h in local_hits:
                            retrieved_notes_text += f"\nNote: {h['concept_name']} ({h.get('course', 'General')}):\n{h['content']}\n"

                    is_sufficient = evaluate_notes_sufficiency(eval_query, retrieved_notes_text)

                if is_sufficient:
                    st.markdown(
                        f"""
<div class="coverage-card-pass">
    <div style="font-weight: 700; font-size: 1.05rem; margin-bottom: 4px;">
        <span class="material-symbols-rounded" style="vertical-align: -2px; margin-right: 4px; color: #10b981;">check_circle</span>HIGH VAULT SUFFICIENCY VERIFIED
    </div>
    <div style="font-size: 0.88rem; line-height: 1.45;">
        Your personal notes directly provide sufficient theoretical concepts and derivations 
        to answer this exam objective!
    </div>
</div>
""",
                        unsafe_allow_html=True,
                    )
                    with st.expander(":material/description: View Retrieved Notes Context Used for Verification"):
                        st.markdown(retrieved_notes_text)
                else:
                    st.markdown(
                        f"""
<div class="coverage-card-gap">
    <div style="font-weight: 700; font-size: 1.05rem; margin-bottom: 4px;">
        <span class="material-symbols-rounded" style="vertical-align: -2px; margin-right: 4px; color: #f59e0b;">warning</span>KNOWLEDGE GAP DETECTED
    </div>
    <div style="font-size: 0.88rem; line-height: 1.45;">
        Your personal course notes currently <strong>do not sufficiently answer</strong> this objective. 
        Authoring a new note or enhancing an existing one is recommended prior to exams.
    </div>
</div>
""",
                        unsafe_allow_html=True,
                    )
                    clean_q = extract_search_keywords(eval_query)
                    wiki_ref = cached_get_wikipedia_summary(clean_q, max_paragraphs=1)
                    if wiki_ref:
                        st.markdown(
                            f"""
<div class="ai-grounding-banner" style="margin-top: 12px;">
    <strong><span class="material-symbols-rounded" style="font-size: 0.95rem; vertical-align: -2px; margin-right: 4px;">public</span>Reference Guide from Offline Wikipedia:</strong> <em>{wiki_ref.get('title')}</em><br>
    {wiki_ref.get('summary')}
</div>
""",
                            unsafe_allow_html=True,
                        )

                    if st.button("Author Note in Studio to Fill this Gap", icon=":material/edit_note:", key="btn_fill_gap_studio", type="primary"):
                        fill_title = eval_query[:60]
                        fill_content = f"# {eval_query}\n\n## Objective\n{eval_query}\n\n## Theoretical Derivations\n"
                        st.session_state["author_subject"] = "General"
                        st.session_state["author_title"] = fill_title
                        st.session_state["author_content"] = fill_content
                        st.session_state["author_pending_subject"] = "General"
                        st.session_state["author_pending_title"] = fill_title
                        st.session_state["author_pending_content"] = fill_content
                        st.session_state["active_tab"] = "Notes Vault"
                        st.session_state["notes_workspace_tab"] = ":material/edit_note: Note Authoring Studio"
                        st.toast("Ready in Authoring Studio!", icon=":material/check:")
                        st.rerun()

# ==============================================================================
# VIEW 2: NOTES VAULT (Subject Trees, Professional Studio & AI Engine)
# ==============================================================================
elif active_tab == "Notes Vault":
    # -------------------------------------------------------------------------
    # State Management Initialization
    # -------------------------------------------------------------------------
    if "notes_nav_mode" not in st.session_state:
        st.session_state["notes_nav_mode"] = ":material/folder_open: Subject Vault & Tree Explorer"
    if "selected_note_path" not in st.session_state:
        st.session_state["selected_note_path"] = None
    if "author_subject" not in st.session_state:
        st.session_state["author_subject"] = "Machine Learning"
    if "author_title" not in st.session_state:
        st.session_state["author_title"] = ""
    if "author_content" not in st.session_state:
        st.session_state["author_content"] = ""
    if "author_difficulty" not in st.session_state:
        st.session_state["author_difficulty"] = 3
    if "author_exam_date" not in st.session_state:
        st.session_state["author_exam_date"] = None
    if "author_tags" not in st.session_state:
        st.session_state["author_tags"] = ""
    if "author_editor_text" not in st.session_state:
        st.session_state["author_editor_text"] = st.session_state.get("author_content", "")
    if "author_pending_content" not in st.session_state:
        st.session_state["author_pending_content"] = None
    if "author_pending_title" not in st.session_state:
        st.session_state["author_pending_title"] = None
    if "author_pending_tags" not in st.session_state:
        st.session_state["author_pending_tags"] = None
    if "author_pending_subject" not in st.session_state:
        st.session_state["author_pending_subject"] = None
    if "ai_generated_result" not in st.session_state:
        st.session_state["ai_generated_result"] = None
    if "ai_enhance_result" not in st.session_state:
        st.session_state["ai_enhance_result"] = None
    if "vault_collapsed_folders" not in st.session_state:
        st.session_state["vault_collapsed_folders"] = set()

    # Read the active vault so changes made outside Streamlit appear on rerun.
    vault_tree = get_subject_folder_tree(storage_paths["notes_vault_dir"])
    total_subjects = len(vault_tree)
    total_notes = sum(len(notes) for notes in vault_tree.values())
    total_math_notes = sum(1 for notes in vault_tree.values() for n in notes if n.get("has_math"))

    # Drop selections from files that have been moved or deleted.
    note_subjects = {note["path"]: subject for subject, notes in vault_tree.items() for note in notes}
    selected_note = st.session_state["selected_note_path"]
    if selected_note not in note_subjects:
        selected_note = next(iter(note_subjects), None)
        st.session_state["selected_note_path"] = selected_note
    if selected_note:
        st.session_state["vault_collapsed_folders"].discard(note_subjects[selected_note])

    # -------------------------------------------------------------------------
    # Minimalist Workspace Header
    # -------------------------------------------------------------------------
    st.markdown(
        f"""
<div class="minimal-hero-card">
    <div class="page-heading-row">
        <div>
            <h2 class="minimal-hero-title"><span class="material-symbols-rounded" aria-hidden="true">folder_copy</span> Notes vault</h2>
            <p class="minimal-hero-sub">
                Browse notes by subject and write or revise them with LaTeX and local AI
            </p>
        </div>
        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">folder</span>{total_subjects} Subjects</span>
            <span class="badge-diff-1"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">description</span>{total_notes} Notes ({total_math_notes} LaTeX)</span>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    valid_notes_tabs = [
        ":material/folder_open: Subject Vault & Tree Explorer",
        ":material/edit_note: Note Authoring Studio",
        ":material/smart_toy: AI Note Authoring",
        ":material/drive_file_move: Folder Tree Manager",
    ]
    notes_tab_default = st.session_state.get("notes_workspace_tab", valid_notes_tabs[0])
    if notes_tab_default not in valid_notes_tabs:
        legacy_notes_tab_map = {
            "📂 Subject Vault & Tree Explorer": valid_notes_tabs[0],
            "Subject Vault & Tree Explorer": valid_notes_tabs[0],
            "✍️ Note Authoring Studio": valid_notes_tabs[1],
            "Note Authoring Studio": valid_notes_tabs[1],
            "🤖 AI Note Authoring": valid_notes_tabs[2],
            "AI Note Authoring": valid_notes_tabs[2],
            "📁 Folder Tree Manager": valid_notes_tabs[3],
            "Folder Tree Manager": valid_notes_tabs[3],
        }
        notes_tab_default = legacy_notes_tab_map.get(notes_tab_default, valid_notes_tabs[0])
        st.session_state["notes_workspace_tab"] = notes_tab_default

    tab_vault, tab_author, tab_ai, tab_manager = st.tabs(
        valid_notes_tabs,
        default=notes_tab_default,
        key="course_notes_workspace_tabs",
    )

    # =========================================================================
    # PANEL 1: :material/folder_open: SUBJECT VAULT & TREE EXPLORER
    # =========================================================================
    with tab_vault:
        col_tree, col_viewer = st.columns([1.15, 2.15])

        # --- Left Column: Streamlined Single Navigation Tree ---
        with col_tree:
            # Marker div for CSS targeting of left-column tree elements
            st.markdown('<div class="vault-tree-marker"></div>', unsafe_allow_html=True)
            st.markdown("### :material/folder_open: Vault Navigation")

            # Search & Filter controls strictly reserved for navigation
            search_query = st.text_input(
                "Search Vault",
                placeholder="Title, tag, concept, or formula...",
                label_visibility="collapsed",
                icon=":material/search:",
                key="vault_tree_search",
            ).strip().lower()

            subject_list = ["All Subjects"] + list(vault_tree.keys())
            filter_subject = st.selectbox(
                "Filter Subject",
                subject_list,
                label_visibility="collapsed",
                key="vault_tree_subj_filter",
            )

            # Streamlined Single Navigation Tree Container
            with st.container(border=True):
                st.markdown(
                    f"""
<div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; border-bottom: 1px solid rgba(128,128,128,0.18); margin-bottom: 8px;">
    <span style="font-size: 0.76rem; font-weight: 700; color: rgba(156, 163, 175, 0.95); display: inline-flex; align-items: center; gap: 4px;">
        <span class="material-symbols-rounded" style="font-size: 0.9rem; vertical-align: -1px;">folder_open</span> DIRECTORY TREE
    </span>
    <span style="font-size: 0.70rem; color: rgba(156, 163, 175, 0.75);">
        {total_notes} notes · {total_subjects} subjects
    </span>
</div>
""",
                    unsafe_allow_html=True,
                )

                if not vault_tree:
                    st.info("No subject folders found. Navigate to the Note Authoring Studio tab to create your first note!", icon=":material/info:")
                else:
                    has_matches = False
                    for subj_name, notes_in_subj in vault_tree.items():
                        if filter_subject != "All Subjects" and subj_name != filter_subject:
                            continue

                        # Filter notes by search query if provided
                        if search_query:
                            matched_notes = [
                                n for n in notes_in_subj
                                if search_query in n["title"].lower()
                                or search_query in n["name"].lower()
                                or any(search_query in t.lower() for t in n.get("tags", []))
                            ]
                            if not matched_notes:
                                continue
                        else:
                            matched_notes = notes_in_subj

                        has_matches = True
                        is_collapsed = (subj_name in st.session_state["vault_collapsed_folders"]) and not search_query
                        folder_glyph = "▶" if is_collapsed else "▼"
                        folder_icon = ":material/folder:" if is_collapsed else ":material/folder_open:"
                        folder_label = f"{folder_glyph} {subj_name}  ({len(matched_notes)})"

                        if st.button(folder_label, icon=folder_icon, key=f"tree_fold_{subj_name}", type="tertiary", use_container_width=True):
                            if subj_name in st.session_state["vault_collapsed_folders"]:
                                st.session_state["vault_collapsed_folders"].remove(subj_name)
                            else:
                                st.session_state["vault_collapsed_folders"].add(subj_name)
                            st.rerun()

                        if not is_collapsed:
                            if not matched_notes:
                                st.caption("  └─ *(Empty subject)*")
                            else:
                                for idx, note in enumerate(matched_notes):
                                    is_active = (st.session_state.get("selected_note_path") == note["path"])
                                    is_last = (idx == len(matched_notes) - 1)
                                    branch = "└─ " if is_last else "├─ "

                                    active_marker = "▶ " if is_active else ""
                                    diff_badge = f"L{note.get('difficulty', 3)}"
                                    btn_label = f"  {branch}{active_marker}{note['title']}  ({diff_badge})"
                                    note_icon = ":material/description:"

                                    if st.button(
                                        btn_label,
                                        icon=note_icon,
                                        key=f"tree_btn_{note['path']}",
                                        use_container_width=True,
                                        type="primary" if is_active else "secondary",
                                    ):
                                        st.session_state["selected_note_path"] = note["path"]
                                        st.rerun()

                    if search_query and not has_matches:
                        st.caption(f"No notes match '{search_query}'.")

        # --- Right Column: Note Viewer & Document Studio ---
        with col_viewer:
            active_path = st.session_state.get("selected_note_path")

            if not active_path or not os.path.exists(active_path):
                st.info("Select a course note from the subject folder tree to read or edit.", icon=":material/arrow_back:")
            else:
                try:
                    note_raw_text = load_study_note(active_path)
                    meta = parse_note_metadata(note_raw_text, fallback_filename=os.path.basename(active_path))
                except Exception as e:
                    st.error(f"Error loading note: {e}")
                    meta = None

                if meta:
                    rel_path = os.path.relpath(active_path, storage_paths["notes_vault_dir"])
                    word_count = len(meta["raw_body"].split())
                    read_time = max(1, round(word_count / 180))

                    diff_level = meta.get("difficulty", 3)
                    diff_badge_class = f"badge-diff-{diff_level}"

                    # Calculate exam countdown
                    exam_badge_str = ""
                    if meta.get("exam_date"):
                        try:
                            ex_dt = datetime.strptime(str(meta["exam_date"]), "%Y-%m-%d")
                            days_diff = (ex_dt.date() - today_dt.date()).days
                            if days_diff >= 0:
                                exam_badge_str = f'<span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">event</span>Exam: {meta["exam_date"]} ({days_diff} days left)'
                            else:
                                exam_badge_str = f'<span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">event</span>Exam: {meta["exam_date"]} (Past)'
                        except Exception:
                            exam_badge_str = f'<span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">event</span>Exam: {meta["exam_date"]}'

                    tags_html = "".join([f'<span class="badge-tag">#{t}</span> ' for t in meta.get("tags", [])])

                    # Build badges list dynamically to prevent empty lines or whitespace indentation traps
                    header_badges = [f'<span class="{diff_badge_class}"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">local_fire_department</span>Level {diff_level}/5</span>']
                    has_math = meta.get("has_math") if "has_math" in meta else bool(re.search(r"\$\$|\$[^$]+\$|\\frac|\\int|\\sum|\\begin\{|\\hat|\\mathbf|\\mathbb", note_raw_text))
                    if has_math:
                        header_badges.append('<span class="badge-latex"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">functions</span>LaTeX</span>')
                    if exam_badge_str:
                        header_badges.append(f'<span class="badge-exam">{exam_badge_str}</span>')
                    header_badges.append(f'<span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">schedule</span>~{read_time} min read ({word_count} words)</span>')
                    badges_html = " ".join(header_badges)

                    tags_div = f'<div style="margin-top: 8px;">{tags_html}</div>' if tags_html else ""

                    # Document Header Card (Unindented to prevent CommonMark from treating tags as code blocks)
                    doc_header_html = (
                        f'<div class="doc-header-card">\n'
                        f'<div style="font-size: 0.76rem; color: rgba(156, 163, 175, 0.9); margin-bottom: 4px;">'
                        f'<span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">folder</span><strong>{meta.get("subject", "General")}</strong> &rsaquo; <span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">description</span><code>{os.path.basename(active_path)}</code>'
                        f'</div>\n'
                        f'<div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 8px;">\n'
                        f'<h2 style="margin: 0; font-size: 1.55rem; font-weight: 700; color: var(--text-color, inherit);">'
                        f'{meta.get("title", "Untitled Note")}'
                        f'</h2>\n'
                        f'<div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">{badges_html}</div>\n'
                        f'</div>'
                        + (f'\n{tags_div}' if tags_div else '') +
                        '\n</div>'
                    )
                    st.markdown(doc_header_html, unsafe_allow_html=True)

                    # Action Toolbar
                    st.markdown('<div class="note-viewer-toolbar"></div>', unsafe_allow_html=True)
                    act_c1, act_c2, act_c3, act_c4 = st.columns([1.3, 1.3, 1.2, 1.2])

                    with act_c1:
                        if st.button("Load into Studio", icon=":material/edit_note:", key="btn_edit_studio", use_container_width=True):
                            r_body = meta.get("raw_body", "")
                            st.session_state["author_subject"] = meta.get("subject", "General")
                            st.session_state["author_title"] = meta.get("title", "")
                            st.session_state["author_content"] = r_body
                            st.session_state["author_pending_subject"] = meta.get("subject", "General")
                            st.session_state["author_pending_title"] = meta.get("title", "")
                            st.session_state["author_pending_content"] = r_body
                            st.session_state["author_difficulty"] = meta.get("difficulty", 3)
                            st.session_state["author_tags"] = ", ".join(meta.get("tags", []))
                            st.session_state["author_pending_tags"] = ", ".join(meta.get("tags", []))
                            if meta.get("exam_date"):
                                try:
                                    st.session_state["author_exam_date"] = datetime.strptime(str(meta["exam_date"]), "%Y-%m-%d").date()
                                except Exception:
                                    st.session_state["author_exam_date"] = None
                            st.session_state["notes_workspace_tab"] = ":material/edit_note: Note Authoring Studio"
                            st.toast("Note loaded into Note Authoring Studio!", icon=":material/check:")
                            st.info("Note content loaded. Navigate to the Note Authoring Studio tab above to edit.", icon=":material/info:")

                    with act_c2:
                        if st.button("Send to AI Enhancer", icon=":material/smart_toy:", key="btn_ai_enhance_jump", use_container_width=True):
                            st.session_state["ai_selected_note_path"] = active_path
                            st.session_state["notes_workspace_tab"] = ":material/smart_toy: AI Note Authoring"
                            st.toast("Note selected for AI Enhancement!", icon=":material/check:")
                            st.info("Note loaded. Navigate to the AI Note Authoring tab above to enhance.", icon=":material/info:")

                    with act_c3:
                        st.download_button(
                            "Download .md",
                            icon=":material/download:",
                            data=note_raw_text,
                            file_name=os.path.basename(active_path),
                            mime="text/markdown",
                            key="btn_download_active",
                            use_container_width=True,
                        )

                    with act_c4:
                        if st.button("Delete", icon=":material/delete:", key="btn_delete_active", type="secondary", use_container_width=True):
                            st.session_state["confirm_delete_active"] = True

                    if st.session_state.get("confirm_delete_active"):
                        st.warning(f"Are you sure you want to permanently delete `{os.path.basename(active_path)}`?", icon=":material/warning:")
                        col_del_yes, col_del_no = st.columns(2)
                        with col_del_yes:
                            if st.button("Yes, Delete Permanently", icon=":material/delete_forever:", type="primary", key="btn_confirm_del_yes"):
                                delete_study_note(active_path)
                                st.cache_data.clear()
                                st.session_state["selected_note_path"] = None
                                st.session_state["confirm_delete_active"] = False
                                st.success("Note deleted successfully.", icon=":material/check_circle:")
                                st.rerun()
                        with col_del_no:
                            if st.button("Cancel", icon=":material/close:", key="btn_confirm_del_no"):
                                st.session_state["confirm_delete_active"] = False
                                st.rerun()

                    # Interactive Document Tabs
                    tab_read, tab_edit, tab_meta, tab_raw = st.tabs([
                        ":material/menu_book: Rendered Document",
                        ":material/bolt: Quick In-Place Edit",
                        ":material/badge: Metadata & Frontmatter",
                        ":material/code: Raw Markdown Source",
                    ])

                    with tab_read:
                        st.markdown(sanitize_latex_markdown(meta["raw_body"]))

                    with tab_edit:
                        st.markdown("##### :material/bolt: Quick In-Place Note Editor")
                        quick_edit_body = st.text_area(
                            "Note Body",
                            value=meta["raw_body"],
                            height=380,
                            key=f"quick_edit_{active_path}",
                            label_visibility="collapsed",
                        )
                        if st.button("Save Quick Changes", icon=":material/save:", type="primary", key="btn_quick_save"):
                            save_course_note_file(
                                subject=meta.get("subject", "General"),
                                title=meta.get("title", os.path.splitext(os.path.basename(active_path))[0]),
                                content=quick_edit_body,
                                filename=os.path.basename(active_path),
                                difficulty=meta.get("difficulty", 3),
                                exam_date=str(meta.get("exam_date")) if meta.get("exam_date") else None,
                                tags=meta.get("tags", []),
                                overwrite=True,
                            )
                            st.cache_data.clear()
                            st.success(f"Updated `{os.path.basename(active_path)}` successfully!", icon=":material/check_circle:")
                            st.rerun()

                    with tab_meta:
                        st.markdown("##### :material/badge: Document Metadata & Attributes")
                        meta_df = {
                            "Attribute": ["Title", "Subject Folder", "Difficulty", "Target Exam", "Tags", "File Path", "Size", "Word Count"],
                            "Value": [
                                meta.get("title", ""),
                                meta.get("subject", ""),
                                f"{meta.get('difficulty', 3)} / 5",
                                str(meta.get("exam_date", "None")),
                                ", ".join(meta.get("tags", [])) or "None",
                                active_path,
                                f"{os.stat(active_path).st_size} bytes",
                                f"{word_count} words",
                            ],
                        }
                        st.table(meta_df)

                    with tab_raw:
                        st.code(note_raw_text, language="markdown")

    # =========================================================================
    # PANEL 2: :material/edit_note: NOTE AUTHORING STUDIO (Professional Authoring System)
    # =========================================================================
    with tab_author:
        st.markdown("### :material/edit_note: Professional Note Authoring Studio")
        st.markdown(
            "Craft structured, publication-grade academic notes with LaTeX formulas, templates, "
            "and persistent classification into subject folder trees."
        )

        # Apply any pending content, title, tags, or subject updates to widget state keys before widgets render
        if st.session_state.get("author_pending_content") is not None:
            st.session_state["author_editor_text"] = st.session_state.pop("author_pending_content")
            st.session_state["author_content"] = st.session_state["author_editor_text"]
        if st.session_state.get("author_pending_title") is not None:
            st.session_state["author_title_input"] = st.session_state.pop("author_pending_title")
            st.session_state["author_title"] = st.session_state["author_title_input"]
        if st.session_state.get("author_pending_tags") is not None:
            st.session_state["author_tags_input"] = st.session_state.pop("author_pending_tags")
            st.session_state["author_tags"] = st.session_state["author_tags_input"]
        if st.session_state.get("author_pending_subject") is not None:
            p_subj = st.session_state.pop("author_pending_subject")
            st.session_state["author_subject"] = p_subj
            if "author_subj_select" in st.session_state:
                st.session_state["author_subj_select"] = p_subj

        # --- Step 1: Note Metadata & Folder Classification ---
        with st.container():
            st.markdown("#### :material/badge: Document Metadata & Subject Classification")
            c_meta1, c_meta2 = st.columns([1.5, 1.5])

            with c_meta1:
                subj_options = list_subject_folders()
                if "+ New Subject Folder..." not in subj_options:
                    subj_options.append("+ New Subject Folder...")

                curr_subject = st.session_state.get("author_subject", "General")
                subj_index = subj_options.index(curr_subject) if curr_subject in subj_options else 0

                selected_subj_choice = st.selectbox(
                    ":material/folder: Subject Folder Tree",
                    subj_options,
                    index=subj_index,
                    key="author_subj_select",
                )

                if selected_subj_choice == "+ New Subject Folder...":
                    final_subject = st.text_input(
                        "New Subject Folder Name",
                        placeholder="e.g. Theoretical Physics, Calculus II",
                        key="author_new_subj_input",
                    ).strip()
                else:
                    final_subject = selected_subj_choice

                st.session_state["author_subject"] = final_subject

                note_title_input = st.text_input(
                    ":material/title: Note Title / Topic",
                    value=st.session_state.get("author_title", ""),
                    placeholder="e.g. Convolutional Neural Networks & Feature Maps",
                    key="author_title_input",
                )
                st.session_state["author_title"] = note_title_input

            with c_meta2:
                col_d, col_e = st.columns(2)
                with col_d:
                    diff_val = st.slider(
                        ":material/local_fire_department: Difficulty Rating",
                        min_value=1,
                        max_value=5,
                        value=int(st.session_state.get("author_difficulty", 3)),
                        help="1: Fundamentals, 3: Core Academic, 5: Rigorous Derivations",
                        key="author_diff_slider",
                    )
                    st.session_state["author_difficulty"] = diff_val

                with col_e:
                    exam_date_val = st.date_input(
                        ":material/event: Target Exam Date (Optional)",
                        value=st.session_state.get("author_exam_date"),
                        key="author_exam_input",
                    )
                    st.session_state["author_exam_date"] = exam_date_val

                tags_input_str = st.text_input(
                    ":material/label: Tags (comma-separated)",
                    value=st.session_state.get("author_tags", ""),
                    placeholder="e.g. neural-nets, computer-vision, calculus",
                    key="author_tags_input",
                )
                st.session_state["author_tags"] = tags_input_str

        # --- Step 2: Starter Template & LaTeX Snippet Toolbar ---
        st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
        st.markdown("#### :material/library_books: Starter Templates & Academic Snippet Toolbar")

        t_col1, t_col2 = st.columns([1.8, 2.2])
        with t_col1:
            template_choices = get_template_names()
            chosen_template = st.selectbox(":material/snippet_folder: Load Starter Template", template_choices, key="author_template_select")
            if st.button("Apply Template to Editor", icon=":material/content_paste_go:", type="secondary", key="btn_apply_template", use_container_width=True):
                rendered_tmpl = get_template(
                    chosen_template,
                    topic=note_title_input or "New Academic Topic",
                    subject=final_subject or "General",
                    difficulty=diff_val,
                )
                st.session_state["author_pending_content"] = rendered_tmpl
                st.toast(f"Applied '{chosen_template}' template!", icon=":material/check:")
                st.rerun()

        with t_col2:
            st.markdown("**:material/add_circle: Quick-Insert LaTeX Math & Academic Snippets**")

            def insert_academic_snippet(snippet_name: str, friendly_label: str):
                snip_text = get_snippet(snippet_name)
                current_val = st.session_state.get("author_editor_text", "")
                if current_val and not current_val.endswith("\n"):
                    st.session_state["author_pending_content"] = current_val + "\n" + snip_text
                else:
                    st.session_state["author_pending_content"] = current_val + snip_text
                st.toast(f"Inserted {friendly_label} snippet!", icon=":material/check:")
                st.rerun()

            snip_c1, snip_c2, snip_c3 = st.columns(3)
            with snip_c1:
                if st.button("$$ Display Math", icon=":material/functions:", key="snip_disp", use_container_width=True):
                    insert_academic_snippet("LaTeX Display Equation", "Display Math")
                if st.button("Definition Box", icon=":material/menu_book:", key="snip_def", use_container_width=True):
                    insert_academic_snippet("Definition Callout", "Definition")
            with snip_c2:
                if st.button("$ Inline Math", icon=":material/calculate:", key="snip_inl", use_container_width=True):
                    insert_academic_snippet("LaTeX Inline Equation", "Inline Math")
                if st.button("Theorem & Proof", icon=":material/psychology:", key="snip_thm", use_container_width=True):
                    insert_academic_snippet("Theorem & Proof Block", "Theorem & Proof")
            with snip_c3:
                if st.button("Fraction / Integral", icon=":material/percent:", key="snip_frac", use_container_width=True):
                    insert_academic_snippet("LaTeX Fraction & Integral", "Fraction/Integral")
                if st.button("Comparison Table", icon=":material/table_chart:", key="snip_tbl", use_container_width=True):
                    insert_academic_snippet("Comparison Table", "Comparison Table")

        # --- Step 3: Split-View Editor & Live KaTeX Preview ---
        st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
        st.markdown("#### :material/splitscreen: Note Authoring Workspace & Live Preview")

        edit_col, prev_col = st.columns([1.1, 1.1])

        with edit_col:
            st.caption(":material/edit: Markdown & LaTeX Editor")
            editor_text = st.text_area(
                "Editor",
                height=480,
                placeholder="Type your markdown notes here. LaTeX equations like $E = mc^2$ and $$\\int f(x) dx$$ are fully supported.",
                key="author_editor_text",
                label_visibility="collapsed",
            )
            st.session_state["author_content"] = editor_text

        with prev_col:
            prev_head1, prev_head2 = st.columns([2.5, 1.5])
            with prev_head1:
                st.caption(":material/visibility: Live Rendered Document Preview")
            with prev_head2:
                if st.button("Refresh Preview", icon=":material/refresh:", key="btn_refresh_preview", use_container_width=True):
                    st.rerun()

            with st.container(border=True, height=480):
                if not editor_text.strip():
                    st.info("Start writing on the left or click a template / snippet above to preview your rendered document.", icon=":material/info:")
                else:
                    parsed_p = parse_note_metadata(editor_text)
                    preview_title = note_title_input.strip() or parsed_p.get("title") or "Untitled Note"
                    preview_subject = final_subject.strip() or parsed_p.get("subject") or "General"
                    preview_diff = diff_val
                    preview_tags = [t.strip() for t in tags_input_str.split(",") if t.strip()] or parsed_p.get("tags", [])
                    preview_body = parsed_p.get("raw_body", editor_text)
                    word_cnt = len(preview_body.split())
                    read_mins = max(1, round(word_cnt / 200))

                    # Graphical document header preview card
                    header_badges = []
                    header_badges.append(f'<span class="badge-diff-{preview_diff}"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">local_fire_department</span>Level {preview_diff}/5</span>')
                    if exam_date_val:
                        header_badges.append(f'<span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">event</span>Exam: {exam_date_val.strftime("%Y-%m-%d")}</span>')
                    header_badges.append(f'<span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">schedule</span>~{read_mins} min read ({word_cnt} words)</span>')
                    badges_html = " ".join(header_badges)

                    tags_html = " ".join(f'<span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">label</span>{t}</span>' for t in preview_tags) if preview_tags else ""
                    tags_div = f'<div style="margin-top: 8px;">{tags_html}</div>' if tags_html else ""

                    preview_header_card = (
                        f'<div class="doc-header-card" style="margin-bottom: 14px;">\n'
                        f'<div style="font-size: 0.76rem; color: rgba(156, 163, 175, 0.9); margin-bottom: 4px;">'
                        f'<span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -1px; margin-right: 3px;">folder</span><strong>{preview_subject}</strong>'
                        f'</div>\n'
                        f'<div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 8px;">\n'
                        f'<h3 style="margin: 0; font-size: 1.35rem; font-weight: 700; color: var(--text-color, inherit);">'
                        f'{preview_title}'
                        f'</h3>\n'
                        f'<div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">{badges_html}</div>\n'
                        f'</div>'
                        + (f'\n{tags_div}' if tags_div else '') +
                        '\n</div>'
                    )
                    st.markdown(preview_header_card, unsafe_allow_html=True)
                    st.markdown(sanitize_latex_markdown(preview_body), unsafe_allow_html=True)

        # --- Step 4: Persistence Controls ---
        st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
        save_col1, save_col2, save_col3, save_col4 = st.columns([1.5, 1.2, 1.2, 1.0])

        with save_col1:
            if st.button("Save Note to Subject Folder Tree", icon=":material/save:", type="primary", use_container_width=True):
                if not final_subject.strip():
                    st.error("Please specify a Subject Folder.", icon=":material/error:")
                elif not note_title_input.strip():
                    st.error("Please provide a Title / Topic for the note.", icon=":material/error:")
                elif not editor_text.strip():
                    st.error("Cannot save empty note content.", icon=":material/error:")
                else:
                    tag_list = [t.strip() for t in tags_input_str.split(",") if t.strip()]
                    exam_str = exam_date_val.strftime("%Y-%m-%d") if exam_date_val else None

                    saved_path = save_course_note_file(
                        subject=final_subject,
                        title=note_title_input,
                        content=editor_text,
                        difficulty=diff_val,
                        exam_date=exam_str,
                        tags=tag_list,
                        overwrite=True,
                    )
                    st.cache_data.clear()
                    st.session_state["selected_note_path"] = saved_path
                    st.toast(f"Saved note to {final_subject}!", icon=":material/check:")
                    st.success(f"Saved note to `{os.path.basename(saved_path)}` under `{final_subject}`! View it in the **:material/folder_open: Subject Vault & Tree Explorer** panel.", icon=":material/check_circle:")
                    st.rerun()

        with save_col2:
            if st.button("Polish & Format (AI)", icon=":material/auto_awesome:", key="btn_polish_draft", use_container_width=True):
                if not editor_text.strip():
                    st.warning("Note content is empty.", icon=":material/warning:")
                else:
                    with st.spinner("Polishing formatting and LaTeX..."):
                        enh_res = enhance_existing_note(editor_text, action="polish_formatting")
                        if enh_res["success"]:
                            st.session_state["author_pending_content"] = enh_res["content"]
                            st.toast("Polished note successfully!", icon=":material/check:")
                            st.rerun()
                        else:
                            st.error(f"Error: {enh_res['error']}", icon=":material/error:")

        with save_col3:
            st.download_button(
                "Export (.md)",
                icon=":material/download:",
                data=editor_text,
                file_name=f"{note_title_input.lower().replace(' ', '_') or 'note'}.md",
                mime="text/markdown",
                use_container_width=True,
                key="btn_download_author_draft",
            )

        with save_col4:
            if st.button("Clear", icon=":material/restart_alt:", key="btn_clear_author_draft", use_container_width=True):
                st.session_state["author_pending_title"] = ""
                st.session_state["author_pending_content"] = ""
                st.session_state["author_pending_tags"] = ""
                st.toast("Cleared Authoring Studio!", icon=":material/check:")
                st.rerun()

    # =========================================================================
    # PANEL 3: :material/smart_toy: AI NOTE AUTHORING (Generation & Co-Authoring Engine)
    # =========================================================================
    with tab_ai:
        st.markdown("### :material/smart_toy: AI Academic Note Authoring Studio")
        st.markdown(
            "Use your configured local model, with optional offline Wikipedia context, "
            "to draft publication-grade notes or enhance existing lecture material."
        )

        ai_tab_gen, ai_tab_enh = st.tabs([
            ":material/rocket_launch: Generate Full Note from Topic",
            ":material/auto_awesome: AI Co-Author & Note Enhancer",
        ])

        # --- Tab A: Full Note Generation from Scratch ---
        with ai_tab_gen:
            st.markdown("#### :material/rocket_launch: Generate New Academic Note")
            gen_c1, gen_c2 = st.columns([1.3, 1.3])

            with gen_c1:
                existing_folders = list_subject_folders()
                if not existing_folders:
                    existing_folders = ["General"]

                subj_dropdown_options = list(existing_folders)
                if "+ New Subject Folder..." not in subj_dropdown_options:
                    subj_dropdown_options.append("+ New Subject Folder...")

                curr_saved_subj = st.session_state.get("author_subject", existing_folders[0])
                folder_index = (
                    subj_dropdown_options.index(curr_saved_subj)
                    if curr_saved_subj in subj_dropdown_options
                    else 0
                )

                selected_folder_opt = st.selectbox(
                    ":material/folder: Target Subject Folder",
                    subj_dropdown_options,
                    index=folder_index,
                    help="Select an existing subject folder from your local vault",
                    key="ai_gen_subj_select",
                )

                if selected_folder_opt == "+ New Subject Folder...":
                    gen_subj = st.text_input(
                        "New Subject Folder Name",
                        placeholder="e.g. Theoretical Physics, Calculus II",
                        key="ai_gen_new_subj_input",
                    ).strip() or "General"
                else:
                    gen_subj = selected_folder_opt

                st.session_state["author_subject"] = gen_subj

                gen_topic = st.text_input(
                    ":material/school: Topic or Learning Objective",
                    placeholder="e.g. Principal Component Analysis & SVD, Heisenberg Uncertainty Principle",
                    key="ai_gen_topic_input",
                )
            with gen_c2:
                detail_level_options = [
                    "Standard Academic Lecture",
                    "Deep In-Depth Analysis",
                    "Historical & Conceptual Narrative",
                    "Written Academic Exposition",
                    "Rigorous Mathematical Treatment",
                    "Concise Overview",
                ]
                detail_descriptions = {
                    "Standard Academic Lecture": "Balanced academic synthesis with core definitions, equations, theory, and recall questions.",
                    "Deep In-Depth Analysis": "Exhaustive multi-dimensional deep dive into internal mechanisms, theoretical subtleties, edge cases, and comparative paradigms.",
                    "Historical & Conceptual Narrative": "Extensive written history exploring discovery origins, timeline, seminal thinkers, paradigm shifts, and foundational context.",
                    "Written Academic Exposition": "Rich narrative textbook prose focusing on conceptual depth, analogies, and qualitative understanding without terse bullet points.",
                    "Rigorous Mathematical Treatment": "Formal graduate-level treatment with step-by-step LaTeX derivations, theorems, proof sketches, and edge cases.",
                    "Concise Overview": "High-yield summary with core definitions, essential formula, and concise bullet points (250-400 words).",
                }

                gen_detail = st.selectbox(
                    ":material/tune: Detail & Depth Level",
                    detail_level_options,
                    index=0,
                    help="Determines the pedagogical depth, prose style, and structural focus of the AI note",
                    key="ai_gen_detail_select",
                )
                st.caption(f":material/info: *{detail_descriptions.get(gen_detail, '')}*")

                gen_wiki_ground = st.checkbox(
                    ":material/public: Ground with offline Wikipedia",
                    value=wiki_available,
                    disabled=not wiki_available,
                    help="Enable and configure an archive in Settings ? Local AI & Wikipedia.",
                    key="ai_gen_wiki_checkbox",
                )
                gen_math_toggle = st.checkbox(
                    ":material/functions: Include Rigorous LaTeX Math Equations",
                    value=True,
                    help="Formats equations in KaTeX-compliant LaTeX ($...$ and $$...$$)",
                    key="ai_gen_math_checkbox",
                )

            gen_guidance = st.text_area(
                ":material/flag: Specific Focus Areas or Exam Objectives (Optional)",
                placeholder="e.g. Include step-by-step projection matrix derivation, explain eigenvalues intuitively...",
                height=70,
                key="ai_gen_guidance_input",
            )

            gen_button = st.button("Generate Academic Note with Local AI", icon=":material/smart_toy:", type="primary", use_container_width=True, key="btn_run_ai_gen")

            if gen_button:
                if not gen_topic.strip():
                    st.error("Please provide a Topic or Learning Objective.", icon=":material/error:")
                else:
                    with st.spinner("Drafting note..."):
                        gen_res = generate_ai_study_note(
                            subject=gen_subj,
                            topic=gen_topic,
                            detail_level=gen_detail,
                            include_math=gen_math_toggle,
                            ground_with_wiki=wiki_available and gen_wiki_ground,
                            custom_guidance=gen_guidance,
                        )
                        st.session_state["ai_generated_result"] = gen_res

            # Display generated result if available
            gen_res = st.session_state.get("ai_generated_result")
            if gen_res:
                st.markdown("---")
                if not gen_res["success"]:
                    st.error(f"Generation failed: {gen_res['error']}", icon=":material/error:")
                else:
                    if gen_res.get("wiki_grounded"):
                        st.markdown(
                            f"""
<div class="ai-grounding-banner">
    <span class="material-symbols-rounded" style="vertical-align: -2px; margin-right: 4px;">public</span><strong>Grounded Reference:</strong> Successfully verified against offline Wikipedia article: 
    <em>{gen_res.get('wiki_title', gen_res['topic'])}</em>
</div>
""",
                            unsafe_allow_html=True,
                        )

                    st.markdown("#### :material/description: Generated Note Preview")
                    with st.container(border=True):
                        st.markdown(sanitize_latex_markdown(gen_res["content"]))

                    col_ai_act1, col_ai_act2, col_ai_act3 = st.columns([1.5, 1.5, 1.0])
                    with col_ai_act1:
                        if st.button("Open in Authoring Studio", icon=":material/edit_note:", key="btn_ai_to_studio", type="primary", use_container_width=True):
                            parsed_ai = parse_note_metadata(gen_res["content"], fallback_filename=gen_res["topic"])
                            r_ai_body = parsed_ai.get("raw_body", gen_res["content"])
                            st.session_state["author_subject"] = gen_res["subject"]
                            st.session_state["author_title"] = parsed_ai.get("title", gen_res["topic"])
                            st.session_state["author_content"] = r_ai_body
                            st.session_state["author_pending_subject"] = gen_res["subject"]
                            st.session_state["author_pending_title"] = parsed_ai.get("title", gen_res["topic"])
                            st.session_state["author_pending_content"] = r_ai_body
                            st.session_state["author_difficulty"] = parsed_ai.get("difficulty", 3)
                            st.session_state["author_tags"] = ", ".join(parsed_ai.get("tags", []))
                            st.session_state["author_pending_tags"] = ", ".join(parsed_ai.get("tags", []))
                            st.session_state["notes_workspace_tab"] = ":material/edit_note: Note Authoring Studio"
                            st.toast("Note transferred to Authoring Studio!", icon=":material/check:")
                            st.success("Note transferred to the **:material/edit_note: Note Authoring Studio** panel above. Switch to that panel to edit.", icon=":material/check_circle:")

                    with col_ai_act2:
                        if st.button("Save Directly to Subject Tree", icon=":material/save:", key="btn_ai_direct_save", use_container_width=True):
                            parsed_ai = parse_note_metadata(gen_res["content"], fallback_filename=gen_res["topic"])
                            default_tags = [gen_res["topic"].lower().replace(" ", "-"), "academic-note"]
                            saved_p = save_course_note_file(
                                subject=gen_res["subject"],
                                title=parsed_ai.get("title", gen_res["topic"]),
                                content=parsed_ai.get("raw_body", gen_res["content"]),
                                difficulty=parsed_ai.get("difficulty", 3),
                                exam_date=str(parsed_ai.get("exam_date")) if parsed_ai.get("exam_date") else None,
                                tags=parsed_ai.get("tags") or default_tags,
                                overwrite=True,
                            )
                            st.cache_data.clear()
                            st.session_state["selected_note_path"] = saved_p
                            st.toast(f"Saved note to {gen_res['subject']}!", icon=":material/check:")
                            st.success(f"Saved directly to `{os.path.basename(saved_p)}` in `{gen_res['subject']}`! Available in **:material/folder_open: Subject Vault & Tree Explorer**.", icon=":material/check_circle:")

                    with col_ai_act3:
                        st.download_button(
                            "Download .md",
                            icon=":material/download:",
                            data=gen_res["content"],
                            file_name=f"{gen_res['topic'].lower().replace(' ', '_')}.md",
                            mime="text/markdown",
                            use_container_width=True,
                            key="btn_download_ai_res",
                        )

        # --- Tab B: Note Enhancer & Co-Authoring ---
        with ai_tab_enh:
            st.markdown("#### :material/auto_awesome: AI Co-Author & Note Enhancer")
            all_existing_notes = list_study_notes()

            if not all_existing_notes:
                st.info("No existing notes found in vault to enhance. Generate or author one first!", icon=":material/info:")
            else:
                note_options = {f"{n['subject']} / {n['name']}": n["path"] for n in all_existing_notes}
                sel_label = st.selectbox(":material/description: Select Note to Enhance", list(note_options.keys()), key="ai_enh_note_select")
                target_enhance_path = note_options[sel_label]

                current_text = load_study_note(target_enhance_path)

                action_choice = st.selectbox(
                    ":material/tune: Enhancement Action",
                    [
                        ("add_derivations", "Add Rigorous LaTeX Derivations ($$)"),
                        ("add_wiki_grounding", "Enrich with Foundational Context"),
                        ("add_historical_context", "Enrich with Historical Context & Origin Narrative"),
                        ("deep_analysis", "Expand into Deep In-Depth Analysis & Edge Cases"),
                        ("generate_flashcards", "Generate Active Recall Flashcards & Quiz"),
                        ("summarize_cheatsheet", "Generate High-Yield Cheatsheet (TL;DR)"),
                        ("polish_formatting", "Polish Formatting, Headings & Equations"),
                        ("custom", "Custom AI Prompt Instruction"),
                    ],
                    format_func=lambda x: x[1],
                    key="ai_enh_action_select",
                )

                custom_instruct_text = None
                if action_choice[0] == "custom":
                    custom_instruct_text = st.text_input(":material/chat: Custom Prompt Instruction", placeholder="e.g. Translate formulas to Dirac bra-ket notation...", key="ai_custom_instruct_input")

                if st.button("Execute AI Enhancement", icon=":material/auto_awesome:", type="primary", use_container_width=True, key="btn_run_ai_enh"):
                    with st.spinner("Enhancing note..."):
                        res_enh = enhance_existing_note(
                            current_markdown=current_text,
                            action=action_choice[0],
                            custom_instruction=custom_instruct_text,
                        )
                        st.session_state["ai_enhance_result"] = res_enh

                # Display enhancement results
                if st.session_state.get("ai_enhance_result"):
                    enh_out = st.session_state["ai_enhance_result"]
                    if not enh_out["success"]:
                        st.error(f"Enhancement error: {enh_out['error']}", icon=":material/error:")
                    else:
                        st.markdown("---")
                        st.markdown("##### :material/description: Enhanced Note Preview")
                        with st.container(border=True):
                            st.markdown(sanitize_latex_markdown(enh_out["content"]))

                        e_act1, e_act2 = st.columns(2)
                        with e_act1:
                            if st.button("Save Changes to Note File", icon=":material/save:", key="btn_save_enh_overwrite", type="primary", use_container_width=True):
                                with open(target_enhance_path, "w", encoding="utf-8") as f:
                                    f.write(enh_out["content"])
                                st.cache_data.clear()
                                st.session_state["selected_note_path"] = target_enhance_path
                                st.toast("Note updated successfully!", icon=":material/check:")
                                st.success("Updated note file successfully! View the changes in the **:material/folder_open: Subject Vault & Tree Explorer** panel.", icon=":material/check_circle:")

                        with e_act2:
                            if st.button("Load Enhanced Version into Studio", icon=":material/edit_note:", key="btn_load_enh_studio", use_container_width=True):
                                parsed_enh = parse_note_metadata(enh_out["content"])
                                r_enh_body = parsed_enh.get("raw_body", enhancement_out := enh_out["content"])
                                st.session_state["author_content"] = r_enh_body
                                st.session_state["author_pending_content"] = r_enh_body
                                st.session_state["notes_workspace_tab"] = ":material/edit_note: Note Authoring Studio"
                                st.toast("Transferred to Authoring Studio!", icon=":material/check:")
                                st.info("Enhanced content loaded. Navigate to the **:material/edit_note: Note Authoring Studio** tab above to edit.", icon=":material/info:")

    # =========================================================================
    # PANEL 4: :material/drive_file_move: FOLDER TREE MANAGER & VAULT STATS
    # =========================================================================
    with tab_manager:
        st.markdown("### :material/drive_file_move: Folder Tree Manager & Vault Operations")
        st.markdown(
            "Manage subject directories, move notes between subjects, remove unused files or folders, "
            "and inspect the structure and health of your Markdown vault."
        )

        all_vault_notes = list_study_notes(recursive=True)

        tab_m_notes, tab_m_folders, tab_m_tree = st.tabs([
            ":material/drive_file_move: Note Operations (Move & Delete)",
            ":material/folder: Folder Operations (Create, Rename, Delete)",
            ":material/account_tree: Vault Directory Breakdown & Health",
        ])

        # ---------------------------------------------------------------------
        # SUB-TAB 1: NOTE OPERATIONS (Move & Delete)
        # ---------------------------------------------------------------------
        with tab_m_notes:
            col_note_move, col_note_del = st.columns(2)

            # Move Note
            with col_note_move:
                with st.container(border=True):
                    st.markdown("#### :material/drive_file_move: Move Note Between Folders")
                    st.caption("Transfer a note to another subject folder. The internal YAML frontmatter is automatically updated.")

                    if not all_vault_notes:
                        st.info("No notes found in the vault to move.", icon=":material/info:")
                    else:
                        note_options = {n["path"]: f"[{n['subject']}] {n['name']}" for n in all_vault_notes}
                        sel_move_path = st.selectbox(
                            ":material/description: Select Note to Move",
                            options=list(note_options.keys()),
                            format_func=lambda p: note_options[p],
                            key="sb_mgr_move_note_select",
                        )
                        target_note_obj = next((n for n in all_vault_notes if n["path"] == sel_move_path), None)
                        curr_note_subj = target_note_obj["subject"] if target_note_obj else "General"

                        st.markdown(
                            f"<div style='font-size: 0.8rem; color: rgba(156,163,175,0.9); margin-bottom: 8px;'>"
                            f"Current folder: <strong><code>{curr_note_subj}</code></strong> &bull; "
                            f"Size: <strong>{round((target_note_obj['size_bytes'] if target_note_obj else 0) / 1024, 1)} KB</strong>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        existing_subj_folders = list_subject_folders()
                        target_folder_options = existing_subj_folders + ["+ Create New Subject Folder..."]
                        target_folder_choice = st.selectbox(
                            ":material/folder: Target Subject Folder",
                            options=target_folder_options,
                            key="sb_mgr_target_folder_select",
                        )

                        if target_folder_choice == "+ Create New Subject Folder...":
                            new_target_folder_input = st.text_input(
                                "New Target Folder Name",
                                placeholder="e.g. Astrophysics, Deep Learning",
                                key="input_mgr_move_new_folder",
                            ).strip()
                            dest_subject = new_target_folder_input
                        else:
                            dest_subject = target_folder_choice

                        if st.button("Move Note", icon=":material/drive_file_move:", type="primary", key="btn_mgr_move_note_submit", use_container_width=True):
                            if not dest_subject:
                                st.error("Please enter or select a valid target subject folder.", icon=":material/error:")
                            elif dest_subject.strip().lower() == curr_note_subj.lower():
                                st.warning(f"Note is already in '{curr_note_subj}'. Select a different target folder.", icon=":material/warning:")
                            else:
                                try:
                                    new_note_path = move_note_to_subject_folder(sel_move_path, dest_subject)
                                    st.cache_data.clear()
                                    if st.session_state.get("selected_note_path") == sel_move_path:
                                        st.session_state["selected_note_path"] = new_note_path
                                    st.toast(f"Moved note to {dest_subject}!", icon=":material/check:")
                                    st.success(f"Successfully moved `{target_note_obj['name']}` to `{dest_subject}`!", icon=":material/check_circle:")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to move note: {e}", icon=":material/error:")

            # Delete Note
            with col_note_del:
                with st.container(border=True):
                    st.markdown("#### :material/delete: Remove Note")
                    st.caption("Permanently delete a Markdown note file from the vault. This action cannot be undone.")

                    if not all_vault_notes:
                        st.info("No notes found in the vault to delete.", icon=":material/info:")
                    else:
                        note_del_options = {n["path"]: f"[{n['subject']}] {n['name']}" for n in all_vault_notes}
                        sel_del_path = st.selectbox(
                            ":material/description: Select Note to Remove",
                            options=list(note_del_options.keys()),
                            format_func=lambda p: note_del_options[p],
                            key="sb_mgr_del_note_select",
                        )
                        del_target_obj = next((n for n in all_vault_notes if n["path"] == sel_del_path), None)

                        if del_target_obj:
                            st.markdown(
                                f"<div style='font-size: 0.8rem; color: rgba(156,163,175,0.9); margin-bottom: 8px;'>"
                                f"Subject: <strong><code>{del_target_obj['subject']}</code></strong> &bull; "
                                f"Modified: <strong>{del_target_obj['modified_at']}</strong> &bull; "
                                f"Size: <strong>{round(del_target_obj['size_bytes'] / 1024, 1)} KB</strong>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )
                            confirm_del_note = st.checkbox(
                                f"Permanently delete '{del_target_obj['name']}' from disk",
                                key="chk_mgr_confirm_del_note",
                            )
                            if st.button(
                                "Delete Note",
                                icon=":material/delete_forever:",
                                type="primary",
                                disabled=not confirm_del_note,
                                key="btn_mgr_delete_note_submit",
                                use_container_width=True,
                            ):
                                try:
                                    delete_study_note(sel_del_path)
                                    st.cache_data.clear()
                                    if st.session_state.get("selected_note_path") == sel_del_path:
                                        st.session_state["selected_note_path"] = None
                                    st.toast(f"Deleted note '{del_target_obj['name']}'", icon=":material/delete:")
                                    st.success(f"Permanently removed `{del_target_obj['name']}` from `{del_target_obj['subject']}`.", icon=":material/check_circle:")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to delete note: {e}", icon=":material/error:")

        # ---------------------------------------------------------------------
        # SUB-TAB 2: FOLDER OPERATIONS (Create, Rename, Delete, Auto-Organize)
        # ---------------------------------------------------------------------
        with tab_m_folders:
            col_f_left, col_f_right = st.columns(2)

            with col_f_left:
                # Create Folder
                with st.container(border=True):
                    st.markdown("#### :material/create_new_folder: Create Subject Folder")
                    st.caption("Create a new directory tree inside `data/notes/` for organizing notes.")
                    new_folder_input = st.text_input(
                        "Folder Name",
                        placeholder="e.g. Linear Algebra, Thermodynamics",
                        key="create_subj_folder_input",
                    )
                    if st.button("Create Folder Tree", icon=":material/create_new_folder:", type="primary", key="btn_create_folder_submit", use_container_width=True):
                        if not new_folder_input.strip():
                            st.error("Please enter a valid folder name.", icon=":material/error:")
                        else:
                            try:
                                created = create_subject_folder(new_folder_input)
                                st.cache_data.clear()
                                st.toast(f"Created folder '{os.path.basename(created)}'!", icon=":material/check:")
                                st.success(f"Created subject folder: `{os.path.basename(created)}`", icon=":material/check_circle:")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to create folder: {e}", icon=":material/error:")

                # Rename Folder
                with st.container(border=True):
                    st.markdown("#### :material/drive_file_rename_outline: Rename Subject Folder")
                    st.caption("Renames a subject folder and updates the YAML frontmatter of all notes inside to match.")
                    existing_folders = list_subject_folders()
                    if not existing_folders:
                        st.info("No subject folders exist yet.", icon=":material/info:")
                    else:
                        rename_old_subj = st.selectbox(
                            "Select Folder to Rename",
                            options=existing_folders,
                            key="sb_rename_folder_select",
                        )
                        folder_notes = [n for n in all_vault_notes if n["subject"] == rename_old_subj]
                        st.markdown(
                            f"<div style='font-size: 0.8rem; color: rgba(156,163,175,0.9); margin-bottom: 8px;'>"
                            f"Contains: <strong>{len(folder_notes)} note(s)</strong> &bull; "
                            f"Total size: <strong>{round(sum(n['size_bytes'] for n in folder_notes) / 1024, 1)} KB</strong>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                        rename_new_subj = st.text_input(
                            "New Folder Name",
                            placeholder=f"e.g. Advanced {rename_old_subj}",
                            key="input_rename_folder_new",
                        )
                        if st.button("Rename Subject Folder", icon=":material/drive_file_rename_outline:", key="btn_execute_rename_folder", use_container_width=True):
                            if not rename_new_subj.strip():
                                st.error("Please enter a valid new folder name.", icon=":material/error:")
                            elif rename_new_subj.strip().lower() == rename_old_subj.lower():
                                st.warning("New folder name is identical to the current folder name.", icon=":material/warning:")
                            else:
                                try:
                                    renamed_path = rename_subject_folder(rename_old_subj, rename_new_subj)
                                    st.cache_data.clear()
                                    clean_new = os.path.basename(renamed_path)
                                    if st.session_state.get("author_subject") == rename_old_subj:
                                        st.session_state["author_subject"] = clean_new
                                    if st.session_state.get("selected_note_path") and rename_old_subj in st.session_state["selected_note_path"]:
                                        fname = os.path.basename(st.session_state["selected_note_path"])
                                        st.session_state["selected_note_path"] = os.path.join(renamed_path, fname)
                                    if rename_old_subj in st.session_state.get("vault_collapsed_folders", set()):
                                        st.session_state["vault_collapsed_folders"].discard(rename_old_subj)
                                        st.session_state["vault_collapsed_folders"].add(clean_new)
                                    st.toast(f"Renamed folder to '{clean_new}'!", icon=":material/check:")
                                    st.success(f"Renamed `{rename_old_subj}` &rarr; `{clean_new}` and updated {len(folder_notes)} note(s).", icon=":material/check_circle:")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to rename folder: {e}", icon=":material/error:")

            with col_f_right:
                # Remove Folder
                with st.container(border=True):
                    st.markdown("#### :material/folder_delete: Remove Subject Folder")
                    st.caption("Permanently delete a subject folder directory and all notes contained within.")
                    existing_folders = list_subject_folders()
                    if not existing_folders:
                        st.info("No subject folders exist to delete.", icon=":material/info:")
                    else:
                        del_subj = st.selectbox(
                            "Select Folder to Remove",
                            options=existing_folders,
                            key="sb_delete_folder_select",
                        )
                        del_folder_notes = [n for n in all_vault_notes if n["subject"] == del_subj]
                        num_notes = len(del_folder_notes)
                        if num_notes > 0:
                            st.warning(f"Folder `{del_subj}` contains **{num_notes} note(s)**. Deleting it will permanently remove all files inside.", icon=":material/warning:")
                        else:
                            st.info(f"Folder `{del_subj}` is currently empty.", icon=":material/info:")

                        confirm_del_folder = st.checkbox(
                            f"Permanently delete folder '{del_subj}'" + (f" and {num_notes} note(s)" if num_notes > 0 else ""),
                            key="chk_confirm_delete_folder",
                        )

                        if st.button(
                            "Delete Subject Folder",
                            icon=":material/folder_delete:",
                            type="primary",
                            disabled=not confirm_del_folder,
                            key="btn_execute_delete_folder",
                            use_container_width=True,
                        ):
                            try:
                                delete_subject_folder(del_subj, delete_contents=True)
                                st.cache_data.clear()
                                if st.session_state.get("author_subject") == del_subj:
                                    st.session_state["author_subject"] = "General"
                                if st.session_state.get("selected_note_path"):
                                    for dn in del_folder_notes:
                                        if dn["path"] == st.session_state["selected_note_path"]:
                                            st.session_state["selected_note_path"] = None
                                            break
                                if del_subj in st.session_state.get("vault_collapsed_folders", set()):
                                    st.session_state["vault_collapsed_folders"].discard(del_subj)
                                st.toast(f"Deleted folder '{del_subj}'!", icon=":material/delete:")
                                st.success(f"Permanently deleted subject folder `{del_subj}` and its contents.", icon=":material/check_circle:")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to delete folder: {e}", icon=":material/error:")

                # Auto-Organize Flat Notes
                with st.container(border=True):
                    st.markdown("#### :material/auto_fix_high: Auto-Organize Flat Notes")
                    st.caption(
                        "Scans for any unclassified Markdown notes sitting directly in root `data/notes/` "
                        "and moves them into subject folders based on content headers."
                    )
                    if st.button("Run Auto-Classification", icon=":material/auto_fix_high:", key="btn_run_organize_flat", use_container_width=True):
                        moved_files = organize_notes_into_subject_folders()
                        st.cache_data.clear()
                        if moved_files:
                            st.toast(f"Auto-classified {len(moved_files)} note(s)!", icon=":material/check:")
                            st.success(f"Classified and moved {len(moved_files)} note(s) into subject trees:", icon=":material/check_circle:")
                            for old_f, new_f in moved_files.items():
                                st.write(f"- `{old_f}` &rarr; `{new_f}`")
                        else:
                            st.info("All notes are already classified into subject folder trees!", icon=":material/info:")
                        st.rerun()

        # ---------------------------------------------------------------------
        # SUB-TAB 3: VAULT DIRECTORY BREAKDOWN & HEALTH
        # ---------------------------------------------------------------------
        with tab_m_tree:
            st.markdown("#### :material/account_tree: Vault Subject Tree Directory Breakdown")
            st.caption("Visual inspection of all subject directories, note allocations, file sizes, and LaTeX derivations.")

            tree_info = get_subject_folder_tree()
            all_tree_notes = list_study_notes(recursive=True)
            total_vault_kb = sum(n["size_bytes"] for n in all_tree_notes) / 1024
            all_academic_notes = [n for notes in tree_info.values() for n in notes]
            total_math_notes = sum(1 for n in all_academic_notes if n.get("has_math"))

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Subject Folders", len(tree_info))
            m2.metric("Total Notes", len(all_tree_notes))
            m3.metric("Vault Storage", f"{round(total_vault_kb, 1)} KB")
            m4.metric("LaTeX Notes", total_math_notes)

            st.markdown("---")
            if not tree_info and not all_tree_notes:
                st.info("Vault is currently empty.", icon=":material/info:")
            else:
                for s_name, n_list in tree_info.items():
                    s_size = sum(n["size_bytes"] for n in n_list)
                    with st.expander(f"{s_name} — {len(n_list)} note(s) ({round(s_size / 1024, 1)} KB)", expanded=len(n_list) > 0, icon=":material/folder:"):
                        if not n_list:
                            st.caption("This subject folder is currently empty.")
                        else:
                            for n in n_list:
                                c_info, c_act = st.columns([3.5, 1])
                                with c_info:
                                    math_badge = '<span class="material-symbols-rounded" style="font-size: 0.8rem; vertical-align: -1px; margin-left: 4px;">functions</span> LaTeX' if n.get("has_math") else ""
                                    words_str = f"{n.get('word_count', 0)} words"
                                    st.markdown(
                                        f"<span class=\"material-symbols-rounded\" style=\"font-size: 0.95rem; vertical-align: -1px; margin-right: 4px;\">description</span><strong>{n.get('title', n['name'])}</strong> (`{n['name']}`)  \n"
                                        f"<span style='font-size: 0.78rem; color: rgba(156,163,175,0.85);'>"
                                        f"{words_str} &bull; {round(n['size_bytes'] / 1024, 1)} KB &bull; Modified: {n['modified_at']} {math_badge}</span>",
                                        unsafe_allow_html=True,
                                    )
                                with c_act:
                                    if st.button("Open in Vault", icon=":material/open_in_new:", key=f"btn_open_tree_{s_name}_{n['name']}", use_container_width=True):
                                        st.session_state["selected_note_path"] = n["path"]
                                        st.toast(f"Selected '{n['name']}' in Explorer!", icon=":material/check:")
                                        st.info("Note selected. Switch to the :material/folder_open: Subject Vault & Tree Explorer tab above to view.", icon=":material/info:")

# ==============================================================================
# VIEW 5: SETTINGS & SYSTEM MANAGEMENT
# ==============================================================================
elif active_tab == "Settings":
    current_vault_path = get_notes_vault_dir()
    v_stats = get_vault_stats(current_vault_path)
    all_saved_blocks_settings = load_study_blocks()
    all_courses_settings = load_courses()

    # Hero Card
    st.markdown(
        f"""
<div class="minimal-hero-card">
    <div class="page-heading-row">
        <div>
            <h2 class="minimal-hero-title"><span class="material-symbols-rounded" aria-hidden="true">settings</span> Settings</h2>
            <p class="minimal-hero-sub">
                Manage vault storage, backups, and date and time formats
            </p>
        </div>
        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span class="badge-exam"><span class="material-symbols-rounded" style="font-size: 0.85rem; vertical-align: -0.1em; margin-right: 4px;">calendar_today</span>{formatted_today}</span>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    settings_tabs = st.tabs([
        ":material/folder_zip: Storage & Backup",
        ":material/schedule: Date & Time Formatting",
        ":material/mop: Orphaned Data Cleanup",
        ":material/dns: Local AI & Wikipedia",
    ])

    # --------------------------------------------------------------------------
    # TAB 1: VAULT DIRECTORY MAPPING & GLOBAL BACKUP
    # --------------------------------------------------------------------------
    with settings_tabs[0]:
        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        col_v1, col_v2 = st.columns([1.5, 1.5], gap="large")

        with col_v1:
            st.markdown("#### :material/folder_open: Study data locations")
            st.caption("Set the study data folder to switch all default paths together. Leave individual fields blank to use their defaults, or enter absolute paths or paths relative to the study data folder.")
            fields = {
                "data_dir": ("Study data folder", "example-study-data"),
                "notes_vault_dir": ("Markdown notes folder", "example-notes/"),
                "courses_file": ("Courses JSON file", "example-courses.json"),
                "sessions_dir": ("Study sessions folder", "example-sessions/"),
                "legacy_study_blocks_file": ("Legacy study blocks JSON file", "example-study-blocks.json"),
                "study_db_file": ("SQLite database file", "example-notes/example-study.db"),
            }
            with st.form("study_data_locations"):
                new_paths = {
                    key: st.text_input(label, value=app_settings.get(key) or "", placeholder=example).strip()
                    for key, (label, example) in fields.items()
                }
                if st.form_submit_button("Save storage paths", type="primary"):
                    try:
                        validate_storage_paths({**app_settings, **new_paths})
                        save_settings(new_paths)
                    except (OSError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()

            st.caption(f"{v_stats['subject_count']} subjects ? {v_stats['note_count']} Markdown notes")

        with col_v2:
            st.markdown("#### :material/archive: Export study data")
            st.caption("Download a ZIP of the configured course records, study sessions, legacy blocks, and Markdown notes. Store backups privately.")

            backup_filename = f"study-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
            zip_bytes, arc_stats = create_vault_archive_zip(notes_dir=current_vault_path)

            st.download_button(
                label="Download Vault Archive",
                data=zip_bytes,
                file_name=backup_filename,
                mime="application/zip",
                icon=":material/download:",
                type="primary",
                use_container_width=True,
                key="btn_download_vault_archive",
                help="Downloads a timestamped ZIP archive containing all Markdown notes and data JSON files.",
            )
            st.caption(
                f":material/info: Archive Size: **{round(arc_stats['archive_size_bytes'] / 1024, 1)} KB** "
                f"({arc_stats['notes_count']} notes, {arc_stats['json_count']} JSON files &bull; Uncompressed: {round(arc_stats['uncompressed_bytes'] / 1024, 1)} KB)"
            )

    # --------------------------------------------------------------------------
    # TAB 2: DATE & TIME FORMATTING
    # --------------------------------------------------------------------------
    with settings_tabs[1]:
        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        st.markdown("#### :material/tune: Date & Time Formatting")
        st.caption("A global toggle between 24-hour and 12-hour clocks, and preferred date formats (YYYY-MM-DD vs. DD/MM/YYYY) that automatically updates all dataframes and chart axis labels.")

        f_col1, f_col2 = st.columns(2, gap="large")

        with f_col1:
            st.markdown("##### :material/schedule: Clock Display Format")
            time_fmt_options = ["24-Hour (14:30)", "12-Hour (02:30 PM)"]
            curr_time_idx = 1 if time_fmt == "12h" else 0
            selected_time_label = st.radio(
                "Clock Format",
                options=time_fmt_options,
                index=curr_time_idx,
                horizontal=True,
                key="radio_settings_time_fmt",
                label_visibility="collapsed",
            )
            new_time_val = "12h" if "12-Hour" in selected_time_label else "24h"

        with f_col2:
            st.markdown("##### :material/calendar_today: Preferred Date Format")
            date_fmt_options = ["YYYY-MM-DD (2026-10-19)", "DD/MM/YYYY (19/10/2026)"]
            curr_date_idx = 1 if date_fmt == "DD/MM/YYYY" else 0
            selected_date_label = st.radio(
                "Date Format",
                options=date_fmt_options,
                index=curr_date_idx,
                horizontal=True,
                key="radio_settings_date_fmt",
                label_visibility="collapsed",
            )
            new_date_val = "DD/MM/YYYY" if "DD/MM/YYYY" in selected_date_label else "YYYY-MM-DD"

        # Apply settings if toggled
        if new_time_val != time_fmt or new_date_val != date_fmt:
            save_settings({"time_format": new_time_val, "date_format": new_date_val})
            st.session_state["app_settings"]["time_format"] = new_time_val
            st.session_state["app_settings"]["date_format"] = new_date_val
            st.toast(f"Display format updated: {new_time_val} clock & {new_date_val} date format!", icon=":material/check_circle:")
            time.sleep(0.25)
            st.rerun()

        st.markdown("<div style='margin-top: 16px;'></div>", unsafe_allow_html=True)
        st.markdown("##### :material/preview: Live Preview: Dataframe & Chart Axis Labels")

        # Preview Metric strip
        now_dt = datetime.now()
        prev_c1, prev_c2, prev_c3, prev_c4 = st.columns(4)
        prev_c1.metric("Formatted Date", format_date(now_dt, date_fmt))
        prev_c2.metric("Formatted Time", format_time(now_dt.time(), time_fmt))
        prev_c3.metric("Sample Session Window", f"{format_time('14:00', time_fmt)} – {format_time('16:00', time_fmt)}")
        prev_c4.metric("Active Clock Standard", "12-Hour (AM/PM)" if time_fmt == "12h" else "24-Hour (Military/ISO)")

        # Live Chart & DataFrame preview reflecting format choices
        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        ch_col, df_col = st.columns([1.5, 1.5], gap="medium")

        with ch_col:
            st.caption(f":material/show_chart: Altair Chart X-Axis Labels dynamically rendered with format `{date_fmt}`:")
            sample_chart_dates = [format_date(now_dt - timedelta(days=6 - i), date_fmt) for i in range(7)]
            sample_chart_mins = [30, 45, 60, 45, 90, 75, 120]
            df_preview_chart = pd.DataFrame({"Date": sample_chart_dates, "Minutes": sample_chart_mins})

            preview_alt_chart = (
                alt.Chart(df_preview_chart)
                .mark_line(color="#818cf8", strokeWidth=3, point=alt.OverlayMarkDef(color="#818cf8", size=55))
                .encode(
                    x=alt.X("Date:N", sort=None, axis=alt.Axis(labelAngle=-20, title=f"Timeline Axis ({date_fmt})")),
                    y=alt.Y("Minutes:Q", axis=alt.Axis(title="Study Minutes")),
                    tooltip=[
                        alt.Tooltip("Date:N", title="Date"),
                        alt.Tooltip("Minutes:Q", title="Study Minutes"),
                    ],
                )
                .properties(height=210)
                .configure_view(strokeWidth=0)
            )
            st.altair_chart(preview_alt_chart, use_container_width=True, theme="streamlit")

        with df_col:
            st.caption(f":material/table_chart: Tabular DataFrame dynamically updated with `{date_fmt}` & `{time_fmt}`:")
            sample_df_data = [
                {
                    "Session Date": format_date(now_dt - timedelta(days=2), date_fmt),
                    "Time Window": f"{format_time('09:00', time_fmt)} – {format_time('10:45', time_fmt)}",
                    "Course Topic": "CS-C3240 Machine Learning",
                    "Status": "Completed",
                },
                {
                    "Session Date": format_date(now_dt - timedelta(days=1), date_fmt),
                    "Time Window": f"{format_time('14:15', time_fmt)} – {format_time('16:00', time_fmt)}",
                    "Course Topic": "Quantum Mechanics",
                    "Status": "Completed",
                },
                {
                    "Session Date": format_date(now_dt, date_fmt),
                    "Time Window": f"{format_time('17:00', time_fmt)} – {format_time('18:30', time_fmt)}",
                    "Course Topic": "CS-C3150 Software Engineering",
                    "Status": "In Progress",
                },
                {
                    "Session Date": format_date(now_dt + timedelta(days=1), date_fmt),
                    "Time Window": f"{format_time('10:15', time_fmt)} – {format_time('12:00', time_fmt)}",
                    "Course Topic": "Industrial Engineering",
                    "Status": "Scheduled",
                },
            ]
            st.dataframe(pd.DataFrame(sample_df_data), use_container_width=True, hide_index=True)

    # --------------------------------------------------------------------------
    # TAB 3: ORPHANED DATA CLEANUP
    # --------------------------------------------------------------------------
    with settings_tabs[2]:
        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        st.markdown("#### :material/mop: Orphaned Data Scanner & Cleanup")
        st.caption("A utility button to scan for and delete study block JSONs that reference courses or notes that have been deleted.")

        orphans_detected = find_orphaned_study_blocks(notes_dir=current_vault_path)

        if orphans_detected:
            st.warning(
                f"**Integrity Warning:** Detected **{len(orphans_detected)} orphaned study block(s)** in the configured storage "
                f"referencing notes or courses that no longer exist.",
                icon=":material/warning:",
            )

            # Display table of orphaned blocks with reasons
            orphan_display_rows = []
            for ob in orphans_detected:
                orphan_display_rows.append({
                    "Block ID": ob["block_id"],
                    "Topic": ob["topic"],
                    "Session Date": format_date(ob["session_date"], date_fmt),
                    "Orphaned Reason(s)": " • ".join(ob["reasons"]),
                })

            st.dataframe(pd.DataFrame(orphan_display_rows), use_container_width=True, hide_index=True)

            del_orphan_col1, del_orphan_col2 = st.columns([1.6, 2.4])
            with del_orphan_col1:
                if st.button(
                    f"Delete {len(orphans_detected)} Orphaned Study Block(s)",
                    type="primary",
                    icon=":material/delete_forever:",
                    key="btn_purge_orphans_action",
                    use_container_width=True,
                ):
                    cleaned_num = cleanup_orphaned_study_blocks(orphans_detected)
                    # Discard from active timeline session states
                    for ob in orphans_detected:
                        st.session_state.get("active_study_block_ids", set()).discard(ob["block_id"])
                        st.session_state.get("manually_reinstated_block_ids", set()).discard(ob["block_id"])
                    st.cache_data.clear()
                    st.toast(f"Successfully deleted {cleaned_num} orphaned study block file(s)!", icon=":material/delete:")
                    time.sleep(0.3)
                    st.rerun()
            with del_orphan_col2:
                st.caption(
                    ":material/info: Clicking delete removes these obsolete JSON session files permanently and updates your schedule registries."
                )
        else:
            st.success(
                f"**Data Integrity Verified:** All {len(all_saved_blocks_settings)} study block records in the configured storage "
                f"are properly linked to existing Markdown notes and valid registered courses.",
                icon=":material/check_circle:",
            )
            st.markdown(
                f"""
<div style="background: rgba(16, 185, 129, 0.05); border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 10px; padding: 14px 18px; margin-top: 8px;">
    <div style="font-weight: 600; font-size: 0.92rem; color: #34d399; margin-bottom: 4px;">
        Zero Orphaned Study Block Files Detected
    </div>
    <div style="font-size: 0.8rem; color: rgba(156, 163, 175, 0.9); line-height: 1.45;">
        Checked the session folder and legacy block file against
        <strong>{v_stats['note_count']} Markdown notes</strong> and
        <strong>{len(all_courses_settings)} courses</strong>.
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
            if st.button("Re-scan Now", icon=":material/refresh:", key="btn_rescan_orphans"):
                st.toast("Registry scan complete: 0 orphaned blocks found.", icon=":material/check:")

    # --------------------------------------------------------------------------
    # TAB 4: LOCAL AI AND OPTIONAL WIKIPEDIA
    # --------------------------------------------------------------------------
    with settings_tabs[3]:
        st.markdown("#### Ollama")
        st.caption("The tutor and note authoring use this model. Prompts can include excerpts from your Markdown notes and are sent to this endpoint.")
        with st.form("local_ai_settings"):
            new_url = st.text_input("Ollama base URL", value=ollama_url).strip().rstrip("/")
            new_model = st.text_input("Ollama model", value=ollama_model).strip()
            use_tutor_notes = st.checkbox("Use Markdown notes in tutor replies", value=tutor_notes_enabled)
            st.markdown("#### Offline Wikipedia")
            use_wikipedia = st.checkbox("Enable offline Wikipedia", value=bool(app_settings.get("wikipedia_enabled")))
            new_zim_path = st.text_input("Wikipedia ZIM file", value=app_settings.get("zim_path") or "", placeholder="archives/example-wikipedia.zim").strip()
            if st.form_submit_button("Save AI settings", type="primary"):
                try:
                    parsed_url = urlsplit(new_url)
                    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username is not None or parsed_url.query or parsed_url.fragment:
                        raise ValueError("Enter an HTTP or HTTPS Ollama base URL without credentials, a query, or a fragment.")
                    if not new_model:
                        raise ValueError("Enter the name of an installed Ollama model.")
                    save_settings({
                        "ollama_url": new_url,
                        "ollama_model": new_model,
                        "tutor_notes_enabled": use_tutor_notes,
                        "wikipedia_enabled": use_wikipedia,
                        "zim_path": new_zim_path,
                    })
                except (OSError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

        if wiki_available:
            st.success("Wikipedia archive ready.")
        elif app_settings.get("wikipedia_enabled"):
            st.warning("Wikipedia archive unavailable. Check the ZIM path and install the optional Wikipedia requirements. The tutor can still use Ollama and your notes.")
        else:
            st.caption("Wikipedia is off. The tutor can use Ollama without an archive.")
