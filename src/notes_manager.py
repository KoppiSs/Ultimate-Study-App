"""
Notes Manager Module.

Provides decoupled, secure file I/O and persistence for local study notes and
session plans strictly using local Markdown (.md) files organized under
subject-related folder trees.
All SQLite database logic has been completely removed.
"""

from datetime import datetime
import glob
import os
import re
import shutil
from typing import Any, Dict, List, Optional, Union
from scheduler import StudyBlockSchedule

# Directory Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NOTES_DIR = os.path.join(BASE_DIR, "data", "notes")
NOTES_DIR = os.getenv("NOTES_DIR", DEFAULT_NOTES_DIR)


def get_notes_dir() -> str:
    """
    Returns the active notes vault directory, consulting settings_manager.
    Synchronizes the module-level NOTES_DIR variable.
    """
    global NOTES_DIR
    try:
        from settings_manager import get_notes_vault_dir
        active_vault = get_notes_vault_dir()
        NOTES_DIR = active_vault
        return active_vault
    except Exception:
        return NOTES_DIR


# ==============================================================================
# SANITIZATION & SECURITY HELPERS
# ==============================================================================

def sanitize_filename(name: str, default: str = "study_session") -> str:
    """
    Sanitizes a string to be safely used as a filename.
    Prevents path traversal ('..'), removes invalid characters, and trims length.
    """
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name)
    sanitized = re.sub(r"\.{2,}", "", sanitized)
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = sanitized.strip("._")
    if not sanitized:
        sanitized = default
    return sanitized[:80]


def sanitize_subject_name(name: str, default: str = "General") -> str:
    """
    Sanitizes a string to be safely used as a subject folder name.
    Allows spaces and hyphens, removes path traversal and invalid path characters.
    """
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name)
    sanitized = re.sub(r"\.{2,}", "", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip("._ ")
    if not sanitized:
        sanitized = default
    return sanitized[:60]


def is_path_safe(filepath: str, base_dir: Optional[str] = None) -> bool:
    """
    Verifies that a given file path is strictly contained within the notes base directory.
    Prevents directory traversal attacks.
    """
    target_base = os.path.abspath(base_dir or get_notes_dir())
    target_file = os.path.abspath(filepath)
    return target_file.startswith(target_base)


def sanitize_latex_markdown(text: str) -> str:
    """
    Sanitizes and normalizes LaTeX math expressions in markdown notes to guarantee
    flawless KaTeX / Streamlit rendering:
    1. Converts LaTeX display brackets '\\[ ... \\]' to '$$ ... $$'.
    2. Converts LaTeX inline brackets '\\( ... \\)' to '$ ... $'.
    3. Normalizes existing $$ ... $$ blocks so KaTeX-incompatible environments (align*, align, equation)
       are safely converted to 'aligned'.
    4. Wraps unwrapped environments (align*, align, aligned, equation, equation*) in $$ ... $$ blocks.
    5. Wraps gather/gathered environments in $$ ... $$ blocks.
    6. Ensures proper spacing around display equations so markdown block parsers treat them as math blocks.
    7. Protects fenced code blocks (```...```) and inline code (`...`) from modification.
    """
    if not text:
        return text

    # Protect fenced code blocks
    code_blocks: List[str] = []
    def save_code_block(match: re.Match) -> str:
        code_blocks.append(match.group(0))
        return f"@@@CODE_BLOCK_{len(code_blocks)-1}@@@"

    processed = re.sub(r"```[\s\S]*?```", save_code_block, text)

    # Protect inline code spans
    inline_spans: List[str] = []
    def save_inline_code(match: re.Match) -> str:
        inline_spans.append(match.group(0))
        return f"@@@INLINE_CODE_{len(inline_spans)-1}@@@"

    processed = re.sub(r"`[^`\n]+`", save_inline_code, processed)

    # 1. Convert \[ ... \] display brackets to $$ ... $$
    processed = re.sub(r"\\\[\s*([\s\S]*?)\s*\\\]", r"\n\n$$\n\1\n$$\n\n", processed)

    # 2. Convert \( ... \) inline brackets to $ ... $
    processed = re.sub(r"\\\(\s*([\s\S]*?)\s*\\\)", r"$\1$", processed)

    # 3. Process existing $$ ... $$ display math blocks:
    # If it contains KaTeX-incompatible environments (align*, align, equation), convert them to aligned.
    display_math_blocks: List[str] = []
    def save_and_normalize_display_math(match: re.Match) -> str:
        inner = match.group(1)
        if re.search(r"\\begin\{(?:align\*?|equation\*?)\}", inner):
            inner = re.sub(r"\\begin\{(?:align\*?|equation\*?)\}", r"\\begin{aligned}", inner)
            inner = re.sub(r"\\end\{(?:align\*?|equation\*?)\}", r"\\end{aligned}", inner)
            display_math_blocks.append(f"$$\n{inner.strip()}\n$$")
        else:
            display_math_blocks.append(match.group(0))
        return f"@@@DISPLAY_MATH_{len(display_math_blocks)-1}@@@"

    processed = re.sub(r"\$\$([\s\S]*?)\$\$", save_and_normalize_display_math, processed)

    # 4. Convert remaining unwrapped LaTeX environments (align*, align, aligned, equation, equation*)
    # into $$\begin{aligned} ... \end{aligned}$$
    target_envs = r"align\*?|aligned|equation\*?"
    unwrapped_pattern = re.compile(
        r"[ \t]*\\begin\{(" + target_envs + r")\}([\s\S]*?)\\end\{\1\}[ \t]*",
        re.MULTILINE
    )

    def wrap_unwrapped_aligned(match: re.Match) -> str:
        body = match.group(2).strip()
        return f"\n\n$$\n\\begin{{aligned}}\n{body}\n\\end{{aligned}}\n$$\n\n"

    processed = unwrapped_pattern.sub(wrap_unwrapped_aligned, processed)

    # 5. Handle gather/gathered environments
    gather_pattern = re.compile(
        r"[ \t]*\\begin\{(gather\*?|gathered)\}([\s\S]*?)\\end\{\1\}[ \t]*",
        re.MULTILINE
    )

    def wrap_unwrapped_gather(match: re.Match) -> str:
        body = match.group(2).strip()
        return f"\n\n$$\n\\begin{{gathered}}\n{body}\n\\end{{gathered}}\n$$\n\n"

    processed = gather_pattern.sub(wrap_unwrapped_gather, processed)

    # Restore display math blocks
    for i, math_block in enumerate(display_math_blocks):
        processed = processed.replace(f"@@@DISPLAY_MATH_{i}@@@", math_block)

    # Restore inline code spans
    for i, span in enumerate(inline_spans):
        processed = processed.replace(f"@@@INLINE_CODE_{i}@@@", span)

    # Restore fenced code blocks
    for i, block in enumerate(code_blocks):
        processed = processed.replace(f"@@@CODE_BLOCK_{i}@@@", block)

    return processed


# ==============================================================================
# FRONTMATTER & METADATA PARSING
# ==============================================================================

def parse_note_metadata(content: str, fallback_filename: str = "") -> Dict[str, Any]:
    """
    Parses YAML frontmatter and Markdown headers into a structured metadata dictionary.
    Handles both modern YAML frontmatter and legacy Markdown headers.
    """
    meta: Dict[str, Any] = {
        "title": "",
        "subject": "General",
        "difficulty": 3,
        "exam_date": None,
        "tags": [],
        "created_at": None,
        "updated_at": None,
        "raw_body": content,
        "has_math": bool(re.search(r"\$\$|\$[^$]+\$|\\frac|\\int|\\sum|\\begin\{|\\hat|\\mathbf|\\mathbb", content)),
    }

    # 1. Parse YAML frontmatter if present
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        meta["raw_body"] = fm_match.group(2)
        try:
            import yaml
            parsed = yaml.safe_load(fm_text)
            if isinstance(parsed, dict):
                for k in ["title", "subject", "difficulty", "exam_date", "tags", "created_at", "updated_at"]:
                    if k in parsed and parsed[k] is not None:
                        meta[k] = parsed[k]
        except Exception:
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip(), v.strip()
                    if k in meta:
                        meta[k] = v

    # 2. Extract Title fallback
    if not meta["title"]:
        topic_match = re.search(r"^#\s*(?:Topic):\s*(.+)$", content, re.MULTILINE)
        if topic_match:
            meta["title"] = topic_match.group(1).strip()
        else:
            h1_match = re.search(r"^#\s+([^#\n]+)$", content, re.MULTILINE)
            if h1_match and not h1_match.group(1).strip().startswith("Study Session"):
                meta["title"] = h1_match.group(1).strip()
            else:
                base_name = os.path.splitext(os.path.basename(fallback_filename))[0] if fallback_filename else "Untitled Note"
                meta["title"] = base_name.replace("_", " ").title()

    # 3. Extract Subject/Course fallback
    if not meta.get("subject") or meta["subject"] == "General":
        course_match = re.search(r"^##\s*(?:Course|Subject):\s*(.+)$", content, re.MULTILINE)
        if course_match:
            meta["subject"] = course_match.group(1).strip()
        elif meta.get("title") and meta["title"] != "Untitled Note":
            meta["subject"] = meta["title"]

    # 4. Extract Difficulty fallback
    diff_match = re.search(r"^##\s*Difficulty:\s*(\d+)", content, re.MULTILINE)
    if diff_match:
        try:
            meta["difficulty"] = int(diff_match.group(1))
        except ValueError:
            pass

    # 5. Extract Exam Date fallback
    if not meta.get("exam_date"):
        exam_match = re.search(r"^##\s*Target Exam:\s*(.+)$", content, re.MULTILINE)
        if exam_match:
            meta["exam_date"] = exam_match.group(1).strip()

    # 6. Extract Tags fallback if empty
    if not meta["tags"]:
        tag_matches = re.findall(r"#([a-zA-Z0-9_\-]+)", content)
        if tag_matches:
            meta["tags"] = list(dict.fromkeys(tag_matches[:6]))

    # 7. Normalize raw body LaTeX expressions for KaTeX compatibility
    meta["raw_body"] = sanitize_latex_markdown(meta.get("raw_body", content))

    return meta


def format_note_with_frontmatter(
    title: str,
    subject: str,
    body: str,
    difficulty: int = 3,
    exam_date: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> str:
    """
    Formats note content with clean YAML frontmatter and standard academic headers.
    Ensures body LaTeX math is fully sanitized and KaTeX-compatible.
    """
    clean_tags = [t.strip().lstrip("#").lower() for t in (tags or []) if t.strip()]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # If body already contains frontmatter, strip it first
    body_stripped = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", body.strip(), flags=re.DOTALL)
    body_stripped = sanitize_latex_markdown(body_stripped)

    tags_yaml = f"[{', '.join(clean_tags)}]" if clean_tags else "[]"
    exam_val = f'"{exam_date}"' if exam_date else "null"

    frontmatter = f"""---
title: "{title}"
subject: "{subject}"
difficulty: {difficulty}
exam_date: {exam_val}
tags: {tags_yaml}
created_at: "{now_str}"
updated_at: "{now_str}"
---

"""
    return frontmatter + body_stripped.strip() + "\n"


# ==============================================================================
# SUBJECT FOLDER TREE OPERATIONS
# ==============================================================================

def create_subject_folder(subject_name: str, notes_dir: Optional[str] = None) -> str:
    """
    Creates a new subject folder inside data/notes/ with sanitization.
    Returns the absolute path to the subject directory.
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())
    clean_subj = sanitize_subject_name(subject_name)
    folder_path = os.path.join(target_base, clean_subj)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def list_subject_folders(notes_dir: Optional[str] = None) -> List[str]:
    """
    Returns a sorted list of all subject folder names in the notes directory.
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())
    if not os.path.exists(target_base):
        return []

    folders = []
    for entry in os.scandir(target_base):
        if entry.is_dir() and not entry.name.startswith("."):
            folders.append(entry.name)

    return sorted(folders)


def get_subject_folder_tree(notes_dir: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Scans the notes directory and returns a full tree classifying all notes
    under their respective subject folder trees.
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())
    if not os.path.exists(target_base):
        os.makedirs(target_base, exist_ok=True)

    tree: Dict[str, List[Dict[str, Any]]] = {}

    # Initialize with all physical subdirectories
    for subj_folder in list_subject_folders(target_base):
        tree[subj_folder] = []

    # Walk all files recursively
    for root, dirs, files in os.walk(target_base):
        # Exclude hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        rel_dir = os.path.relpath(root, target_base)
        current_subject = None if rel_dir == "." else rel_dir.split(os.sep)[0]

        for file in files:
            if not file.endswith(".md"):
                continue

            full_path = os.path.join(root, file)
            try:
                stat = os.stat(full_path)
            except OSError:
                continue
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError):
                content = ""

            # Exclude study session schedule files from academic course notes
            if re.search(r"^#\s*Study Session:|\bSession Schedule\b", content, re.MULTILINE):
                continue

            meta = parse_note_metadata(content, fallback_filename=file)
            subject_key = current_subject or meta.get("subject") or "General"
            clean_subject = sanitize_subject_name(subject_key)

            if clean_subject not in tree:
                tree[clean_subject] = []

            # Word count and formula detection
            words = len(content.split())
            has_math = bool(re.search(r"\$\$|\$[^$]+\$|\\frac|\\int|\\sum|\\begin\{|\\hat|\\mathbf|\\mathbb", content))

            tree[clean_subject].append({
                "name": file,
                "title": meta.get("title") or file,
                "path": full_path,
                "relative_path": os.path.relpath(full_path, target_base),
                "subject": clean_subject,
                "difficulty": meta.get("difficulty", 3),
                "exam_date": meta.get("exam_date"),
                "tags": meta.get("tags", []),
                "size_bytes": stat.st_size,
                "word_count": words,
                "has_math": has_math,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })

    # Sort notes in each subject by modification time (most recent first)
    for subj in tree:
        tree[subj].sort(key=lambda x: x["modified_at"], reverse=True)

    return tree


def organize_notes_into_subject_folders(notes_dir: Optional[str] = None) -> Dict[str, str]:
    """
    Scans for loose/flat Markdown notes sitting directly in the root data/notes/ directory
    and automatically classifies & moves them into their own subject-related folder trees.
    Returns a mapping of {original_filename: new_relative_path}.
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())
    if not os.path.exists(target_base):
        return {}

    moved: Dict[str, str] = {}

    for entry in os.scandir(target_base):
        if entry.is_file() and entry.name.endswith(".md"):
            try:
                with open(entry.path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            meta = parse_note_metadata(content, fallback_filename=entry.name)
            subject = sanitize_subject_name(meta.get("subject") or "General")

            target_folder = os.path.join(target_base, subject)
            os.makedirs(target_folder, exist_ok=True)

            target_path = os.path.join(target_folder, entry.name)

            # If not already in that path, move it
            if os.path.abspath(entry.path) != os.path.abspath(target_path):
                shutil.move(entry.path, target_path)
                rel_path = os.path.relpath(target_path, target_base)
                moved[entry.name] = rel_path

    return moved


def move_note_to_subject_folder(
    note_path: str,
    target_subject: str,
    notes_dir: Optional[str] = None,
) -> str:
    """
    Moves an existing Markdown note file into a different subject folder.
    Updates the YAML frontmatter to reflect the new subject classification.
    Returns the new absolute path of the note.
    """
    if not target_subject or not target_subject.strip() or target_subject.strip() in (".", "/"):
        raise ValueError("Target subject cannot be empty or root.")

    target_base = os.path.abspath(notes_dir or get_notes_dir())
    source_path = os.path.abspath(note_path)

    if not is_path_safe(source_path, target_base):
        raise ValueError(f"Access denied: source path '{source_path}' is outside notes directory.")
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source note file not found: '{source_path}'")

    clean_target_subj = sanitize_subject_name(target_subject)
    target_folder = os.path.join(target_base, clean_target_subj)
    os.makedirs(target_folder, exist_ok=True)

    filename = os.path.basename(source_path)
    destination_path = os.path.join(target_folder, filename)

    # Read existing note content
    with open(source_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse metadata and update subject in frontmatter
    meta = parse_note_metadata(content, fallback_filename=filename)
    note_title = meta.get("title") or os.path.splitext(filename)[0]

    updated_content = format_note_with_frontmatter(
        title=note_title,
        subject=clean_target_subj,
        body=meta["raw_body"],
        difficulty=meta.get("difficulty", 3),
        exam_date=meta.get("exam_date"),
        tags=meta.get("tags", []),
    )

    # Write to new destination
    with open(destination_path, "w", encoding="utf-8") as f:
        f.write(updated_content)

    # Remove original file if moving to a different path
    if os.path.abspath(source_path) != os.path.abspath(destination_path):
        os.remove(source_path)

    return destination_path


def rename_subject_folder(
    old_subject: str,
    new_subject: str,
    notes_dir: Optional[str] = None,
) -> str:
    """
    Renames a subject folder and updates the frontmatter of all Markdown notes
    contained inside to reference the new subject name.
    Returns the absolute path to the renamed subject folder.
    """
    if not old_subject or not old_subject.strip() or old_subject.strip() in (".", "/"):
        raise ValueError("Cannot rename root or empty subject folder.")
    if not new_subject or not new_subject.strip() or new_subject.strip() in (".", "/"):
        raise ValueError("New subject folder name cannot be empty or root.")

    target_base = os.path.abspath(notes_dir or get_notes_dir())
    clean_old = sanitize_subject_name(old_subject)
    clean_new = sanitize_subject_name(new_subject)

    old_folder = os.path.join(target_base, clean_old)
    new_folder = os.path.join(target_base, clean_new)

    if not is_path_safe(old_folder, target_base) or not is_path_safe(new_folder, target_base):
        raise ValueError("Access denied: folder path outside notes directory.")
    if os.path.abspath(old_folder) == target_base or os.path.abspath(new_folder) == target_base:
        raise ValueError("Cannot rename the root notes directory.")
    if not os.path.isdir(old_folder):
        raise FileNotFoundError(f"Subject folder '{clean_old}' does not exist.")

    if clean_old == clean_new:
        return old_folder

    if os.path.exists(new_folder):
        # Merge contents if destination directory already exists
        for entry in os.scandir(old_folder):
            dest_item = os.path.join(new_folder, entry.name)
            if entry.is_file():
                if os.path.exists(dest_item):
                    os.remove(dest_item)
                shutil.move(entry.path, dest_item)
            elif entry.is_dir():
                shutil.move(entry.path, dest_item)
        os.rmdir(old_folder)
    else:
        os.rename(old_folder, new_folder)

    # Update subject field in frontmatter of all .md files in the new folder
    for root, _, files in os.walk(new_folder):
        for file in files:
            if file.endswith(".md"):
                file_p = os.path.join(root, file)
                try:
                    with open(file_p, "r", encoding="utf-8") as f:
                        file_content = f.read()
                    file_meta = parse_note_metadata(file_content, fallback_filename=file)
                    new_fm_content = format_note_with_frontmatter(
                        title=file_meta.get("title") or os.path.splitext(file)[0],
                        subject=clean_new,
                        body=file_meta["raw_body"],
                        difficulty=file_meta.get("difficulty", 3),
                        exam_date=file_meta.get("exam_date"),
                        tags=file_meta.get("tags", []),
                    )
                    with open(file_p, "w", encoding="utf-8") as f:
                        f.write(new_fm_content)
                except Exception:
                    pass

    return new_folder


def delete_subject_folder(
    subject_name: str,
    delete_contents: bool = True,
    notes_dir: Optional[str] = None,
) -> bool:
    """
    Deletes a subject folder directory.
    If delete_contents is True, recursively removes all notes inside.
    If delete_contents is False, raises an error if the directory contains files.
    Returns True on success.
    """
    if not subject_name or not subject_name.strip() or subject_name.strip() in (".", "/"):
        raise ValueError("Cannot delete root or empty subject folder.")

    target_base = os.path.abspath(notes_dir or get_notes_dir())
    clean_subj = sanitize_subject_name(subject_name)
    folder_path = os.path.join(target_base, clean_subj)

    if not is_path_safe(folder_path, target_base):
        raise ValueError(f"Access denied: path '{folder_path}' is outside notes directory.")
    if os.path.abspath(folder_path) == target_base:
        raise ValueError("Cannot delete the root notes directory.")
    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"Subject folder '{clean_subj}' does not exist.")

    if not delete_contents:
        has_items = any(os.scandir(folder_path))
        if has_items:
            raise ValueError(f"Subject folder '{clean_subj}' is not empty.")
        os.rmdir(folder_path)
    else:
        shutil.rmtree(folder_path)

    return True


# ==============================================================================
# NOTE PERSISTENCE (Strictly File-Based, Decoupled I/O)
# ==============================================================================

def save_course_note_file(
    subject: str,
    title: str,
    content: str,
    filename: Optional[str] = None,
    difficulty: int = 3,
    exam_date: Optional[str] = None,
    tags: Optional[List[str]] = None,
    notes_dir: Optional[str] = None,
    overwrite: bool = True,
) -> str:
    """
    Saves a course study note directly into its subject-related folder tree
    (data/notes/<Subject>/<filename>.md).
    Formats with YAML frontmatter for robust indexing.
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())
    clean_subj = sanitize_subject_name(subject)
    folder_path = os.path.join(target_base, clean_subj)
    os.makedirs(folder_path, exist_ok=True)

    if filename is None:
        file_slug = sanitize_filename(title.lower())
        final_filename = f"{file_slug}.md"
    else:
        clean_name = sanitize_filename(filename)
        final_filename = clean_name if clean_name.endswith(".md") else f"{clean_name}.md"

    filepath = os.path.join(folder_path, final_filename)

    # Format content with frontmatter
    formatted_content = format_note_with_frontmatter(
        title=title,
        subject=clean_subj,
        body=content,
        difficulty=difficulty,
        exam_date=exam_date,
        tags=tags,
    )

    mode = "w" if overwrite else "a"
    with open(filepath, mode, encoding="utf-8") as f:
        f.write(formatted_content)

    return filepath


def delete_study_note(filepath: str, notes_dir: Optional[str] = None) -> bool:
    """
    Safely deletes a note file after validating boundary constraints.
    Prevents path traversal.
    """
    safe_path = os.path.abspath(filepath)
    if not is_path_safe(safe_path, notes_dir):
        raise ValueError(f"Access denied: path '{safe_path}' is outside notes directory.")

    if os.path.exists(safe_path) and os.path.isfile(safe_path):
        os.remove(safe_path)
        return True
    return False


def load_study_note(filepath: str) -> str:
    """
    Safely reads and returns the text content of a Markdown note.
    """
    safe_path = os.path.abspath(filepath)
    if not os.path.exists(safe_path):
        raise FileNotFoundError(f"Note file not found: {safe_path}")

    with open(safe_path, "r", encoding="utf-8") as f:
        return f.read()


def list_study_notes(notes_dir: Optional[str] = None, recursive: bool = True) -> List[Dict[str, Any]]:
    """
    Lists all Markdown study notes in the notes directory (recursively across subject folders).
    Returns metadata sorted by modification time (most recent first).
    """
    target_dir = os.path.abspath(notes_dir or get_notes_dir())
    if not os.path.exists(target_dir):
        return []

    notes: List[Dict[str, Any]] = []

    if recursive:
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.endswith(".md"):
                    full_p = os.path.join(root, f)
                    stat = os.stat(full_p)
                    rel_p = os.path.relpath(full_p, target_dir)
                    subj = rel_p.split(os.sep)[0] if os.sep in rel_p else "General"
                    notes.append({
                        "name": f,
                        "path": full_p,
                        "relative_path": rel_p,
                        "subject": subj,
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    })
    else:
        for entry in os.scandir(target_dir):
            if entry.is_file() and entry.name.endswith(".md"):
                stat = entry.stat()
                notes.append({
                    "name": entry.name,
                    "path": entry.path,
                    "relative_path": entry.name,
                    "subject": "General",
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })

    notes.sort(key=lambda x: x["modified_at"], reverse=True)
    return notes


def search_markdown_notes(
    query: str,
    notes_dir: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Pure file-based search over all Markdown notes across all subject folder trees.
    Scores notes with keyword matching, title/topic weighting, tag bonuses, and exact phrase matching.
    Returns full note body in 'content' to support accurate AI evaluation and tutoring.
    """
    target_dir = os.path.abspath(notes_dir or get_notes_dir())
    if not os.path.exists(target_dir):
        return []

    stopwords = {
        "what", "is", "are", "the", "a", "an", "of", "in", "for", "to", "on", "and",
        "my", "notes", "according", "tell", "me", "about", "how", "why", "can", "you",
        "does", "explain", "describe", "main", "some", "give", "list", "show", "with",
        "from", "that", "this", "its", "please"
    }

    raw_words = [w.lower() for w in re.findall(r"\b\w+\b", query) if len(w) > 2]
    keywords = [w for w in raw_words if w not in stopwords]
    if not keywords:
        keywords = raw_words or ([query.lower().strip()] if query.strip() else [])

    if not keywords:
        return []

    clean_phrase = " ".join(keywords)

    results = []
    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file in files:
            if not file.endswith(".md"):
                continue

            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            if re.search(r"^#\s*Study Session:|\bSession Schedule\b", content, re.MULTILINE):
                continue

            meta = parse_note_metadata(content, fallback_filename=file)
            topic = meta.get("title") or os.path.splitext(file)[0]
            course = meta.get("subject") or "General"

            concept_match = re.search(r"###\s*Concept:\s*(.+)$", content, re.MULTILINE)
            concept_name = concept_match.group(1).strip() if concept_match else topic
            tags = [str(t).lower() for t in meta.get("tags", [])]

            lower_content = content.lower()
            lower_topic = topic.lower()
            lower_file = file.lower()

            score = 0
            # Phrase match bonus
            if len(keywords) > 1 and (clean_phrase in lower_topic or clean_phrase in lower_file):
                score += 150
            elif len(keywords) > 1 and clean_phrase in lower_content:
                score += 60

            # Title, filename, and tag keyword matches
            for kw in keywords:
                if kw in lower_topic:
                    score += 35
                if kw in lower_file:
                    score += 25
                if any(kw in tag for tag in tags):
                    score += 20

                # Content keyword occurrences
                count = lower_content.count(kw)
                if count > 0:
                    score += min(count * 2, 40)

            if score > 0:
                raw_body = meta.get("raw_body", content)
                results.append({
                    "topic": topic,
                    "course": course,
                    "concept_name": concept_name,
                    "content": raw_body,
                    "excerpt": raw_body[:300],
                    "filepath": filepath,
                    "score": score,
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    if not results:
        return []

    # Quality filter: avoid incidental low-score noise if top hit is highly relevant
    top_score = results[0]["score"]
    filtered = [r for r in results if r["score"] >= max(10, top_score * 0.25)]
    if not filtered:
        filtered = results
    return filtered[:limit]


def get_all_markdown_notes_hierarchy(notes_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Parses all Markdown notes in data/notes/ across all subject folder trees
    into a Course -> Topic -> Concepts hierarchy.
    """
    target_dir = os.path.abspath(notes_dir or get_notes_dir())
    if not os.path.exists(target_dir):
        return {}

    hierarchy: Dict[str, Dict[str, Any]] = {}

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file in sorted(files):
            if not file.endswith(".md"):
                continue

            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    raw_text = f.read()
            except Exception:
                continue

            if re.search(r"^#\s*Study Session:|\bSession Schedule\b", raw_text, re.MULTILINE):
                continue

            meta = parse_note_metadata(raw_text, fallback_filename=file)
            course = meta.get("subject") or "General"
            topic = meta.get("title") or os.path.splitext(file)[0]
            difficulty = meta.get("difficulty", 3)
            exam_date = meta.get("exam_date")

            if course not in hierarchy:
                hierarchy[course] = {}
            if topic not in hierarchy[course]:
                hierarchy[course][topic] = {
                    "difficulty": difficulty,
                    "exam_date": exam_date,
                    "filepath": filepath,
                    "notes": [],
                }

            # Parse individual concepts if available
            concepts = re.split(r"\n###\s*(?:Concept|📌|📐|🔬|💡|🎯|📚):\s*", raw_text)
            if len(concepts) > 1:
                for section in concepts[1:]:
                    lines = section.strip().split("\n", 1)
                    concept_name = lines[0].strip()
                    body = lines[1].strip() if len(lines) > 1 else ""
                    hierarchy[course][topic]["notes"].append({
                        "concept": concept_name,
                        "content": body,
                        "is_derivation": bool(re.search(r"\$\$|\\frac|\\int|\\psi", body)),
                    })
            else:
                hierarchy[course][topic]["notes"].append({
                    "concept": os.path.basename(filepath),
                    "content": meta.get("raw_body", raw_text),
                    "is_derivation": bool(re.search(r"\$\$|\\frac|\\int|\\psi", raw_text)),
                })

    return hierarchy


# ==============================================================================
# STUDY SESSION & PROTOCOL HELPERS
# ==============================================================================

def extract_single_reference_summary(wiki_summary: Union[Dict[str, Any], str]) -> Dict[str, str]:
    """
    Extracts a clean, single reference summary from Wikipedia query results.
    Guarantees no duplicated paragraphs or repeated concatenations.
    """
    if isinstance(wiki_summary, dict):
        raw_title = wiki_summary.get("title", "")
        raw_source = wiki_summary.get("source", "Offline Wikipedia Archive")
        paragraphs = wiki_summary.get("paragraphs", [])
        raw_summary = wiki_summary.get("summary", "")
        if isinstance(raw_summary, list):
            raw_summary = raw_summary[0] if raw_summary else ""
    else:
        raw_title = ""
        raw_source = "Offline Wikipedia Archive"
        raw_summary = str(wiki_summary)
        paragraphs = []

    if paragraphs and isinstance(paragraphs, list) and len(paragraphs) > 0:
        lead_summary = paragraphs[0].strip()
    elif raw_summary:
        chunks = [p.strip() for p in raw_summary.split("\n\n") if p.strip()]
        lead_summary = chunks[0] if chunks else raw_summary.strip()
    else:
        lead_summary = "No reference summary available."

    paragraphs_seen = set()
    deduped_lines = []
    for line in lead_summary.split("\n"):
        line_clean = line.strip()
        if line_clean and line_clean not in paragraphs_seen:
            paragraphs_seen.add(line_clean)
            deduped_lines.append(line_clean)
    final_summary = " ".join(deduped_lines) if deduped_lines else lead_summary

    return {
        "title": raw_title,
        "source": raw_source,
        "summary": final_summary,
    }


def format_study_session_markdown(
    schedule: StudyBlockSchedule,
    wiki_summary: Union[Dict[str, Any], str],
    additional_notes: Optional[str] = None,
) -> str:
    """
    Formats a structured Markdown document for a study block session.
    """
    topic = schedule.topic
    date_str = schedule.session_date
    start_time = schedule.start_time
    end_time = schedule.end_time
    total_min = schedule.total_duration_minutes
    focus_min = schedule.total_focus_minutes
    break_min = schedule.total_break_minutes

    clean_wiki = extract_single_reference_summary(wiki_summary)
    wiki_title = clean_wiki["title"] or topic
    wiki_source = clean_wiki["source"]
    wiki_text = clean_wiki["summary"]

    focus_intervals = [i for i in schedule.intervals if i.interval_type == "focus"]
    objectives_md = []
    for idx, f_block in enumerate(focus_intervals, 1):
        objectives_md.append(
            f"- [ ] **Interval {idx} ({f_block.start_time} - {f_block.end_time})**: Deep work on {topic} core concepts."
        )
    objectives_str = "\n".join(objectives_md)

    extra_notes_section = (
        additional_notes.strip()
        if additional_notes and additional_notes.strip()
        else "*No personal notes recorded yet. Add formulas, key takeaways, and derivations here.*"
    )

    markdown_doc = f"""# Study Session: {topic}

> **Date:** {date_str}  
> **Session Window:** {start_time} - {end_time} ({total_min} minutes total)  
> **Allocation:** 🎯 {focus_min} min Focus ({schedule.focus_duration_minutes}m intervals) | ☕ {break_min} min Breaks ({schedule.break_duration_minutes}m intervals)  
> **Reference Topic:** {wiki_title} ({wiki_source})

---

## 📅 Session Schedule

{schedule.to_markdown_table()}

---

## 📖 Reference Summary: {wiki_title}

*{wiki_source}*

{wiki_text}

---

## 🎯 Interval Objectives & Checkpoints

{objectives_str}

---

## ✍️ Session Notes & Reflections

{extra_notes_section}
"""
    return markdown_doc


def save_study_session_note(
    schedule: StudyBlockSchedule,
    wiki_summary: Union[Dict[str, Any], str],
    notes_dir: Optional[str] = None,
    filename: Optional[str] = None,
    additional_notes: Optional[str] = None,
    subject: Optional[str] = None,
) -> str:
    """
    Saves the study session schedule and Wikipedia reference summary as a local Markdown note.
    If subject is specified or detected, saves inside the subject folder tree.
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())

    # If subject is explicitly provided, store under subject folder
    target_dir = os.path.join(target_base, sanitize_subject_name(subject)) if subject else target_base
    os.makedirs(target_dir, exist_ok=True)

    if filename is None:
        topic_slug = sanitize_filename(schedule.topic.lower())
        date_slug = sanitize_filename(schedule.session_date)
        time_slug = schedule.start_time.replace(":", "")
        clean_name = f"{date_slug}_{time_slug}_{topic_slug}.md"
    else:
        clean_name = sanitize_filename(filename)
        if not clean_name.endswith(".md"):
            clean_name += ".md"

    filepath = os.path.join(target_dir, clean_name)

    content = format_study_session_markdown(
        schedule=schedule,
        wiki_summary=wiki_summary,
        additional_notes=additional_notes,
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def save_generic_markdown_note(
    topic: str,
    concept_name: str,
    content: str,
    course: str = "General",
    difficulty: int = 1,
    exam_date: Optional[str] = None,
    notes_dir: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> str:
    """
    Creates and saves a structured course/concept Markdown note directly into its
    subject-related folder tree (data/notes/<Course>/<topic>.md).
    """
    target_base = os.path.abspath(notes_dir or get_notes_dir())
    clean_course = sanitize_subject_name(course)
    subject_dir = os.path.join(target_base, clean_course)
    os.makedirs(subject_dir, exist_ok=True)

    topic_slug = sanitize_filename(topic.lower())
    clean_name = f"{topic_slug}.md"
    filepath = os.path.join(subject_dir, clean_name)

    exam_header = f"\n## Target Exam: {exam_date}" if exam_date else ""

    concept_block = f"""
### Concept: {concept_name}
{content.strip()}
"""

    if os.path.exists(filepath):
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(concept_block)
    else:
        doc = f"""# Topic: {topic}
## Course: {course}
## Difficulty: {difficulty}{exam_header}
{concept_block}"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(doc)

    return filepath
