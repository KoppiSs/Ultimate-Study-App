# Ultimate Study Application

A local, single-user Streamlit app for course planning, timed study sessions, Markdown notes, and tutoring with Ollama. The planner and notes work without Ollama or Wikipedia.

## Run locally

The setup below uses Python 3.14. From the project directory on Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run src/app.py
```

On macOS or Linux, use `python3.14 -m venv .venv` and `.venv/bin/python` for the last two commands. The default Streamlit configuration binds to the loopback interface. The app uses Google Fonts for some browser icons.

The first launch with fresh default storage creates a fictional course, a Markdown note, and a sample study session for the next day. See `examples/` for the starter course and note. All edits are stored locally under `data/` by default.

## Use your own study data

In **Settings → Storage & Backup**, set **Study data folder** to use a different workspace. The folder path can be absolute or relative to this project. Individual overrides can be absolute or relative to the selected study data folder. Blank overrides use these paths:

| Setting | Path inside the study data folder |
| --- | --- |
| Markdown notes folder | `notes/` |
| Courses JSON file | `courses.json` |
| Study sessions folder | `sessions/` |
| Legacy study blocks JSON file | `study_blocks.json` |
| SQLite database file used by the legacy scripts | `notes/study_core.db` |

Switching paths opens the selected data in place. Settings are saved to `data/settings.json`. Set `SETTINGS_FILE` before launch if you want the settings file itself elsewhere. To move an existing study data folder, close the app, move the folder, and update **Study data folder** after restarting. Update any absolute overrides that pointed into the old location.

**Export study data** downloads the active Markdown notes, courses, sessions, and legacy block records. Copy your SQLite database and settings separately if you use them. Keep backups private.

## Optional local AI and Wikipedia

1. Install and start [Ollama](https://ollama.com/download). Download your preferred model with `ollama pull MODEL_NAME`, replacing `MODEL_NAME` with its Ollama name.
2. In **Settings → Local AI & Wikipedia**, set the model name to the one you installed. The default base URL is `http://localhost:11434`. Tutor and authoring prompts can include excerpts from your notes and are sent to the configured endpoint.
3. Leave **Enable offline Wikipedia** off to work without an archive. Also turn off **Use Markdown notes in tutor replies** if you want replies based only on the model. To add offline grounding, install `requirements-wikipedia.txt`, download a suitable archive from the [Kiwix library](https://library.kiwix.org/), enter its path as **Wikipedia ZIM file**, and enable the option. The app reads the ZIM directly. If the file or its reader is unavailable, model and notes features remain available.

To use the optional Wikipedia MCP server, run `python src/wiki_server.py --mcp` with the Wikipedia dependencies installed. `examples/mcp_config.json` shows the command and arguments. Adapt its interpreter and workspace variable to your MCP client. `ZIM_PATH` can supply the archive path for command-line use.

## Tests

```text
python -m pip install pytest
python -m pytest -q
```

The direct Wikipedia integration test needs an English Wikipedia ZIM and `requirements-wikipedia.txt`. The legacy Kiwix HTTP scraper test runs when `RUN_KIWIX_TESTS=1` and a server is available. Set `KIWIX_SERVER` and `KIWIX_ARCHIVE_ID` for a custom server or archive. The core test suite does not need a ZIM or Kiwix server.

## Preparing a public repository

Keep runtime storage, local credentials, and personal exports out of Git. If you use a custom workspace inside the checkout, add it to `.gitignore` before staging. Review the staged file list and diff for credentials, personal information, and absolute paths. Use a GitHub-provided `noreply` author email if you want to keep your email address private. Share screenshots made from the fictional workspace.


## License

Released under the [MIT License](LICENSE).
