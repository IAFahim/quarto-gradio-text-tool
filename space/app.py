from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gradio as gr


DEFAULT_USER_NAME = os.getenv("TEXT_TOOL_USER_NAME", "IAFahim")
LOCAL_DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR = Path("/data") if Path("/data").is_dir() and os.access("/data", os.W_OK) else LOCAL_DATA_DIR
HISTORY_PATH = DATA_DIR / "history.json"

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


def commit_editor_text(sections: list[dict[str, str]], active_index: int, text: str | None) -> list[dict[str, str]]:
    sections = [dict(section) for section in (sections or blank_sections())]
    active_index = max(0, min(active_index or 0, len(sections) - 1))
    sections[active_index]["text"] = text or ""
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


def draft_label(draft: dict[str, Any]) -> str:
    title = draft.get("title") or "Untitled draft"
    updated = draft.get("updated_at") or ""
    return f"{title} | {updated}"


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


def history_choices(drafts: list[dict[str, Any]], query: str = "") -> list[str]:
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
    return [draft_label(draft) for draft in sorted(filtered, key=lambda item: item["updated_at"], reverse=True)]


def find_draft_by_label(drafts: list[dict[str, Any]], label: str | None) -> dict[str, Any] | None:
    for draft in drafts or []:
        if draft_label(draft) == label:
            return draft
    return None


def load_app() -> tuple[Any, ...]:
    drafts = load_history()
    choices = history_choices(drafts)
    selected = choices[0] if choices else None
    draft = find_draft_by_label(drafts, selected) if selected else None
    sections = draft["sections"] if draft else blank_sections()
    active_index = 0
    return (
        drafts,
        gr.update(choices=choices, value=selected),
        sections,
        active_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, active_index)),
        sections[active_index]["text"],
        sections[active_index]["title"],
        draft.get("title", "Untitled draft") if draft else "Untitled draft",
        combine_sections(sections),
        f"Loaded {len(drafts)} recent draft(s).",
    )


def search_history(drafts: list[dict[str, Any]], query: str) -> Any:
    choices = history_choices(drafts or [], query)
    return gr.update(choices=choices, value=choices[0] if choices else None)


def select_history(drafts: list[dict[str, Any]], selected_label: str | None) -> tuple[Any, ...]:
    draft = find_draft_by_label(drafts or [], selected_label)
    sections = draft["sections"] if draft else blank_sections()
    active_index = 0
    return (
        sections,
        active_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, active_index)),
        sections[active_index]["text"],
        sections[active_index]["title"],
        draft.get("title", "Untitled draft") if draft else "Untitled draft",
        combine_sections(sections),
        "Loaded selection." if draft else "No draft selected.",
    )


def new_draft() -> tuple[Any, ...]:
    sections = blank_sections()
    return (
        gr.update(value=None),
        sections,
        0,
        gr.update(choices=tab_choices(sections), value=active_label(sections, 0)),
        "",
        sections[0]["title"],
        "Untitled draft",
        "",
        "New draft.",
    )


def save_current(
    drafts: list[dict[str, Any]],
    selected_label: str | None,
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    draft_title: str,
    user_name: str,
) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    sections = commit_editor_text(sections, active_index, editor_text)
    timestamp = now_iso()
    existing = find_draft_by_label(drafts, selected_label)
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
    choices = history_choices(drafts)
    return (
        drafts,
        gr.update(choices=choices, value=draft_label(saved)),
        sections,
        combine_sections(sections),
        "Saved.",
    )


def fork_current(
    drafts: list[dict[str, Any]],
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    draft_title: str,
    user_name: str,
) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    sections = commit_editor_text(sections, active_index, editor_text)
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
    choices = history_choices(drafts)
    return drafts, gr.update(choices=choices, value=draft_label(forked)), forked["title"], "Forked."


def delete_current(drafts: list[dict[str, Any]], selected_label: str | None) -> tuple[Any, ...]:
    drafts = [draft for draft in (drafts or []) if draft_label(draft) != selected_label]
    save_history(drafts)
    choices = history_choices(drafts)
    selected = choices[0] if choices else None
    draft = find_draft_by_label(drafts, selected) if selected else None
    sections = draft["sections"] if draft else blank_sections()
    return (
        drafts,
        gr.update(choices=choices, value=selected),
        sections,
        0,
        gr.update(choices=tab_choices(sections), value=active_label(sections, 0)),
        sections[0]["text"],
        sections[0]["title"],
        draft.get("title", "Untitled draft") if draft else "Untitled draft",
        combine_sections(sections),
        "Deleted draft.",
    )


def select_tab(
    sections: list[dict[str, str]],
    active_index: int,
    active_tab: str | None,
    editor_text: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text)
    next_index = parse_tab_index(active_tab, sections)
    return (
        sections,
        next_index,
        sections[next_index]["text"],
        sections[next_index]["title"],
        combine_sections(sections),
    )


def update_active_text(sections: list[dict[str, str]], active_index: int, editor_text: str) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text)
    return sections, combine_sections(sections)


def rename_active_tab(
    sections: list[dict[str, str]],
    active_index: int,
    editor_text: str,
    tab_title: str,
) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text)
    active_index = max(0, min(active_index or 0, len(sections) - 1))
    sections[active_index]["title"] = (tab_title or "").strip() or f"Tab {active_index + 1}"
    return (
        sections,
        gr.update(choices=tab_choices(sections), value=active_label(sections, active_index)),
        combine_sections(sections),
    )


def add_tab(sections: list[dict[str, str]], active_index: int, editor_text: str) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text)
    sections.append(new_section(f"Tab {len(sections) + 1}"))
    next_index = len(sections) - 1
    return (
        sections,
        next_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, next_index)),
        "",
        sections[next_index]["title"],
        combine_sections(sections),
        "Added tab.",
    )


def delete_tab(sections: list[dict[str, str]], active_index: int, editor_text: str) -> tuple[Any, ...]:
    sections = commit_editor_text(sections, active_index, editor_text)
    if len(sections) > 1:
        del sections[max(0, min(active_index or 0, len(sections) - 1))]
    next_index = max(0, min(active_index or 0, len(sections) - 1))
    return (
        sections,
        next_index,
        gr.update(choices=tab_choices(sections), value=active_label(sections, next_index)),
        sections[next_index]["text"],
        sections[next_index]["title"],
        combine_sections(sections),
        "Deleted tab." if len(sections) > 1 else "Kept the last tab.",
    )


def toggle_sidebar(open_state: bool) -> tuple[Any, ...]:
    next_state = not bool(open_state)
    return next_state, gr.update(visible=next_state), "☰"


with gr.Blocks(title="Text Tool", fill_height=True, theme="gstaff/sketch") as demo:
    drafts_state = gr.State([])
    sections_state = gr.State(blank_sections())
    active_index_state = gr.State(0)
    sidebar_open_state = gr.State(True)

    with gr.Row():
        with gr.Column(scale=1, min_width=280) as sidebar:
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

        with gr.Column(scale=3):
            with gr.Row():
                toggle_button = gr.Button("☰", scale=0)
                save_button = gr.Button("Save", variant="primary", scale=0)
                copy_button = gr.Button("Copy", scale=0)

            draft_title = gr.Textbox(
                value="Untitled draft",
                label="Draft title",
                show_label=False,
            )

            active_tab = gr.Dropdown(label="Section", choices=[], interactive=True)
            editor = gr.Textbox(label="Text", lines=24, max_lines=80)

            with gr.Row():
                tab_title = gr.Textbox(
                    label="Tab name",
                    placeholder="Tab name",
                    scale=4,
                )
                add_tab_button = gr.Button("+ Tab", scale=0)
                rename_tab_button = gr.Button("Rename", scale=0)
                delete_tab_button = gr.Button("Delete section", scale=0)

            with gr.Accordion("Combined text", open=False):
                combined = gr.Textbox(
                    label="Combined text",
                    lines=10,
                    max_lines=24,
                    interactive=False,
                )
            status = gr.Markdown()

    demo.load(
        load_app,
        outputs=[
            drafts_state,
            history,
            sections_state,
            active_index_state,
            active_tab,
            editor,
            tab_title,
            draft_title,
            combined,
            status,
        ],
    )

    search.change(search_history, inputs=[drafts_state, search], outputs=history)

    toggle_button.click(
        toggle_sidebar,
        inputs=sidebar_open_state,
        outputs=[sidebar_open_state, sidebar, toggle_button],
    )

    history.change(
        select_history,
        inputs=[drafts_state, history],
        outputs=[
            sections_state,
            active_index_state,
            active_tab,
            editor,
            tab_title,
            draft_title,
            combined,
            status,
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
            tab_title,
            draft_title,
            combined,
            status,
        ],
    )

    save_button.click(
        save_current,
        inputs=[drafts_state, history, sections_state, active_index_state, editor, draft_title, user_name],
        outputs=[drafts_state, history, sections_state, combined, status],
    )

    fork_button.click(
        fork_current,
        inputs=[drafts_state, sections_state, active_index_state, editor, draft_title, user_name],
        outputs=[drafts_state, history, draft_title, status],
    )

    delete_button.click(
        delete_current,
        inputs=[drafts_state, history],
        outputs=[
            drafts_state,
            history,
            sections_state,
            active_index_state,
            active_tab,
            editor,
            tab_title,
            draft_title,
            combined,
            status,
        ],
    )

    active_tab.change(
        select_tab,
        inputs=[sections_state, active_index_state, active_tab, editor],
        outputs=[sections_state, active_index_state, editor, tab_title, combined],
    )

    editor.change(
        update_active_text,
        inputs=[sections_state, active_index_state, editor],
        outputs=[sections_state, combined],
    )

    rename_tab_button.click(
        rename_active_tab,
        inputs=[sections_state, active_index_state, editor, tab_title],
        outputs=[sections_state, active_tab, combined],
    )

    add_tab_button.click(
        add_tab,
        inputs=[sections_state, active_index_state, editor],
        outputs=[sections_state, active_index_state, active_tab, editor, tab_title, combined, status],
    )

    delete_tab_button.click(
        delete_tab,
        inputs=[sections_state, active_index_state, editor],
        outputs=[sections_state, active_index_state, active_tab, editor, tab_title, combined, status],
    )

    copy_button.click(
        lambda text: "Copied to clipboard.",
        inputs=combined,
        outputs=status,
        js="(text) => { navigator.clipboard.writeText(text || ''); return 'Copied to clipboard.'; }",
    )


if __name__ == "__main__":
    demo.launch()
