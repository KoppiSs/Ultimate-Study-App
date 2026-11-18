"""
Unit tests for Subject-Related Folder Trees, Frontmatter Parsing,
and AI Note Authoring integration.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from notes_manager import (
    sanitize_filename,
    sanitize_subject_name,
    is_path_safe,
    parse_note_metadata,
    format_note_with_frontmatter,
    create_subject_folder,
    list_subject_folders,
    get_subject_folder_tree,
    organize_notes_into_subject_folders,
    save_course_note_file,
    delete_study_note,
    move_note_to_subject_folder,
    rename_subject_folder,
    delete_subject_folder,
    search_markdown_notes,
    get_all_markdown_notes_hierarchy,
    sanitize_latex_markdown,
)
from ai_notes_author import (
    get_template_names,
    get_template,
    get_snippet_names,
    get_snippet,
    clean_generated_markdown,
)


class TestNotesVaultRefresh(unittest.TestCase):
    def test_replaced_vault_refreshes_tree_and_opens_current_note(self):
        from streamlit.testing.v1 import AppTest
        from settings_manager import save_settings

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            vault = root / "notes"
            old_note = vault / "Old Subject" / "old.md"
            old_note.parent.mkdir(parents=True)
            old_note.write_text("# Old note\n\nArchived text", encoding="utf-8")

            with (
                patch("settings_manager.SETTINGS_FILE", str(root / "settings.json")),
                patch("streamlit_option_menu.option_menu", return_value="Notes Vault"),
            ):
                save_settings({"data_dir": str(root)})
                app = AppTest.from_file(Path(__file__).with_name("app.py"), default_timeout=10).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["selected_note_path"], str(old_note))

                vault.rename(root / "archived")
                first_note = vault / "Current Subject" / "first.md"
                second_note = vault / "Current Subject" / "second.md"
                first_note.parent.mkdir(parents=True)
                first_note.write_text("# First note\n\nFirst current body", encoding="utf-8")
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["selected_note_path"], str(first_note))
                self.assertNotIn(f"tree_btn_{old_note}", {button.key for button in app.button})

                second_note.write_text("# Second note\n\nSecond current body", encoding="utf-8")
                app.run()
                app.button(key=f"tree_btn_{second_note}").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["selected_note_path"], str(second_note))
                self.assertTrue(any("Second current body" in item.value for item in app.markdown))

                second_note.write_text("# Second note\n\nUpdated outside Streamlit", encoding="utf-8")
                app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any("Updated outside Streamlit" in item.value for item in app.markdown))


class TestNotesManagerFolderTrees(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_sanitize_subject_name(self):
        self.assertEqual(sanitize_subject_name("Physics & Math: 101/Intro"), "Physics & Math 101Intro")
        self.assertEqual(sanitize_subject_name("../Dangerous/Path"), "DangerousPath")
        self.assertEqual(sanitize_subject_name(""), "General")

    def test_is_path_safe(self):
        inside_path = os.path.join(self.test_dir, "Physics", "note.md")
        outside_path = os.path.abspath(os.path.join(self.test_dir, "..", "secret.txt"))
        self.assertTrue(is_path_safe(inside_path, self.test_dir))
        self.assertFalse(is_path_safe(outside_path, self.test_dir))

    def test_frontmatter_formatting_and_parsing(self):
        title = "Eigenvalues & Spectral Theorem"
        subject = "Linear Algebra"
        body = "Let $A \\in \\mathbb{R}^{n \\times n}$ be symmetric.\n$$Av = \\lambda v$$"
        tags = ["linear-algebra", "spectral-theory"]
        
        formatted = format_note_with_frontmatter(
            title=title,
            subject=subject,
            body=body,
            difficulty=4,
            exam_date="2026-12-15",
            tags=tags,
        )
        self.assertIn("title: \"Eigenvalues & Spectral Theorem\"", formatted)
        self.assertIn("difficulty: 4", formatted)
        self.assertIn("tags: [linear-algebra, spectral-theory]", formatted)

        meta = parse_note_metadata(formatted)
        self.assertEqual(meta["title"], title)
        self.assertEqual(meta["subject"], subject)
        self.assertEqual(meta["difficulty"], 4)
        self.assertEqual(meta["exam_date"], "2026-12-15")
        self.assertIn("linear-algebra", meta["tags"])
        self.assertIn("$$Av = \\lambda v$$", meta["raw_body"])

    def test_save_course_note_in_subject_tree(self):
        saved = save_course_note_file(
            subject="Quantum Computing",
            title="Qubit Superposition",
            content="State vector: $|\\psi\\rangle = \\alpha|0\\rangle + \\beta|1\\rangle$",
            difficulty=3,
            notes_dir=self.test_dir,
        )
        self.assertTrue(os.path.exists(saved))
        self.assertIn("Quantum Computing", saved)
        
        # Verify tree reflection
        tree = get_subject_folder_tree(self.test_dir)
        self.assertIn("Quantum Computing", tree)
        self.assertEqual(len(tree["Quantum Computing"]), 1)
        self.assertEqual(tree["Quantum Computing"][0]["title"], "Qubit Superposition")
        self.assertTrue(tree["Quantum Computing"][0]["has_math"])

    def test_auto_organize_flat_notes(self):
        # Create loose files in test root
        loose_file = os.path.join(self.test_dir, "loose_calc.md")
        with open(loose_file, "w", encoding="utf-8") as f:
            f.write("# Topic: Differential Calculus\n## Course: Pure Mathematics\n\n### Concept: Derivatives\n$$\\frac{df}{dx}$$")

        moved = organize_notes_into_subject_folders(self.test_dir)
        self.assertIn("loose_calc.md", moved)
        self.assertFalse(os.path.exists(loose_file))
        
        target_path = os.path.join(self.test_dir, "Pure Mathematics", "loose_calc.md")
        self.assertTrue(os.path.exists(target_path))

    def test_search_notes_across_trees(self):
        save_course_note_file(
            subject="Neuroscience",
            title="Action Potentials",
            content="Hodgkin-Huxley model differential equations for sodium channels.",
            notes_dir=self.test_dir,
        )
        results = search_markdown_notes("Hodgkin-Huxley", notes_dir=self.test_dir)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["course"], "Neuroscience")
        self.assertEqual(results[0]["topic"], "Action Potentials")

    def test_search_notes_preserves_full_content_and_handles_stopwords(self):
        long_body = "Overview paragraph.\n" + ("Theoretical derivation step details. " * 80)
        self.assertGreater(len(long_body), 1500)
        save_course_note_file(
            subject="Quantum Mechanics",
            title="Heisenberg Uncertainty Principle",
            content=long_body,
            tags=["quantum", "physics"],
            notes_dir=self.test_dir,
        )
        # Search with conversational stopwords
        results = search_markdown_notes(
            "What is the derivation of the Heisenberg uncertainty principle?",
            notes_dir=self.test_dir,
        )
        self.assertGreaterEqual(len(results), 1)
        top = results[0]
        self.assertEqual(top["topic"], "Heisenberg Uncertainty Principle")
        # Ensure full content is preserved beyond the old 800-char limit
        self.assertEqual(len(top["content"]), len(long_body))
        self.assertIn("Theoretical derivation step details.", top["content"])
        self.assertTrue("excerpt" in top)
        self.assertLessEqual(len(top["excerpt"]), 300)

    def test_delete_study_note(self):
        path = save_course_note_file(
            subject="History",
            title="Industrial Revolution",
            content="Steam engines and mechanization.",
            notes_dir=self.test_dir,
        )
        self.assertTrue(os.path.exists(path))
        deleted = delete_study_note(path, notes_dir=self.test_dir)
        self.assertTrue(deleted)
        self.assertFalse(os.path.exists(path))

    def test_move_note_to_subject_folder(self):
        orig_path = save_course_note_file(
            subject="Physics",
            title="Thermodynamics",
            content="Second law of thermodynamics: entropy increases.",
            difficulty=3,
            notes_dir=self.test_dir,
        )
        self.assertTrue(os.path.exists(orig_path))

        new_path = move_note_to_subject_folder(orig_path, "Chemistry", notes_dir=self.test_dir)
        self.assertTrue(os.path.exists(new_path))
        self.assertFalse(os.path.exists(orig_path))
        self.assertIn("Chemistry", new_path)

        meta = parse_note_metadata(open(new_path, encoding="utf-8").read())
        self.assertEqual(meta["subject"], "Chemistry")
        self.assertEqual(meta["title"], "Thermodynamics")

    def test_rename_subject_folder(self):
        save_course_note_file(
            subject="Math",
            title="Calculus",
            content="Integration by parts.",
            notes_dir=self.test_dir,
        )
        renamed_folder = rename_subject_folder("Math", "Mathematics", notes_dir=self.test_dir)
        self.assertTrue(os.path.isdir(renamed_folder))
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "Math")))

        note_in_renamed = os.path.join(renamed_folder, "calculus.md")
        self.assertTrue(os.path.exists(note_in_renamed))
        meta = parse_note_metadata(open(note_in_renamed, encoding="utf-8").read())
        self.assertEqual(meta["subject"], "Mathematics")

    def test_delete_subject_folder(self):
        create_subject_folder("Robotics", notes_dir=self.test_dir)
        save_course_note_file(
            subject="Robotics",
            title="Kinematics",
            content="Forward and inverse kinematics.",
            notes_dir=self.test_dir,
        )
        self.assertTrue(os.path.isdir(os.path.join(self.test_dir, "Robotics")))

        deleted = delete_subject_folder("Robotics", delete_contents=True, notes_dir=self.test_dir)
        self.assertTrue(deleted)
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "Robotics")))

    def test_folder_safety_boundary_checks(self):
        # Cannot delete root notes directory
        with self.assertRaises(ValueError):
            delete_subject_folder("", notes_dir=self.test_dir)

        # Cannot delete non-existent folder
        with self.assertRaises(FileNotFoundError):
            delete_subject_folder("NonExistentFolder123", notes_dir=self.test_dir)

        # Cannot rename non-existent folder
        with self.assertRaises(FileNotFoundError):
            rename_subject_folder("NonExistentFolder123", "NewName", notes_dir=self.test_dir)

        # Cannot move non-existent note
        with self.assertRaises(FileNotFoundError):
            move_note_to_subject_folder(os.path.join(self.test_dir, "fake.md"), "NewSubj", notes_dir=self.test_dir)



class TestAINoteAuthoring(unittest.TestCase):
    def test_templates_available(self):
        names = get_template_names()
        self.assertIn("Academic Lecture & Core Concepts", names)
        self.assertIn("Mathematical Derivation & Proof", names)
        self.assertIn("Active Recall & Exam Revision Sheet", names)

        rendered = get_template("Mathematical Derivation & Proof", topic="Euler Formula", subject="Analysis", difficulty=4)
        self.assertIn("Euler Formula", rendered)
        self.assertIn("Analysis", rendered)
        self.assertIn("Step-by-Step Derivation", rendered)

    def test_snippets_library(self):
        snippets = get_snippet_names()
        self.assertIn("LaTeX Display Equation", snippets)
        self.assertIn("Definition Callout", snippets)
        self.assertIn("Theorem & Proof Block", snippets)

        tex = get_snippet("LaTeX Display Equation")
        self.assertIn("$$", tex)

    def test_clean_generated_markdown(self):
        raw = "```markdown\n# Topic: Deep Learning\n## Course: AI\nContent here.\n```"
        cleaned = clean_generated_markdown(raw, "Deep Learning", "AI")
        self.assertFalse(cleaned.startswith("```markdown"))
        self.assertTrue(cleaned.startswith("# Topic: Deep Learning"))

    def test_all_snippet_types(self):
        snippets = get_snippet_names()
        expected = [
            "LaTeX Display Equation",
            "LaTeX Inline Equation",
            "LaTeX Fraction & Integral",
            "Definition Callout",
            "Theorem & Proof Block",
            "High-Yield Checkpoint",
            "Comparison Table",
        ]
        for name in expected:
            self.assertIn(name, snippets)
            content = get_snippet(name)
            self.assertTrue(len(content) > 0, f"Snippet {name} was empty")

    def test_studio_staged_state_and_preview_workflow(self):
        from streamlit.testing.v1 import AppTest

        code = '''import sys
sys.path.insert(0, 'src')
import streamlit as st
from ai_notes_author import get_template, get_snippet
from notes_manager import parse_note_metadata

if 'author_editor_text' not in st.session_state:
    st.session_state['author_editor_text'] = ''
if 'author_pending_content' not in st.session_state:
    st.session_state['author_pending_content'] = None

if st.session_state.get('author_pending_content') is not None:
    st.session_state['author_editor_text'] = st.session_state.pop('author_pending_content')

if st.button('Apply Template', key='btn_tmpl'):
    st.session_state['author_pending_content'] = get_template('Academic Lecture & Core Concepts', topic='Entropy', subject='Physics')
    st.rerun()

if st.button('Insert Math', key='btn_math'):
    curr = st.session_state.get('author_editor_text', '')
    snip = get_snippet('LaTeX Display Equation')
    st.session_state['author_pending_content'] = curr + '\\n' + snip
    st.rerun()

editor = st.text_area('Editor', key='author_editor_text')
parsed = parse_note_metadata(editor)
preview_body = parsed.get('raw_body', editor)
st.markdown(preview_body, unsafe_allow_html=True)
'''
        at = AppTest.from_string(code).run()
        # Initial empty
        self.assertEqual(at.text_area(key="author_editor_text").value, "")

        # 1. Apply Template
        at.button(key="btn_tmpl").click().run()
        self.assertIn("Entropy", at.text_area(key="author_editor_text").value)
        self.assertIn("Entropy", at.markdown[0].value)

        # 2. Insert Math
        at.button(key="btn_math").click().run()
        self.assertIn("E = mc^2", at.text_area(key="author_editor_text").value)
        self.assertIn("E = mc^2", at.markdown[0].value)


class TestLatexSanitizationAndDerivations(unittest.TestCase):
    def test_sanitize_unwrapped_align_environment(self):
        raw_latex = "\\begin{align*}\n\\hat{y} &= w^T x \\\\\n&= f(x)\n\\end{align*}"
        sanitized = sanitize_latex_markdown(raw_latex)
        self.assertNotIn("\\begin{align*}", sanitized)
        self.assertIn("$$\n\\begin{aligned}", sanitized)
        self.assertIn("\\end{aligned}\n$$", sanitized)

    def test_sanitize_wrapped_align_environment(self):
        # Even if wrapped in $$, align* is invalid in KaTeX and must become aligned
        raw_latex = "$$\\begin{align*}\na &= b \\\\\nc &= d\n\\end{align*}$$"
        sanitized = sanitize_latex_markdown(raw_latex)
        self.assertNotIn("align*", sanitized)
        self.assertIn("\\begin{aligned}", sanitized)
        self.assertIn("$$", sanitized)

    def test_sanitize_brackets(self):
        # Display \[ ... \] and inline \( ... \)
        raw_latex = "\\[ E = mc^2 \\] and \\( x \\in \\mathbb{R} \\)"
        sanitized = sanitize_latex_markdown(raw_latex)
        self.assertNotIn("\\[", sanitized)
        self.assertNotIn("\\(", sanitized)
        self.assertIn("$$\nE = mc^2\n$$", sanitized)
        self.assertIn("$x \\in \\mathbb{R}$", sanitized)

    def test_protect_code_blocks_and_spans(self):
        raw_markdown = "```latex\n\\begin{align*}\na = 1\n\\end{align*}\n```\nAnd `\\begin{align*}` inline."
        sanitized = sanitize_latex_markdown(raw_markdown)
        self.assertIn("```latex\n\\begin{align*}\na = 1\n\\end{align*}\n```", sanitized)
        self.assertIn("`\\begin{align*}`", sanitized)

    def test_clean_generated_markdown_sanitizes_latex(self):
        ai_output = """# Topic: Logistic Regression
## Course: Machine Learning

### Core Theory & Derivations
\\begin{align*}
p &= \\sigma(z) \\\\
&= \\frac{1}{1 + e^{-z}}
\\end{align*}
"""
        cleaned = clean_generated_markdown(ai_output, "Logistic Regression", "Machine Learning")
        self.assertNotIn("\\begin{align*}", cleaned)
        self.assertIn("$$\n\\begin{aligned}", cleaned)
        self.assertIn("\\end{aligned}\n$$", cleaned)

    def test_classification_vs_regression_note_has_valid_latex(self):
        # Verify the actual note in data/notes has no raw unwrapped align*
        note_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "notes", "Machine Learning", "classification_vs._regression.md"
        )
        if os.path.exists(note_path):
            with open(note_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("\\begin{align*}", content)
            self.assertIn("\\begin{aligned}", content)
            self.assertIn("$$", content)

    def test_starter_templates_include_historical_and_deep_analysis(self):
        names = get_template_names()
        self.assertIn("Historical Context & Conceptual Narrative", names)
        self.assertIn("Deep In-Depth Analysis", names)
        self.assertIn("Written Academic Exposition", names)

        # Test rendering
        hist_tmpl = get_template("Historical Context & Conceptual Narrative", topic="Electromagnetism", subject="Physics")
        self.assertIn("Electromagnetism", hist_tmpl)
        self.assertIn("Historical Genesis", hist_tmpl)

        deep_tmpl = get_template("Deep In-Depth Analysis", topic="Attention Mechanism", subject="Machine Learning")
        self.assertIn("Attention Mechanism", deep_tmpl)
        self.assertIn("Comparative Paradigms & Trade-Offs", deep_tmpl)

    def test_ai_note_generation_prompt_detail_levels(self):
        from unittest.mock import patch
        from ai_notes_author import generate_ai_study_note

        captured_prompts = []

        def fake_ask_ollama(model, prompt):
            captured_prompts.append(prompt)
            return f"# Topic: Test\n## Course: Test\n\nContent for {model}"

        with patch("ai_notes_author.ask_ollama", side_effect=fake_ask_ollama):
            # 1. Historical & Conceptual Narrative
            res_hist = generate_ai_study_note(
                subject="Quantum Mechanics",
                topic="Wave-Particle Duality",
                detail_level="Historical & Conceptual Narrative",
                ground_with_wiki=False,
            )
            self.assertTrue(res_hist["success"])
            self.assertIn("historical evolution", captured_prompts[-1].lower())
            self.assertIn("Historical Genesis", captured_prompts[-1])

            # 2. Deep In-Depth Analysis
            res_deep = generate_ai_study_note(
                subject="Quantum Mechanics",
                topic="Heisenberg Uncertainty Principle",
                detail_level="Deep In-Depth Analysis",
                ground_with_wiki=False,
            )
            self.assertTrue(res_deep["success"])
            self.assertIn("masterclass deep-dive analysis", captured_prompts[-1].lower())
            self.assertIn("Comparative Paradigms", captured_prompts[-1])

    def test_ai_enhance_existing_note_actions(self):
        from unittest.mock import patch
        from ai_notes_author import enhance_existing_note

        captured_prompts = []

        def fake_ask_ollama(model, prompt):
            captured_prompts.append(prompt)
            return prompt

        with patch("ai_notes_author.ask_ollama", side_effect=fake_ask_ollama):
            res_hist = enhance_existing_note("# My Note", action="add_historical_context")
            self.assertTrue(res_hist["success"])
            self.assertIn("Historical Genesis & Evolutionary Development", captured_prompts[-1])

            res_deep = enhance_existing_note("# My Note", action="deep_analysis")
            self.assertTrue(res_deep["success"])
            self.assertIn("deep dive", captured_prompts[-1].lower())

    def test_streamlit_ai_authoring_subject_selection_and_depth_options(self):
        from streamlit.testing.v1 import AppTest
        from notes_manager import list_subject_folders

        # Use tracked sample notes so the test is independent of the local vault.
        folders = list_subject_folders(os.path.join("examples", "notes"))
        self.assertTrue(len(folders) > 0)

        code = '''import sys
sys.path.insert(0, 'src')
import streamlit as st
from notes_manager import list_subject_folders

existing_folders = list_subject_folders("examples/notes")
if not existing_folders:
    existing_folders = ["General"]

subj_dropdown_options = list(existing_folders)
if "+ New Subject Folder..." not in subj_dropdown_options:
    subj_dropdown_options.append("+ New Subject Folder...")

curr_saved_subj = st.session_state.get("author_subject", existing_folders[0])
folder_index = subj_dropdown_options.index(curr_saved_subj) if curr_saved_subj in subj_dropdown_options else 0

selected_folder_opt = st.selectbox(
    "Target Subject Folder",
    subj_dropdown_options,
    index=folder_index,
    key="ai_gen_subj_select",
)

detail_level_options = [
    "Standard Academic Lecture",
    "Deep In-Depth Analysis",
    "Historical & Conceptual Narrative",
    "Written Academic Exposition",
    "Rigorous Mathematical Treatment",
    "Concise Overview",
]

gen_detail = st.selectbox(
    "Detail & Depth Level",
    detail_level_options,
    index=0,
    key="ai_gen_detail_select",
)
st.write(f"Selected Folder: {selected_folder_opt}")
st.write(f"Selected Detail: {gen_detail}")
'''
        at = AppTest.from_string(code).run()
        # Verify selectbox for subject folders is present and contains existing folders
        subj_sb = at.selectbox(key="ai_gen_subj_select")
        self.assertIsNotNone(subj_sb)
        for f in folders:
            self.assertIn(f, subj_sb.options)
        self.assertIn("+ New Subject Folder...", subj_sb.options)

        # Verify selectbox for detail & depth levels
        detail_sb = at.selectbox(key="ai_gen_detail_select")
        self.assertIsNotNone(detail_sb)
        self.assertIn("Deep In-Depth Analysis", detail_sb.options)
        self.assertIn("Historical & Conceptual Narrative", detail_sb.options)
        self.assertIn("Written Academic Exposition", detail_sb.options)


if __name__ == "__main__":
    unittest.main()

