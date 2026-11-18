import sqlite3
import os
import re

from settings_manager import get_storage_paths

STOPWORDS = {
    "what", "is", "are", "the", "a", "an", "of", "in", "for", "to", "on", "and",
    "my", "notes", "according", "tell", "me", "about", "how", "why", "can", "you",
    "does", "explain", "describe", "main", "some", "give", "list"
}

def get_connection():
    path = get_storage_paths()["study_db_file"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn

def initialize_db():
    get_connection().close()


def initialize_schema(conn):
    cursor = conn.cursor()
    
    # 1. Topics Table (Now includes course grouping)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course TEXT NOT NULL DEFAULT 'General',
        title TEXT UNIQUE NOT NULL,
        difficulty_weight INTEGER DEFAULT 1,
        target_exam_date TEXT
    )
    ''')

    # --- AUTO-MIGRATION: Ensure 'course' column exists on older databases ---
    if "course" not in {row[1] for row in cursor.execute("PRAGMA table_info(topics)")}:
        cursor.execute("ALTER TABLE topics ADD COLUMN course TEXT NOT NULL DEFAULT 'General'")
    
    # 2. Notes Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER,
        concept_name TEXT NOT NULL,
        content TEXT NOT NULL,
        is_derivation BOOLEAN DEFAULT 0,
        FOREIGN KEY (topic_id) REFERENCES topics(id)
    )
    ''')
    
    # 3. FTS5 Virtual Table
    cursor.execute('''
    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
        concept_name,
        content,
        content='notes',
        content_rowid='id'
    )
    ''')
    
    cursor.execute('''
    CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
        INSERT INTO notes_fts(rowid, concept_name, content)
        VALUES (new.id, new.concept_name, new.content);
    END;
    ''')
    
    cursor.execute('''
    CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
        INSERT INTO notes_fts(notes_fts, rowid, concept_name, content)
        VALUES('delete', old.id, old.concept_name, old.content);
    END;
    ''')

    # 4. Schedule Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS study_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER,
        scheduled_date TEXT NOT NULL,
        duration_minutes INTEGER NOT NULL,
        completed BOOLEAN DEFAULT 0,
        FOREIGN KEY (topic_id) REFERENCES topics(id)
    )
    ''')
    
    conn.commit()

def insert_or_get_topic(title: str, course: str = "General", difficulty: int = 1, exam_date: str = None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM topics WHERE title = ?", (title,))
    row = cursor.fetchone()
    if row:
        topic_id = row["id"]
        # Update course if previously default
        cursor.execute("UPDATE topics SET course = ? WHERE id = ? AND course = 'General'", (course, topic_id))
        conn.commit()
    else:
        cursor.execute(
            "INSERT INTO topics (course, title, difficulty_weight, target_exam_date) VALUES (?, ?, ?, ?)",
            (course, title, difficulty, exam_date)
        )
        conn.commit()
        topic_id = cursor.lastrowid
    conn.close()
    return topic_id

def insert_note(topic_title: str, concept_name: str, content: str, course: str = "General", is_derivation: bool = False):
    topic_id = insert_or_get_topic(topic_title, course=course)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO notes (topic_id, concept_name, content, is_derivation) VALUES (?, ?, ?, ?)",
        (topic_id, concept_name, content, 1 if is_derivation else 0)
    )
    conn.commit()
    conn.close()

def search_personal_notes(query: str, limit: int = 3):
    conn = get_connection()
    cursor = conn.cursor()
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 1]
    if not keywords:
        keywords = [w for w in words if len(w) > 1]
    if not keywords:
        conn.close()
        return []

    fts_query = " OR ".join(keywords)
    sql = '''
    SELECT n.id, t.course, t.title AS topic, n.concept_name, n.content, n.is_derivation, rank
    FROM notes_fts f
    JOIN notes n ON f.rowid = n.id
    JOIN topics t ON n.topic_id = t.id
    WHERE notes_fts MATCH ?
    ORDER BY rank
    LIMIT ?
    '''
    try:
        cursor.execute(sql, (fts_query, limit))
        results = [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        results = []
    finally:
        conn.close()
    return results

def get_daily_study_blocks(date_str: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT sb.id, t.course, t.title AS topic, sb.duration_minutes, sb.completed
        FROM study_blocks sb
        JOIN topics t ON sb.topic_id = t.id
        WHERE sb.scheduled_date = ?
        ORDER BY sb.id ASC
    ''', (date_str,))
    blocks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return blocks

def update_block_status(block_id: int, completed: bool):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE study_blocks SET completed = ? WHERE id = ?', (1 if completed else 0, block_id))
    conn.commit()
    conn.close()

def clear_daily_schedule(date_str: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM study_blocks WHERE scheduled_date = ?', (date_str,))
    conn.commit()
    conn.close()

def get_courses_and_notes():
    """Fetches notes organized by Course -> Topic -> Concepts."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.course, t.title AS topic, t.difficulty_weight, t.target_exam_date,
               n.id AS note_id, n.concept_name, n.content, n.is_derivation
        FROM topics t
        LEFT JOIN notes n ON t.id = n.topic_id
        ORDER BY t.course, t.title, n.concept_name
    ''')
    rows = cursor.fetchall()
    conn.close()

    hierarchy = {}
    for r in rows:
        c = r["course"]
        t = r["topic"]
        if c not in hierarchy:
            hierarchy[c] = {}
        if t not in hierarchy[c]:
            hierarchy[c][t] = {
                "difficulty": r["difficulty_weight"],
                "exam_date": r["target_exam_date"],
                "notes": []
            }
        if r["concept_name"]:
            hierarchy[c][t]["notes"].append({
                "id": r["note_id"],
                "concept": r["concept_name"],
                "content": r["content"],
                "is_derivation": bool(r["is_derivation"])
            })
    return hierarchy

def get_all_courses():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT course FROM topics')
    courses = [row["course"] for row in cursor.fetchall() if row["course"]]
    conn.close()
    return sorted(courses)

if __name__ == "__main__":
    initialize_db()