from __future__ import annotations

import json
import os
import tempfile
import uuid
import subprocess
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import gradio as gr


DEFAULT_USER_NAME = os.getenv("TEXT_TOOL_USER_NAME", "IAFahim")
LOCAL_DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR = Path("/data") if Path("/data").is_dir() and os.access("/data", os.W_OK) else LOCAL_DATA_DIR
HISTORY_PATH = DATA_DIR / "history.json"

active_processes = {}
is_hf_space = os.getenv("SPACE_ID") is not None
default_run_loc = "Local Bridge (ws://localhost:7890)" if is_hf_space else "Local System (Subprocess)"

def to_html_console(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f"<pre style='margin: 0; font-family: inherit;'>{escaped}</pre>"

LAYOUT_CSS = """
.app-shell {
    align-items: flex-start !important;
    gap: 16px !important;
}
.sidebar-panel {
    max-width: 300px !important;
}
.editor-panel {
    max-width: 760px !important;
}
.editor-panel-full {
    max-width: 1060px !important;
}
.layout-top,
.layout-name,
.layout-bottom {
    align-items: center !important;
    flex-wrap: nowrap !important;
}
.layout-top .form,
.layout-name .form,
.layout-bottom .form {
    min-width: 0 !important;
}
.main-editor textarea {
    min-height: 520px !important;
}
.code-snippet textarea {
    font-family: monospace !important;
}
.arrow-btn {
    min-width: 32px !important;
    max-width: 48px !important;
    padding-left: 4px !important;
    padding-right: 4px !important;
}
#snippet-console {
    font-family: monospace !important;
    background-color: #121212 !important;
    color: #00ff66 !important;
    padding: 12px !important;
    border-radius: 6px !important;
    height: 240px !important;
    min-height: 240px !important;
    max-height: 480px !important;
    overflow-y: auto !important;
    white-space: pre-wrap !important;
    word-break: break-all !important;
    border: 1px solid #2d2d2d !important;
}
"""

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def display_user(name: str | None) -> str:
    clean = (name or "").strip()
    return clean or "Global"


def new_section(title: str | None = None, text: str = "") -> dict[str, str]:
    return {
        "id": str(uuid.uuid4()),
        "title": title or "Untitled",
        "text": text or "",
        "code": "",
        "console": "",
    }


def blank_sections() -> list[dict[str, str]]:
    return [new_section("Tab 1")]


def normalize_sections(draft: dict[str, Any]) -> list[dict[str, str]]:
    raw_sections = draft.get("sections")
    if isinstance(raw_sections, list) and raw_sections:
        sections = []
        for index, section in enumerate(raw_sections, start=1):
            if not isinstance(section, dict):
                continue
            sections.append(
                {
                    "id": str(section.get("id") or uuid.uuid4()),
                    "title": str(section.get("title") or f"Tab {index}"),
                    "text": str(section.get("text") or ""),
                    "code": str(section.get("code") or ""),
                    "console": str(section.get("console") or ""),
                }
            )
        if sections:
            return sections

    segments = draft.get("segments")
    if isinstance(segments, list) and segments:
        return [
            new_section(f"Segment {index}", str(text or ""))
            for index, text in enumerate(segments, start=1)
            if str(text or "").strip()
        ] or blank_sections()

    return blank_sections()


def tab_label(index: int, section: dict[str, str]) -> str:
    title = " ".join((section.get("title") or f"Tab {index + 1}").split())
    return f"{index + 1}. {title[:36]}"


def tab_choices(sections: list[dict[str, str]]) -> list[str]:
    return [tab_label(index, section) for index, section in enumerate(sections)]


def active_label(sections: list[dict[str, str]], index: int) -> str | None:
    if not sections:
        return None
    index = max(0, min(index, len(sections) - 1))
    return tab_label(index, sections[index])


def parse_tab_index(label: str | None, sections: list[dict[str, str]]) -> int:
    if not label:
        return 0
    prefix = label.split(".", 1)[0]
    if prefix.isdigit():
        return max(0, min(int(prefix) - 1, len(sections) - 1))
    return 0


def commit_editor_text(
    sections: list[dict[str, str]],
    active_index: int,
    text: str | None,
    code: str | None = None,
    console: str | None = None,
) -> list[dict[str, str]]:
    sections = [dict(section) for section in (sections or blank_sections())]
    active_index = max(0, min(active_index or 0, len(sections) - 1))
    sections[active_index]["text"] = text or ""
    if code is not None:
        sections[active_index]["code"] = code or ""
    if console is not None:
        sections[active_index]["console"] = console or ""
    return sections


def combine_sections(sections: list[dict[str, str]]) -> str:
    parts = []
    for section in sections or []:
        title = (section.get("title") or "Untitled").strip()
        text = (section.get("text") or "").strip()
        if text:
            parts.append(f"## {title}\n{text}")
    return "\n\n".join(parts)


def title_from_sections(sections: list[dict[str, str]]) -> str:
    for section in sections:
        text = " ".join((section.get("text") or section.get("title") or "").strip().split())
        if text:
            return text[:60]
    return "Untitled draft"


def format_timestamp(iso_str: str, offset_minutes: str | None = None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if offset_minutes is not None:
            try:
                offset = int(offset_minutes)
                dt = dt.astimezone(timezone(timedelta(minutes=-offset)))
            except Exception:
                pass
        return dt.strftime("%b %d, %Y · %H:%M")
    except Exception:
        return iso_str


def draft_label(draft: dict[str, Any], client_tz: str | None = None) -> str:
    title = draft.get("title") or "Untitled draft"
    updated = draft.get("updated_at") or ""
    return f"{title} | {format_timestamp(updated, client_tz)}"


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []

    try:
        payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    drafts = payload.get("drafts", []) if isinstance(payload, dict) else []
    if not isinstance(drafts, list):
        return []

    normalized: list[dict[str, Any]] = []
    for draft in drafts:
        if not isinstance(draft, dict):
            continue
        sections = normalize_sections(draft)
        normalized.append(
            {
                "id": str(draft.get("id") or uuid.uuid4()),
                "owner": str(draft.get("owner") or DEFAULT_USER_NAME),
                "title": str(draft.get("title") or title_from_sections(sections)),
                "created_at": str(draft.get("created_at") or now_iso()),
                "updated_at": str(draft.get("updated_at") or now_iso()),
                "sections": sections,
            }
        )
    return normalized


def save_history(drafts: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=DATA_DIR, encoding="utf-8") as handle:
        json.dump({"drafts": drafts}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, HISTORY_PATH)


def history_choices(drafts: list[dict[str, Any]], query: str = "", client_tz: str | None = None) -> list[str]:
    clean_query = query.strip().lower()
    filtered = []
    for draft in drafts:
        haystack = " ".join(
            [
                str(draft.get("title") or ""),
                " ".join(section.get("title", "") for section in draft.get("sections", [])),
                " ".join(section.get("text", "") for section in draft.get("sections", [])),
            ]
        ).lower()
        if not clean_query or clean_query in haystack:
            filtered.append(draft)
    return [draft_label(draft, client_tz) for draft in sorted(filtered, key=lambda item: item["updated_at"], reverse=True)]


def find_draft_by_label(drafts: list[dict[str, Any]], label: str | None, client_tz: str | None = None) -> dict[str, Any] | None:
    for draft in drafts or []:
        if draft_label(draft, client_tz) == label:
            return draft
    return None


def load_app(client_tz: str | None = None) -> tuple[Any, ...]:
    drafts = load_history()
    choices = history_choices(drafts, client_tz=client_tz)
    selected = choices[0] if choices else None
    draft = find_draft_by_label(drafts, selected, client_tz=client_tz) if selected else None
    sections = draft["sections"] if draft else blank_sections()
    active_index = 0
    console_text = sections[active_index].get("console", "")
    console_html = to_html_console(console_text) if console_text else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        drafts,
        gr.update(choices=choices, value=selected),
        sections,
        active_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, active_index)),
        sections[active_index]["text"],
        sections[active_index].get("code", ""),
        sections[active_index]["title"],
        draft.get("title", "Untitled draft") if draft else "Untitled draft",
        combine_sections(sections),
        f"Loaded {len(drafts)} recent draft(s).",
        console_html,
        console_text,
    )


def search_history(drafts: list[dict[str, Any]], query: str, client_tz: str | None = None) -> Any:
    choices = history_choices(drafts or [], query, client_tz=client_tz)
    return gr.update(choices=choices, value=choices[0] if choices else None)


def select_history(drafts: list[dict[str, Any]], selected_label: str | None, client_tz: str | None = None) -> tuple[Any, ...]:
    draft = find_draft_by_label(drafts or [], selected_label, client_tz=client_tz)
    sections = draft["sections"] if draft else blank_sections()
    active_index = 0
    console_text = sections[active_index].get("console", "")
    console_html = to_html_console(console_text) if console_text else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        sections,
        active_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, active_index)),
        sections[active_index]["text"],
        sections[active_index].get("code", ""),
        sections[active_index]["title"],
        draft.get("title", "Untitled draft") if draft else "Untitled draft",
        combine_sections(sections),
        "Loaded selection." if draft else "No draft selected.",
        console_html,
        console_text,
    )


def new_draft() -> tuple[Any, ...]:
    sections = blank_sections()
    placeholder = "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        gr.update(value=None),
        sections,
        0,
        gr.update(choices=tab_choices(sections), value=active_label(sections, 0)),
        "",
        "",
        sections[0]["title"],
        "Untitled draft",
        "",
        "New draft.",
        placeholder,
        "",
    )


def save_current(
    drafts: list[dict[str, Any]],
    selected_label: str | None,
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
    draft_title: str,
    user_name: str,
    client_tz: str | None = None,
) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    timestamp = now_iso()
    existing = find_draft_by_label(drafts, selected_label, client_tz=client_tz)
    title = (draft_title or "").strip() or title_from_sections(sections)

    if existing:
        existing.update(
            {
                "owner": display_user(user_name),
                "title": title,
                "updated_at": timestamp,
                "sections": sections,
            }
        )
        saved = existing
    else:
        saved = {
            "id": str(uuid.uuid4()),
            "owner": display_user(user_name),
            "title": title,
            "created_at": timestamp,
            "updated_at": timestamp,
            "sections": sections,
        }
        drafts.append(saved)

    save_history(drafts)
    choices = history_choices(drafts, client_tz=client_tz)
    return (
        drafts,
        gr.update(choices=choices, value=draft_label(saved, client_tz)),
        sections,
        combine_sections(sections),
        "Saved.",
    )


def copy_and_save_current(
    drafts: list[dict[str, Any]],
    selected_label: str | None,
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
    draft_title: str,
    user_name: str,
    client_tz: str | None = None,
) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    timestamp = now_iso()
    existing = find_draft_by_label(drafts, selected_label, client_tz=client_tz)
    title = (draft_title or "").strip() or title_from_sections(sections)

    if existing:
        existing.update(
            {
                "owner": display_user(user_name),
                "title": title,
                "updated_at": timestamp,
                "sections": sections,
            }
        )
        saved = existing
    else:
        saved = {
            "id": str(uuid.uuid4()),
            "owner": display_user(user_name),
            "title": title,
            "created_at": timestamp,
            "updated_at": timestamp,
            "sections": sections,
        }
        drafts.append(saved)

    save_history(drafts)
    choices = history_choices(drafts, client_tz=client_tz)
    combined = combine_sections(sections)
    return (
        drafts,
        gr.update(choices=choices, value=draft_label(saved, client_tz)),
        sections,
        combined,
        "Saved and copied.",
        combined,
    )


def fork_current(
    drafts: list[dict[str, Any]],
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
    draft_title: str,
    user_name: str,
    client_tz: str | None = None,
) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    timestamp = now_iso()
    forked = {
        "id": str(uuid.uuid4()),
        "owner": display_user(user_name),
        "title": f"{((draft_title or '').strip() or title_from_sections(sections))} copy",
        "created_at": timestamp,
        "updated_at": timestamp,
        "sections": sections,
    }
    drafts.append(forked)
    save_history(drafts)
    choices = history_choices(drafts, client_tz=client_tz)
    return drafts, gr.update(choices=choices, value=draft_label(forked, client_tz)), forked["title"], "Forked."


def delete_current(drafts: list[dict[str, Any]], selected_label: str | None, client_tz: str | None = None) -> tuple[Any, ...]:
    drafts = [draft for draft in (drafts or []) if draft_label(draft, client_tz) != selected_label]
    save_history(drafts)
    choices = history_choices(drafts, client_tz=client_tz)
    selected = choices[0] if choices else None
    draft = find_draft_by_label(drafts, selected, client_tz=client_tz) if selected else None
    sections = draft["sections"] if draft else blank_sections()
    console_text = sections[0].get("console", "")
    console_html = to_html_console(console_text) if console_text else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        drafts,
        gr.update(choices=choices, value=selected),
        sections,
        0,
        gr.update(choices=tab_choices(sections), value=active_label(sections, 0)),
        sections[0]["text"],
        sections[0].get("code", ""),
        sections[0]["title"],
        draft.get("title", "Untitled draft") if draft else "Untitled draft",
        combine_sections(sections),
        "Deleted draft.",
        console_html,
        console_text,
    )


def select_tab(
    sections: list[dict[str, str]],
    active_index: int,
    active_tab: str | None,
    editor_text: str,
    code_text: str,
    console_text: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    next_index = parse_tab_index(active_tab, sections)
    next_console = sections[next_index].get("console", "")
    next_console_html = to_html_console(next_console) if next_console else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        sections,
        next_index,
        sections[next_index]["text"],
        sections[next_index].get("code", ""),
        sections[next_index]["title"],
        combine_sections(sections),
        "",  # Clear status
        next_console_html,
        next_console,
    )


def update_active_text(sections: list[dict[str, str]], active_index: int, editor_text: str) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text)
    return sections, combine_sections(sections), ""  # Clear status


def update_active_code(
    sections: list[dict[str, str]],
    active_index: int,
    code_text: str,
) -> tuple[Any, ...]:
    sections = [dict(section) for section in (sections or blank_sections())]
    active_index = max(0, min(active_index or 0, len(sections) - 1))
    sections[active_index]["code"] = code_text or ""
    return sections, ""  # Clear status


def rename_active_tab(
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    tab_title: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text, code_text)
    active_index = max(0, min(active_index or 0, len(sections) - 1))
    sections[active_index]["title"] = (tab_title or "").strip() or f"Tab {active_index + 1}"
    return (
        sections,
        gr.update(choices=tab_choices(sections), value=active_label(sections, active_index)),
        combine_sections(sections),
        "",  # Clear status
    )


def add_tab(
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    sections.append(new_section(f"Tab {len(sections) + 1}"))
    next_index = len(sections) - 1
    return (
        sections,
        next_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, next_index)),
        "",
        "",
        sections[next_index]["title"],
        combine_sections(sections),
        "Added tab.",
        "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>",
        "",
    )


def delete_tab(
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    if len(sections) > 1:
        del sections[max(0, min(active_index or 0, len(sections) - 1))]
    next_index = max(0, min(active_index or 0, len(sections) - 1))
    next_console = sections[next_index].get("console", "")
    next_console_html = to_html_console(next_console) if next_console else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        sections,
        next_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, next_index)),
        sections[next_index]["text"],
        sections[next_index].get("code", ""),
        sections[next_index]["title"],
        combine_sections(sections),
        "Deleted tab." if len(sections) > 1 else "Kept the last tab.",
        next_console_html,
        next_console,
    )


def move_tab_up(
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    if active_index > 0:
        sections[active_index], sections[active_index - 1] = sections[active_index - 1], sections[active_index]
        next_index = active_index - 1
        msg = "Moved section up."
    else:
        next_index = active_index
        msg = "Already at the top."
    next_console = sections[next_index].get("console", "")
    next_console_html = to_html_console(next_console) if next_console else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        sections,
        next_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, next_index)),
        sections[next_index]["text"],
        sections[next_index].get("code", ""),
        sections[next_index]["title"],
        combine_sections(sections),
        msg,
        next_console_html,
        next_console,
    )


def move_tab_down(
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    code_text: str,
    console_text: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text, code_text, console_text)
    if active_index < len(sections) - 1:
        sections[active_index], sections[active_index + 1] = sections[active_index + 1], sections[active_index]
        next_index = active_index + 1
        msg = "Moved section down."
    else:
        next_index = active_index
        msg = "Already at the bottom."
    next_console = sections[next_index].get("console", "")
    next_console_html = to_html_console(next_console) if next_console else "<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>"
    return (
        sections,
        next_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, next_index)),
        sections[next_index]["text"],
        sections[next_index].get("code", ""),
        sections[next_index]["title"],
        combine_sections(sections),
        msg,
        next_console_html,
        next_console,
    )


def toggle_sidebar(open_state: bool) -> tuple[Any, ...]:
    next_state = not bool(open_state)
    editor_classes = ["editor-panel"] if next_state else ["editor-panel-full"]
    return next_state, gr.update(visible=next_state), gr.update(elem_classes=editor_classes), "☰"


async def run_local_cmd(cmd: str, session_id: str):
    if not cmd.strip():
        yield to_html_console("Error: Code snippet is empty."), gr.update(visible=True), gr.update(visible=False), "Error: Code snippet is empty."
        return

    if session_id in active_processes:
        try:
            active_processes[session_id].kill()
        except Exception:
            pass
        del active_processes[session_id]

    yield to_html_console("Starting command...\n"), gr.update(visible=False), gr.update(visible=True), "Starting command...\n"
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        active_processes[session_id] = proc
        
        output = ""
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            output += line.decode(errors="replace")
            yield to_html_console(output), gr.update(visible=False), gr.update(visible=True), output
                
        rc = await proc.wait()
        output += f"\n--- Process exited with code {rc} ---"
        if session_id in active_processes:
            del active_processes[session_id]
        yield to_html_console(output), gr.update(visible=True), gr.update(visible=False), output
    except Exception as e:
        yield to_html_console(f"Error executing command: {e}"), gr.update(visible=True), gr.update(visible=False), f"Error executing command: {e}"


def handle_stop_click(run_loc: str, session_id: str):
    if run_loc == "Local System (Subprocess)":
        if session_id in active_processes:
            try:
                active_processes[session_id].kill()
            except Exception:
                pass
            del active_processes[session_id]
        return to_html_console("--- Process terminated by user ---"), gr.update(visible=True), gr.update(visible=False), "--- Process terminated by user ---"
    else:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip()


async def handle_run_click(cmd: str, run_loc: str, session_id: str):
    if run_loc == "Local System (Subprocess)":
        async for out, r_vis, s_vis, raw_out in run_local_cmd(cmd, session_id):
            yield out, r_vis, s_vis, raw_out
    else:
        yield gr.skip(), gr.skip(), gr.skip(), gr.skip()


with gr.Blocks(title="Text Tool", fill_height=True, css=LAYOUT_CSS) as demo:
    client_timezone = gr.Textbox(visible=False, value="0")
    hidden_console = gr.Textbox(visible=False, elem_id="hidden-console-saver")
    drafts_state = gr.State([])
    sections_state = gr.State(blank_sections())
    active_index_state = gr.State(0)
    sidebar_open_state = gr.State(True)
    session_id_state = gr.State(lambda: str(uuid.uuid4()))

    with gr.Row(elem_classes="app-shell"):
        with gr.Column(scale=1, min_width=280, elem_classes="sidebar-panel") as sidebar:
            user_name = gr.Textbox(
                value=DEFAULT_USER_NAME,
                placeholder="Global",
                label="Workspace",
                show_label=False,
            )
            new_button = gr.Button("+ New draft")
            search = gr.Textbox(
                placeholder="Search drafts",
                label="Search",
                show_label=False,
            )
            gr.Markdown("### Recents")
            history = gr.Radio(label="Recents", choices=[], interactive=True)
            with gr.Row():
                fork_button = gr.Button("Fork")
                delete_button = gr.Button("Delete")
            gr.Markdown("Persistent HF storage")

        with gr.Column(scale=2, min_width=520, elem_classes="editor-panel") as editor_panel:
            with gr.Row(elem_classes="layout-top"):
                toggle_button = gr.Button("☰", scale=0)
                draft_title = gr.Textbox(
                    value="Untitled draft",
                    label="Draft title",
                    show_label=False,
                    scale=6,
                )
                copy_button = gr.Button("Copy", scale=0, variant="primary")

            with gr.Row(elem_classes="layout-name"):
                tab_title = gr.Textbox(
                    label="Tab name",
                    show_label=False,
                    placeholder="Name/Rename",
                    scale=4,
                )
                add_tab_button = gr.Button("+ Tab", scale=0)

            code_snippet = gr.Code(
                label="Code Snippet",
                language="shell",
                lines=3,
                max_lines=10,
            )

            with gr.Row():
                run_loc = gr.Dropdown(
                    choices=["Local System (Subprocess)", "Local Bridge (ws://localhost:7890)"],
                    value=default_run_loc,
                    label="Run Location",
                    show_label=False,
                    container=False,
                    scale=2,
                )
                run_snippet_btn = gr.Button("▶ Run", variant="secondary", scale=1, elem_id="run-snippet-btn")
                stop_snippet_btn = gr.Button("⏹ Stop", variant="stop", scale=1, elem_id="stop-snippet-btn", visible=False)

            gr.Markdown("### Console Output")
            console_output = gr.HTML(
                value="<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>",
                elem_id="snippet-console",
            )

            editor = gr.Textbox(label="Text", lines=24, max_lines=80, elem_classes="main-editor")

            with gr.Row(elem_classes="layout-bottom"):
                active_tab = gr.Dropdown(label="Section", show_label=False, choices=[], interactive=True, scale=4)
                move_up_button = gr.Button("◀", scale=0, elem_classes=["arrow-btn"])
                move_down_button = gr.Button("▶", scale=0, elem_classes=["arrow-btn"])
                delete_tab_button = gr.Button("Delete section", scale=0, variant="stop")
            rename_tab_button = gr.Button("Rename", visible=False)
            save_button = gr.Button("Save", visible=False)

            with gr.Accordion("Combined text", open=False):
                combined = gr.Textbox(
                    label="Combined text",
                    lines=10,
                    max_lines=24,
                    interactive=False,
                )
            copy_payload = gr.Textbox(visible=False)
            status = gr.Markdown()

    demo.load(
        fn=None,
        inputs=None,
        outputs=client_timezone,
        js="() => { return String(new Date().getTimezoneOffset()); }"
    )

    client_timezone.change(
        load_app,
        inputs=[client_timezone],
        outputs=[
            drafts_state,
            history,
            sections_state,
            active_index_state,
            active_tab,
            editor,
            code_snippet,
            tab_title,
            draft_title,
            combined,
            status,
            console_output,
            hidden_console,
        ],
    )

    search.change(search_history, inputs=[drafts_state, search, client_timezone], outputs=history)

    toggle_button.click(
        toggle_sidebar,
        inputs=sidebar_open_state,
        outputs=[sidebar_open_state, sidebar, editor_panel, toggle_button],
    )

    history.change(
        select_history,
        inputs=[drafts_state, history, client_timezone],
        outputs=[
            sections_state,
            active_index_state,
            active_tab,
            editor,
            code_snippet,
            tab_title,
            draft_title,
            combined,
            status,
            console_output,
            hidden_console,
        ],
    )

    new_button.click(
        new_draft,
        outputs=[
            history,
            sections_state,
            active_index_state,
            active_tab,
            editor,
            code_snippet,
            tab_title,
            draft_title,
            combined,
            status,
            console_output,
            hidden_console,
        ],
    )

    save_button.click(
        save_current,
        inputs=[drafts_state, history, sections_state, active_index_state, editor, code_snippet, hidden_console, draft_title, user_name, client_timezone],
        outputs=[drafts_state, history, sections_state, combined, status],
    )

    fork_button.click(
        fork_current,
        inputs=[drafts_state, sections_state, active_index_state, editor, code_snippet, hidden_console, draft_title, user_name, client_timezone],
        outputs=[drafts_state, history, draft_title, status],
    )

    delete_button.click(
        delete_current,
        inputs=[drafts_state, history, client_timezone],
        outputs=[
            drafts_state,
            history,
            sections_state,
            active_index_state,
            active_tab,
            editor,
            code_snippet,
            tab_title,
            draft_title,
            combined,
            status,
            console_output,
            hidden_console,
        ],
    )

    active_tab.change(
        select_tab,
        inputs=[sections_state, active_index_state, active_tab, editor, code_snippet, hidden_console],
        outputs=[sections_state, active_index_state, editor, code_snippet, tab_title, combined, status, console_output, hidden_console],
    )

    editor.blur(
        update_active_text,
        inputs=[sections_state, active_index_state, editor],
        outputs=[sections_state, combined, status],
    )

    code_snippet.blur(
        update_active_code,
        inputs=[sections_state, active_index_state, code_snippet],
        outputs=[sections_state, status],
    )

    tab_title.blur(
        rename_active_tab,
        inputs=[sections_state, active_index_state, editor, code_snippet, tab_title],
        outputs=[sections_state, active_tab, combined, status],
    )
    tab_title.submit(
        rename_active_tab,
        inputs=[sections_state, active_index_state, editor, code_snippet, tab_title],
        outputs=[sections_state, active_tab, combined, status],
    )

    rename_tab_button.click(
        rename_active_tab,
        inputs=[sections_state, active_index_state, editor, code_snippet, tab_title],
        outputs=[sections_state, active_tab, combined, status],
    )

    add_tab_button.click(
        add_tab,
        inputs=[sections_state, active_index_state, editor, code_snippet, hidden_console],
        outputs=[sections_state, active_index_state, active_tab, editor, code_snippet, tab_title, combined, status, console_output, hidden_console],
    )

    delete_tab_button.click(
        delete_tab,
        inputs=[sections_state, active_index_state, editor, code_snippet, hidden_console],
        outputs=[sections_state, active_index_state, active_tab, editor, code_snippet, tab_title, combined, status, console_output, hidden_console],
    )

    move_up_button.click(
        move_tab_up,
        inputs=[sections_state, active_index_state, editor, code_snippet, hidden_console],
        outputs=[
            sections_state,
            active_index_state,
            active_tab,
            editor,
            code_snippet,
            tab_title,
            combined,
            status,
            console_output,
            hidden_console,
        ],
    )

    move_down_button.click(
        move_tab_down,
        inputs=[sections_state, active_index_state, editor, code_snippet, hidden_console],
        outputs=[
            sections_state,
            active_index_state,
            active_tab,
            editor,
            code_snippet,
            tab_title,
            combined,
            status,
            console_output,
            hidden_console,
        ],
    )

    copy_button.click(
        copy_and_save_current,
        inputs=[drafts_state, history, sections_state, active_index_state, editor, code_snippet, hidden_console, draft_title, user_name, client_timezone],
        outputs=[drafts_state, history, sections_state, combined, status, copy_payload],
    ).then(
        lambda text: "Saved and copied.",
        inputs=copy_payload,
        outputs=status,
        js="(text) => { navigator.clipboard.writeText(text || ''); return 'Saved and copied.'; }",
    )

    run_snippet_btn.click(
        fn=handle_run_click,
        inputs=[code_snippet, run_loc, session_id_state],
        outputs=[console_output, run_snippet_btn, stop_snippet_btn, hidden_console],
        js="""(code, runLoc) => {
            if (runLoc === "Local Bridge (ws://localhost:7890)") {
                const consoleEl = document.getElementById('snippet-console');
                const runBtn = document.getElementById('run-snippet-btn');
                const stopBtn = document.getElementById('stop-snippet-btn');
                
                if (runBtn) runBtn.style.setProperty('display', 'none', 'important');
                if (stopBtn) stopBtn.style.removeProperty('display');
                
                let consoleText = "Connecting to local bridge at ws://localhost:7890...\\n";
                
                const updateConsole = (newText) => {
                    if (!consoleEl) return;
                    consoleText += newText;
                    const escaped = consoleText
                        .replace(/&/g, "&amp;")
                        .replace(/</g, "&lt;")
                        .replace(/>/g, "&gt;");
                    consoleEl.innerHTML = `<pre style="margin: 0; font-family: inherit;">${escaped}</pre>`;
                    consoleEl.scrollTop = consoleEl.scrollHeight;
                    
                    const hiddenEl = document.querySelector('#hidden-console-saver textarea');
                    if (hiddenEl) {
                        hiddenEl.value = consoleText;
                        hiddenEl.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                };
                
                updateConsole("");
                
                if (window.snippetRunner && window.snippetRunner.socket) {
                    try { window.snippetRunner.socket.close(); } catch(e) {}
                }
                
                let socket;
                try {
                    socket = new WebSocket("ws://127.0.0.1:7890");
                    window.snippetRunner = { socket: socket };
                } catch(err) {
                    updateConsole("Error creating WebSocket: " + err.message + "\\n");
                    if (runBtn) runBtn.style.removeProperty('display');
                    if (stopBtn) stopBtn.style.setProperty('display', 'none', 'important');
                    return;
                }
                
                socket.onopen = () => {
                    updateConsole("Connected. Performing handshake...\\n");
                    socket.send(JSON.stringify({ type: "handshake", version: 2 }));
                };
                
                socket.onmessage = (event) => {
                    try {
                        const msg = JSON.parse(event.data);
                        if (msg.type === "handshake") {
                            updateConsole("Handshake completed. Executing snippet...\\n\\n");
                            socket.send(JSON.stringify({
                                type: "run",
                                id: "gradio-snippet",
                                code: code,
                                kind: "cli",
                                timeout: 300
                            }));
                        } else if (msg.type === "stdout" || msg.type === "stderr") {
                            updateConsole(msg.data);
                        } else if (msg.type === "exit") {
                            updateConsole("\\n--- Process exited with code " + msg.code + " (" + msg.elapsed_ms + "ms) ---\\n");
                            socket.close();
                        } else if (msg.type === "error") {
                            updateConsole("\\nError: " + msg.message + "\\n");
                            socket.close();
                        }
                    } catch(e) {
                        updateConsole("\\nError parsing message: " + e.message + "\\n");
                    }
                };
                
                socket.onerror = (err) => {
                    updateConsole("\\nWebSocket error occurred.\\n");
                };
                
                socket.onclose = () => {
                    updateConsole("\\nBridge connection closed.\\n");
                    if (runBtn) runBtn.style.removeProperty('display');
                    if (stopBtn) stopBtn.style.setProperty('display', 'none', 'important');
                };
            }
        }"""
    )

    stop_snippet_btn.click(
        fn=handle_stop_click,
        inputs=[run_loc, session_id_state],
        outputs=[console_output, run_snippet_btn, stop_snippet_btn, hidden_console],
        js="""(runLoc) => {
            if (runLoc === "Local Bridge (ws://localhost:7890)") {
                if (window.snippetRunner && window.snippetRunner.socket) {
                    window.snippetRunner.socket.send(JSON.stringify({ type: "cancel", id: "gradio-snippet" }));
                    window.snippetRunner.socket.close();
                }
            }
        }"""
    )

    # Reset console when switching tabs or drafts
    active_tab.change(
        lambda: ("<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>", gr.update(visible=True), gr.update(visible=False)),
        outputs=[console_output, run_snippet_btn, stop_snippet_btn]
    )
    history.change(
        lambda: ("<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>", gr.update(visible=True), gr.update(visible=False)),
        outputs=[console_output, run_snippet_btn, stop_snippet_btn]
    )
    new_button.click(
        lambda: ("<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>", gr.update(visible=True), gr.update(visible=False)),
        outputs=[console_output, run_snippet_btn, stop_snippet_btn]
    )
    delete_button.click(
        lambda: ("<pre style='margin: 0; font-family: inherit; color: #888;'>Output from running the code snippet will appear here...</pre>", gr.update(visible=True), gr.update(visible=False)),
        outputs=[console_output, run_snippet_btn, stop_snippet_btn]
    )


if __name__ == "__main__":
    demo.launch()


