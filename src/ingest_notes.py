import os
import re
from study_db import insert_or_get_topic, insert_note, search_personal_notes

from settings_manager import get_notes_vault_dir

def parse_and_ingest_markdown(filepath: str):
    """Parses Markdown metadata and concepts, then inserts them into SQLite."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # Extract Topic metadata
    topic_match = re.search(r'^#\s*Topic:\s*(.+)$', raw_text, re.MULTILINE)
    topic_name = topic_match.group(1).strip() if topic_match else os.path.splitext(os.path.basename(filepath))[0]

    exam_match = re.search(r'^##\s*Target Exam:\s*(.+)$', raw_text, re.MULTILINE)
    target_exam = exam_match.group(1).strip() if exam_match else None

    diff_match = re.search(r'^##\s*Difficulty:\s*(\d+)', raw_text, re.MULTILINE)
    difficulty = int(diff_match.group(1)) if diff_match else 1

    # Ensure topic exists in DB
    insert_or_get_topic(topic_name, difficulty, target_exam)

    # Split concepts by "### Concept:"
    concepts = re.split(r'\n###\s*Concept:\s*', raw_text)
    
    count = 0
    for section in concepts[1:]:  # First chunk is the header metadata
        lines = section.strip().split('\n', 1)
        concept_name = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        
        # Check if content has LaTeX equations or math derivations
        is_derivation = bool(re.search(r'\$\$|\\frac|\\psi', body))
        
        insert_note(
            topic_title=topic_name,
            concept_name=concept_name,
            content=body,
            is_derivation=is_derivation
        )
        count += 1
        
    print(f"[✓] Ingested {count} concept(s) from '{os.path.basename(filepath)}'")

def ingest_all_notes():
    """Scans data/notes for all .md files and imports them."""
    for root, _, files in os.walk(get_notes_vault_dir()):
        for file in files:
            if file.endswith(".md"):
                parse_and_ingest_markdown(os.path.join(root, file))

if __name__ == "__main__":
    ingest_all_notes()
    
    # Quick verification search
    print("\n--- Testing Note Search ---")
    query = "interpretations"
    results = search_personal_notes(query)
    for r in results:
        print(f"\nFound in Topic: {r['topic']} | Concept: {r['concept_name']}")
        print(f"Content excerpt: {r['content'][:140]}...")