from settings_manager import load_settings
from study_db import search_personal_notes
from local_ai_search import ask_ollama
from wiki_server import get_wikipedia_summary, wikipedia_available

def evaluate_notes_sufficiency(query: str, personal_notes_text: str) -> bool:
    """Uses the configured Ollama model to check if personal notes contain enough theoretical coverage or derivations to answer."""
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


def run_study_buddy(query: str):
    print(f"\n[?] Student Query: '{query}'")
    
    # 1. Search Personal Notes (Tier 1)
    print("Searching personal course notes...")
    local_hits = search_personal_notes(query, limit=3) if load_settings().get("tutor_notes_enabled") else []
    
    personal_context = ""
    if local_hits:
        for hit in local_hits:
            personal_context += f"\n[Course: {hit['topic']} | Note: {hit['concept_name']}]\n{hit['content']}\n"

    wiki_context = ""
    if wikipedia_available() and not evaluate_notes_sufficiency(query, personal_context):
        reference = get_wikipedia_summary(query, max_paragraphs=3)
        if reference.get("found"):
            wiki_context = reference["summary"]

    print("Generating reply...")
    synthesis_prompt = f"""You are an intelligent, supportive study buddy. Answer the student's question clearly.
Preserve any LaTeX mathematical formulas ($...$ or $$...$$) accurately.

Personal Course Notes:
{personal_context if personal_context else "None available."}

Wikipedia Background Context:
{wiki_context if wiki_context else "None available."}

Student Question:
{query}

Guidelines:
- Prioritize definitions and formulas from Personal Course Notes.
- If using Wikipedia context to fill gaps, explicitly mention it.
- If neither source has the answer, use your general knowledge and say when you are uncertain."""

    response = ask_ollama(None, synthesis_prompt)
    
    print("\n================ STUDY BUDDY RESPONSE ================\n")
    print(response)
    print("\n======================================================\n")

if __name__ == "__main__":
    print("AI Study Buddy Initialized. Type 'exit' to quit.")
    while True:
        user_query = input("\nAsk a study question: ")
        if user_query.strip().lower() == 'exit':
            break
        run_study_buddy(user_query)