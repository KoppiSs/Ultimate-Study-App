"""
AI Note Authoring Module.

Provides decoupled, intelligent note generation and enhancement capabilities
using the configured Ollama model and optional offline Wikipedia context.
"""

from datetime import datetime
import re
from typing import Any, Dict, List, Optional
from local_ai_search import ask_ollama
from wiki_server import get_wikipedia_summary
from notes_manager import sanitize_latex_markdown


# ==============================================================================
# STARTER NOTE TEMPLATES & ACADEMIC SNIPPETS
# ==============================================================================

STARTER_TEMPLATES: Dict[str, str] = {
    "Academic Lecture & Core Concepts": """# Topic: {topic}
## Subject: {subject}
## Difficulty: {difficulty}

---

### 📌 1. Academic Overview & Prerequisites
- **Core Motivation**: State the central objective and why this topic matters.
- **Prerequisites**: Key foundational concepts required for understanding.

### 📐 2. Formal Mathematical Formulation
State definitions, variables, and governing equations using LaTeX:
$$f(x) = \\int_{-\\infty}^{\\infty} \\psi(t) e^{-i\\omega t} dt$$

### 🔬 3. Key Concepts & Principles
- **Concept A**: Detailed breakdown of primary mechanism.
- **Concept B**: Secondary properties and behavior under constraints.

### 💡 4. Illustrative Example / Case Study
Walk through a concrete problem step-by-step.

### 🎯 5. High-Yield Exam Checkpoints
- [ ] Checkpoint 1: Understand fundamental theorem and edge cases.
- [ ] Checkpoint 2: Reproduce the core derivation without references.
""",
    "Mathematical Derivation & Proof": """# Topic: {topic} (Derivation)
## Subject: {subject}
## Difficulty: {difficulty}

---

### 🎯 Objective of Derivation
State the proposition or theorem to be derived.

### 📜 Initial Assumptions & Axioms
1. Let system state be represented by ...
2. Boundary conditions: $\\lim_{{x \\to \\infty}} f(x) = 0$

### ✍️ Step-by-Step Derivation
**Step 1: Conservation / Starting Law**
$$E = \\frac{p^2}{2m} + V(x)$$

**Step 2: Differential Operator Substitution**
Substitute quantum operators $\\hat{p} = -i\\hbar \\frac{\\partial}{\\partial x}$:
$$-\\frac{\\hbar^2}{2m}\\frac{d^2\\psi(x)}{dx^2} + V(x)\\psi(x) = E\\psi(x)$$

**Step 3: Normalization & Boundary Resolution**
$$\\int_{-\\infty}^{\\infty} |\\psi(x)|^2 dx = 1$$

### 🏁 Conclusion & Physical Interpretation
State the final result and physical consequences.
""",
    "Active Recall & Exam Revision Sheet": """# Topic: {topic} (Exam Revision)
## Subject: {subject}
## Difficulty: {difficulty}

---

### ⚡ High-Yield Cheat Sheet (TL;DR)
- **Key Formula 1**: $$y = Wx + b$$
- **Key Formula 2**: $$\\mathcal{L} = -\\sum y_i \\log(\\hat{y}_i)$$
- **Primary Pitfall**: Beware division by zero in numerical approximations.

### ❓ Active Recall Self-Test Questions
1. **Question 1**: What happens when learning rate is set too high?
   - *Self-Check Hint*: Oscillations around local minima or divergence.
2. **Question 2**: Explain the difference between parametric and non-parametric models.
   - *Self-Check Hint*: Fixed vs data-scaled parameter space.

### 📋 Concept Comparison Matrix
| Model / Method | Complexity | Robustness | Key Use Case |
|---|---|---|---|
| Approach A | $O(N)$ | High | Baseline testing |
| Approach B | $O(N^2)$ | Moderate | High-accuracy non-linear tasks |
""",
    "Deep In-Depth Analysis": """# Topic: {topic} (Deep Analysis)
## Subject: {subject}
## Difficulty: {difficulty}

---

### 📌 1. Executive Summary & Architectural Overview
- **Thesis Statement**: State the core formulation and scope of the deep-dive.
- **Architectural Context**: Foundational assumptions, prerequisites, and system boundaries.

### 🔬 2. Deep Theoretical Breakdown & Core Mechanisms
- **Fundamental Principles**: Deconstruct underlying governing mechanisms step-by-step.
- **Structural Analysis**: In-depth explanation of how moving parts interact.

### 📐 3. Mathematical & Formal Modeling
State governing equations, state models, or formal notations:
$$\\mathcal{L}(\\theta) = \\mathbb{E}_{x \\sim p_{data}} \\left[ \\log D(x) \\right] + \\mathbb{E}_{z \\sim p_z} \\left[ \\log(1 - D(G(z))) \\right]$$

### ⚖️ 4. Comparative Paradigms & Trade-Offs
| Paradigm / Approach | Strengths | Constraints & Trade-Offs | Complexity |
|---|---|---|---|
| Approach Alpha | High precision | Computational scaling | $O(N^2)$ |
| Approach Beta | Linear scaling | Asymptotic bounds | $O(N)$ |

### ⚠️ 5. Nuances, Edge Cases & Failure Modes
- **Boundary Condition 1**: Behavior near critical singularity or domain limits.
- **Pathological Case 2**: Common failure modes and mitigation strategies.

### 💡 6. Advanced Practical Applications & Case Study
Walk through real-world deployment or complex analytical scenario.

### 🎯 7. Synthesis & Active Recall Checkpoints
- [ ] Masterclass Checkpoint 1: Articulate the core trade-off and boundary conditions.
- [ ] Masterclass Checkpoint 2: Compare failure modes against alternative architectures.
""",
    "Historical Context & Conceptual Narrative": """# Topic: {topic} (Historical Narrative)
## Subject: {subject}
## Difficulty: {difficulty}

---

### 📌 1. Historical Genesis & The Original Dilemma
- **The Dilemma**: What fundamental unsolved problem or experimental crisis triggered this discovery?
- **Era & Intellectual Climate**: The prevailing paradigms and historical context of the time.

### 📜 2. Key Thinkers & Chronological Evolution
- **Early Pioneers**: Initial hypotheses, debates, and competing theories.
- **The Breakthrough**: Seminal experiments, publications, or conceptual leaps that changed the paradigm.

### 🔬 3. Modern Conceptual Understanding
- **Core Principles**: How modern science/engineering understands and refines the concept today.
- **Qualitative Exposition**: In-depth written exposition explaining the physical or logical intuition.

### 💡 4. Historical Impact & Lasting Significance
- How this transformed subsequent academic thought, technology, or scientific understanding.
- Lasting connections to modern fields of study.

### 🎯 5. Discussion & Critical Reflection Questions
1. **Reflection 1**: Why did contemporary alternatives fail to address the core anomaly?
2. **Reflection 2**: How does the historical formulation differ from our current standard model?

### 📚 6. Seminal Papers & Historical Archives
- Key historical texts, seminal papers, and verified Wikipedia archives.
""",
    "Written Academic Exposition": """# Topic: {topic} (Academic Exposition)
## Subject: {subject}
## Difficulty: {difficulty}

---

### 📌 1. Conceptual Introduction & Motivation
Narrative introduction establishing why this concept is essential, elegant, and intellectually profound.

### 📖 2. Exposition of Principles & Core Mechanics
Rich, continuous academic prose explaining the central mechanisms and theoretical models.

### 💡 3. Deep Intuition & Illustrative Analogies
Deep conceptual analogies and explanatory walkthroughs clarifying subtleties without terse bullet points.

### 🌐 4. Interdisciplinary Significance & Practical Relevance
How this connects to broader fields, systems, and modern real-world applications.

### 🎯 5. Critical Reflection & Active Recall
1. **Conceptual Question 1**: Explain the core intuition in your own words.
2. **Conceptual Question 2**: What is the most common misunderstanding of this principle?

### 📚 6. Academic References & Further Reading
Cite foundational textbooks and offline references.
""",
    "Blank Canvas": """# Topic: {topic}
## Subject: {subject}
## Difficulty: {difficulty}

---

### Concept: Introduction
Begin your notes here...
""",
}

SNIPPET_LIBRARY: Dict[str, str] = {
    "LaTeX Display Equation": "\n$$\nE = mc^2\n$$\n",
    "LaTeX Inline Equation": " $f(x) = \\sigma(Wx + b)$ ",
    "LaTeX Fraction & Integral": "\n$$\n\\int_{a}^{b} \\frac{d f(x)}{dx} dx = f(b) - f(a)\n$$\n",
    "LaTeX Aligned Derivation": "\n$$\n\\begin{aligned}\n\\hat{y} &= \\frac{1}{n} \\sum_{i=1}^{n} y_i \\phi(w^T x_i) \\\\\n&= f(X; \\theta)\n\\end{aligned}\n$$\n",
    "Definition Callout": "\n> **Definition:** A formal statement defining the exact mathematical or theoretical concept.\n",
    "Theorem & Proof Block": "\n**Theorem:** *State theorem statement here.*\n\n*Proof:* \n1. By assumption, ...\n2. Therefore, ...\n$$\\blacksquare$$\n",
    "High-Yield Checkpoint": "\n- [ ] **High-Yield Checkpoint:** Key formula or definition to memorize.\n",
    "Comparison Table": "\n| Feature | Method A | Method B |\n|---|---|---|\n| Advantage | Fast convergence | Global optimum |\n| Disadvantage | Sensitive to noise | Computationally heavy |\n",
}


def get_template_names() -> List[str]:
    """Returns list of available template names."""
    return list(STARTER_TEMPLATES.keys())


def get_template(name: str, topic: str = "New Topic", subject: str = "General", difficulty: int = 3) -> str:
    """Returns rendered template text."""
    tmpl = STARTER_TEMPLATES.get(name, STARTER_TEMPLATES["Blank Canvas"])
    return (
        tmpl.replace("{topic}", topic)
        .replace("{subject}", subject)
        .replace("{difficulty}", str(difficulty))
    )


def get_snippet_names() -> List[str]:
    """Returns available snippet names."""
    return list(SNIPPET_LIBRARY.keys())


def get_snippet(name: str) -> str:
    """Returns snippet code string."""
    return SNIPPET_LIBRARY.get(name, "")


# ==============================================================================
# AI NOTE GENERATION & ENHANCEMENT ENGINE
# ==============================================================================

def generate_ai_study_note(
    subject: str,
    topic: str,
    detail_level: str = "Standard Academic Lecture",
    include_math: bool = True,
    ground_with_wiki: bool = False,
    custom_guidance: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates a structured, publication-grade academic study note using Ollama,
    optionally grounded with verified definitions from the offline Wikipedia ZIM archive.
    """
    subject_clean = subject.strip() or "General"
    topic_clean = topic.strip() or "Study Note"

    # Step 1: Offline Wikipedia grounding
    wiki_context = ""
    wiki_title = ""
    if ground_with_wiki:
        try:
            wiki_res = get_wikipedia_summary(topic_clean, max_paragraphs=3)
            if wiki_res.get("found"):
                wiki_title = wiki_res.get("title", topic_clean)
                wiki_context = wiki_res.get("summary", "")
        except Exception:
            wiki_context = ""

    # Step 2: Detail instructions
    detail_prompts = {
        "Concise Overview": (
            "Focus on high-yield core definitions, a single key formula, and concise bullet points. Around 250-400 words."
        ),
        "Standard Academic Lecture": (
            "Provide a thorough academic lecture note with clear definitions, theoretical foundations, 2-3 LaTeX equations, practical intuitions, and 2 active recall questions."
        ),
        "Rigorous Mathematical Treatment": (
            "Provide a mathematically rigorous graduate-level note with formal notation, step-by-step LaTeX derivations using $$...$$, proof sketches, edge cases, and active recall questions."
        ),
        "Historical & Conceptual Narrative": (
            "Focus on an extensive, written narrative exploring the historical evolution, key figures, "
            "experimental breakthroughs, paradigm shifts, and foundational context behind the topic. Trace the "
            "original dilemma that triggered this concept, the evolution of competing ideas, and qualitative narrative depth."
        ),
        "Written Academic Exposition": (
            "Provide an eloquent, highly written textbook-grade academic essay. Emphasize continuous narrative prose, "
            "deep conceptual exposition, illustrative analogies, and qualitative understanding, explaining the 'why' and 'how' "
            "in depth rather than concise bullet points."
        ),
        "Deep In-Depth Analysis": (
            "Deliver an exhaustive, masterclass deep-dive analysis. Extensively deconstruct underlying mechanisms, "
            "theoretical subtleties, edge cases and failure modes, comparative paradigms, structural trade-offs, "
            "and advanced practical implementations with high analytical rigor."
        ),
    }
    detail_instruction = detail_prompts.get(detail_level, detail_prompts["Standard Academic Lecture"])

    math_instruction = (
        "CRITICAL LATEX MATH FORMATTING RULES (KaTeX / Streamlit Compliance):\n"
        "- Use $...$ for inline equations and $$...$$ for display equations.\n"
        "- For multi-line derivations, system of equations, or aligned proofs, ALWAYS use the 'aligned' environment inside $$...$$ delimiters:\n"
        "  $$\n"
        "  \\begin{aligned}\n"
        "  \\hat{y} &= f(x) \\\\\n"
        "  &= w^T x + b\n"
        "  \\end{aligned}\n"
        "  $$\n"
        "- NEVER output raw, unwrapped LaTeX environments like \\begin{align*}, \\begin{align}, or \\begin{equation} without $$ delimiters. "
        "KaTeX cannot render them without $$ and requires 'aligned' instead of 'align*'.\n"
        "- Explicitly define all variables and symbols."
        if include_math
        else "Keep mathematical notation minimal and emphasize intuitive conceptual understanding."
    )

    wiki_section_prompt = (
        f"""
Grounding Context from Offline Wikipedia Archive:
Article Title: {wiki_title or topic_clean}
Context:
{wiki_context}
(Incorporate these facts and credit the offline Wikipedia reference at the bottom of the note).
"""
        if wiki_context
        else ""
    )

    guidance_section = (
        f"\nUser-Specified Focus Areas:\n{custom_guidance.strip()}\n"
        if custom_guidance and custom_guidance.strip()
        else ""
    )

    # Dynamic document structure schema tailored to detail level
    if detail_level == "Historical & Conceptual Narrative":
        structure_prompt = f"""Required Markdown Document Structure:
---
title: {topic_clean}
subject: {subject_clean}
difficulty: 3
tags: [{topic_clean.lower().replace(' ', '-')}, history, academic-note]
created_at: {datetime.now().strftime('%Y-%m-%d %H:%M')}
---

# Topic: {topic_clean}
## Course: {subject_clean}
## Difficulty: 3

### 📌 Historical Genesis & The Original Dilemma
- Detailed narrative on the problem, crisis, or curiosity that sparked this discovery.
- Prevailing scientific or philosophical climate of the era.

### 📜 Key Thinkers, Breakthroughs & Paradigm Shifts
- Chronological development, seminal contributors, and pivotal experiments or insights.
- The shift from prior assumptions to the new understanding.

### 🔬 Modern Conceptual Understanding & Theory
- Core qualitative definitions, mechanisms, and modern academic formulation.

### 💡 Historical Impact & Lasting Significance
- Real-world impact, subsequent developments, and philosophical/intellectual legacy.

### 🎯 Discussion & Active Recall Reflection
- 2-3 deep reflection and synthesis questions with concise explanatory answers.

### 📚 Historical Archives & References
- Cite seminal publications and the offline Wikipedia archive.
"""
    elif detail_level == "Deep In-Depth Analysis":
        structure_prompt = f"""Required Markdown Document Structure:
---
title: {topic_clean}
subject: {subject_clean}
difficulty: 4
tags: [{topic_clean.lower().replace(' ', '-')}, deep-analysis, academic-note]
created_at: {datetime.now().strftime('%Y-%m-%d %H:%M')}
---

# Topic: {topic_clean}
## Course: {subject_clean}
## Difficulty: 4

### 📌 Executive Summary & Architectural Prerequisites
- Central thesis, foundational assumptions, and architectural boundaries.

### 🔬 Deep Theoretical Breakdown & Core Mechanisms
- Multi-layered analysis of underlying dynamics and governing principles.
- Detailed step-by-step breakdown of how sub-components interact.

### 📐 Formal Mathematical & Structural Formulation
- Formal equations in LaTeX ($$ ... $$) or rigorous structural models.
- Precise definition of variables, state spaces, and operators.

### ⚖️ Comparative Paradigms & Structural Trade-offs
- Comprehensive comparison table or comparative analysis against alternative paradigms.
- Analysis of trade-offs, scaling behaviors, and algorithmic/physical complexity.

### ⚠️ Boundary Conditions, Failure Modes & Pathological Cases
- Subtle edge cases, common misconceptions, limit behaviors, and failure modes.

### 💡 Advanced Applications & Real-World Implementations
- High-level case study or concrete modern industrial/academic application.

### 🎯 Masterclass Active Recall & Synthesis Problems
- 2-3 advanced conceptual and synthesis problems with rigorous solutions.

### 📚 References & Grounded Literature
- Cite offline Wikipedia references and primary academic literature.
"""
    elif detail_level == "Written Academic Exposition":
        structure_prompt = f"""Required Markdown Document Structure:
---
title: {topic_clean}
subject: {subject_clean}
difficulty: 3
tags: [{topic_clean.lower().replace(' ', '-')}, written-exposition, academic-note]
created_at: {datetime.now().strftime('%Y-%m-%d %H:%M')}
---

# Topic: {topic_clean}
## Course: {subject_clean}
## Difficulty: 3

### 📌 Conceptual Introduction & Motivation
- Narrative introduction establishing why this concept is essential and intellectually profound.

### 📖 Exposition of Principles & Core Mechanics
- Rich, continuous academic prose explaining the central mechanisms and theoretical models.

### 💡 Intuitions, Analogies & Conceptual Bridges
- Deep conceptual analogies and illustrative explanations that provide intuitive clarity.

### 🌐 Interdisciplinary Significance & Practical Manifestations
- Practical manifestations, interdisciplinary connections, and modern relevance.

### 🎯 Conceptual Review & Active Recall
- 2-3 self-test questions focusing on qualitative and conceptual understanding.

### 📚 References
- Cite verified textbooks and offline Wikipedia archive references.
"""
    else:
        structure_prompt = f"""Required Markdown Document Structure:
---
title: {topic_clean}
subject: {subject_clean}
difficulty: 3
tags: [{topic_clean.lower().replace(' ', '-')}, academic-note]
created_at: {datetime.now().strftime('%Y-%m-%d %H:%M')}
---

# Topic: {topic_clean}
## Course: {subject_clean}
## Difficulty: 3

### 📌 Academic Overview & Prerequisites
- Define the central concept clearly.
- Note any prerequisites.

### 📐 Mathematical Formulation & Definitions
- State governing equations in LaTeX ($$ ... $$).
- Define every variable.

### 🔬 Core Theory & Derivations
- Provide structured explanations and step-by-step mathematical derivations.
- Ensure all multi-line equations and derivation steps use $$ \\begin{{aligned}} ... \\end{{aligned}} $$.

### 💡 Intuition & Examples
- Provide a concrete example illustrating the theory.

### 🎯 High-Yield Exam Checkpoints & Active Recall
- 2-3 self-test questions with concise answers.

### 📚 References
- Cite the offline Wikipedia archive or foundational textbook references.
"""

    prompt = f"""You are an elite academic professor and study architect. Write a comprehensive, masterclass study note on the topic '{topic_clean}' under the subject '{subject_clean}'.

{math_instruction}
{detail_instruction}
{wiki_section_prompt}
{guidance_section}

{structure_prompt}

Output ONLY the complete Markdown document. Do not wrap the response in conversational text like "Here is your note:". Begin directly with the frontmatter and headers.
"""

    if not wiki_context:
        prompt += "\nNo Wikipedia excerpt was supplied. Omit Wikipedia citations and do not invent references."

    try:
        raw_response = ask_ollama(model, prompt)
        if not raw_response or "Ollama connection error" in raw_response:
            return {
                "success": False,
                "error": raw_response or "Failed to connect to local Ollama engine.",
                "content": "",
                "topic": topic_clean,
                "subject": subject_clean,
                "wiki_grounded": bool(wiki_context),
            }

        cleaned_md = clean_generated_markdown(raw_response, topic_clean, subject_clean)
        return {
            "success": True,
            "error": None,
            "content": cleaned_md,
            "topic": topic_clean,
            "subject": subject_clean,
            "wiki_grounded": bool(wiki_context),
            "wiki_title": wiki_title,
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "content": "",
            "topic": topic_clean,
            "subject": subject_clean,
            "wiki_grounded": False,
        }


def enhance_existing_note(
    current_markdown: str,
    action: str,
    custom_instruction: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Enhances or extends an existing study note using local Ollama models.
    """
    if not current_markdown.strip():
        return {"success": False, "error": "Note content is empty.", "content": ""}

    action_prompts = {
        "add_derivations": (
            "Analyze the following note. Identify the core mathematical claims or principles, "
            "and add a rigorous step-by-step LaTeX derivation section using $$...$$ equations. "
            "Preserve the existing content and append or integrate the derivations cleanly."
        ),
        "add_wiki_grounding": (
            "Enrich the following note with verified foundational background knowledge. "
            "Add a '### 📚 Foundational Context & Historical Development' section and clarify "
            "key definitions with academic citations."
        ),
        "add_historical_context": (
            "Enrich the following note with verified foundational background knowledge and an extensive historical narrative. "
            "Add a '### 📜 Historical Genesis & Evolutionary Development' section tracing early pioneers, "
            "the original dilemma, key experimental breakthroughs, and paradigm shifts."
        ),
        "deep_analysis": (
            "Perform an exhaustive, multi-dimensional deep dive into this note's subject. Add dedicated sections for "
            "underlying architectural mechanisms, subtle edge cases, failure modes, comparative paradigms, and structural trade-offs."
        ),
        "generate_flashcards": (
            "Based on the following note, append an '### 🎯 Active Recall Flashcards & Practice Problems' "
            "section containing 3-5 high-yield exam questions, conceptual traps to avoid, and step-by-step solutions."
        ),
        "summarize_cheatsheet": (
            "Read the following note and generate a '### ⚡ High-Yield Exam Cheatsheet (TL;DR)' block "
            "at the very top (after the header). Include key formulas, essential definitions, and 3 high-probability exam points."
        ),
        "polish_formatting": (
            "Polish the typography and structure of this note. Standardize Markdown headings, ensure LaTeX formulas "
            "are formatted properly with $...$ and $$...$$, organize lists cleanly, and ensure consistent style."
        ),
        "custom": (
            f"Apply the following instruction to the note:\n{custom_instruction or 'Refine and improve the note.'}"
        ),
    }

    selected_prompt = action_prompts.get(action, action_prompts["custom"])

    prompt = f"""You are an expert academic editor and tutor.
{selected_prompt}

Guidelines:
- Maintain or enhance all LaTeX formulas ($...$ and $$...$$).
- Output the complete, enhanced Markdown note directly.
- Do not output preamble or conversational filler.

Original Note:
{current_markdown}
"""

    try:
        enhanced = ask_ollama(model, prompt)
        if not enhanced or "Ollama connection error" in enhanced:
            return {"success": False, "error": enhanced, "content": current_markdown}

        # Strip accidental code-fence wrappers around the whole document
        enhanced_clean = re.sub(r"^```markdown\s*", "", enhanced.strip(), flags=re.IGNORECASE)
        enhanced_clean = re.sub(r"\s*```$", "", enhanced_clean).strip()
        enhanced_clean = sanitize_latex_markdown(enhanced_clean)

        return {"success": True, "error": None, "content": enhanced_clean}
    except Exception as e:
        return {"success": False, "error": str(e), "content": current_markdown}


def clean_generated_markdown(raw_text: str, topic: str, subject: str) -> str:
    """
    Cleans up any conversational wrappers or markdown backtick enclosures
    produced by LLMs, and sanitizes LaTeX formatting for KaTeX rendering.
    """
    cleaned = raw_text.strip()
    # Strip leading markdown code blocks if the entire response is enclosed in ```markdown ... ```
    if cleaned.startswith("```markdown"):
        cleaned = cleaned[len("```markdown"):].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    # If the response doesn't have a top-level header, ensure one is present
    if not re.search(r"^#\s+", cleaned, re.MULTILINE):
        cleaned = f"# Topic: {topic}\n## Course: {subject}\n\n{cleaned}"

    # Sanitize and normalize LaTeX environments for KaTeX compatibility
    cleaned = sanitize_latex_markdown(cleaned)
    return cleaned
