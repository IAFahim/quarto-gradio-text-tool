from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gradio as gr


SEGMENT_LABELS = [f"Segment {index}" for index in range(1, 6)]
LOCAL_DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR = Path("/data") if Path("/data").is_dir() and os.access("/data", os.W_OK) else LOCAL_DATA_DIR
HISTORY_PATH = DATA_DIR / "history.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def blank_segments() -> list[str]:
    return [""] * len(SEGMENT_LABELS)


def draft_label(draft: dict[str, Any]) -> str:
    title = draft.get("title") or "Untitled"
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
        segments = draft.get("segments")
        if not isinstance(segments, list):
            segments = blank_segments()
        segments = [str(value or "") for value in segments[: len(SEGMENT_LABELS)]]
        segments.extend([""] * (len(SEGMENT_LABELS) - len(segments)))
        normalized.append(
            {
                "id": str(draft.get("id") or uuid.uuid4()),
                "title": str(draft.get("title") or "Untitled"),
                "created_at": str(draft.get("created_at") or now_iso()),
                "updated_at": str(draft.get("updated_at") or now_iso()),
                "segments": segments,
            }
        )
    return normalized


def save_history(drafts: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"drafts": drafts}

    with tempfile.NamedTemporaryFile("w", delete=False, dir=DATA_DIR, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name

    os.replace(temp_name, HISTORY_PATH)


def history_choices(drafts: list[dict[str, Any]]) -> list[str]:
    return [draft_label(draft) for draft in sorted(drafts, key=lambda item: item["updated_at"], reverse=True)]


def combine_segments(*segments: str) -> str:
    parts = []
    for label, text in zip(SEGMENT_LABELS, segments):
        clean = (text or "").strip()
        if clean:
            parts.append(f"## {label}\n{clean}")
    return "\n\n".join(parts)


def title_from_segments(segments: list[str]) -> str:
    for text in segments:
        clean = " ".join((text or "").strip().split())
        if clean:
            return clean[:60]
    return "Untitled"


def find_draft_by_label(drafts: list[dict[str, Any]], label: str | None) -> dict[str, Any] | None:
    for draft in drafts:
        if draft_label(draft) == label:
            return draft
    return None


def load_app() -> tuple[Any, ...]:
    drafts = load_history()
    choices = history_choices(drafts)
    selected = choices[0] if choices else None
    draft = find_draft_by_label(drafts, selected) if selected else None
    segments = draft["segments"] if draft else blank_segments()
    return (
        drafts,
        gr.update(choices=choices, value=selected),
        *(segments),
        combine_segments(*segments),
        f"Loaded {len(drafts)} saved draft(s).",
    )


def refresh_combined(*segments: str) -> str:
    return combine_segments(*segments)


def save_current(drafts: list[dict[str, Any]], selected_label: str | None, *segments: str) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    current_segments = [text or "" for text in segments]
    timestamp = now_iso()
    existing = find_draft_by_label(drafts, selected_label)

    if existing:
        existing["segments"] = current_segments
        existing["title"] = title_from_segments(current_segments)
        existing["updated_at"] = timestamp
        saved = existing
    else:
        saved = {
            "id": str(uuid.uuid4()),
            "title": title_from_segments(current_segments),
            "created_at": timestamp,
            "updated_at": timestamp,
            "segments": current_segments,
        }
        drafts.append(saved)

    save_history(drafts)
    choices = history_choices(drafts)
    selected = draft_label(saved)
    return drafts, gr.update(choices=choices, value=selected), combine_segments(*current_segments), "Saved."


def new_draft() -> tuple[Any, ...]:
    segments = blank_segments()
    return gr.update(value=None), *segments, combine_segments(*segments), "New draft."


def fork_current(drafts: list[dict[str, Any]], selected_label: str | None, *segments: str) -> tuple[Any, ...]:
    drafts = list(drafts or [])
    current_segments = [text or "" for text in segments]
    timestamp = now_iso()
    source = find_draft_by_label(drafts, selected_label)
    source_title = source["title"] if source else title_from_segments(current_segments)
    forked = {
        "id": str(uuid.uuid4()),
        "title": f"{source_title} copy",
        "created_at": timestamp,
        "updated_at": timestamp,
        "segments": current_segments,
    }
    drafts.append(forked)
    save_history(drafts)
    choices = history_choices(drafts)
    return drafts, gr.update(choices=choices, value=draft_label(forked)), *current_segments, combine_segments(*current_segments), "Forked."


def load_selected(drafts: list[dict[str, Any]], selected_label: str | None) -> tuple[Any, ...]:
    draft = find_draft_by_label(drafts or [], selected_label)
    segments = draft["segments"] if draft else blank_segments()
    return *segments, combine_segments(*segments), "Loaded selection." if draft else "No draft selected."


with gr.Blocks(title="Text Tool", fill_height=True) as demo:
    drafts_state = gr.State([])

    gr.Markdown("# Text Tool")

    with gr.Row(equal_height=False):
        with gr.Column(scale=1, min_width=260):
            history = gr.Radio(label="History", choices=[], interactive=True)
            new_button = gr.Button("New", variant="secondary")
            fork_button = gr.Button("Fork", variant="secondary")
            save_button = gr.Button("Save", variant="primary")
            status = gr.Markdown()

        with gr.Column(scale=3):
            segment_boxes = [
                gr.Textbox(label=label, lines=5, max_lines=12)
                for label in SEGMENT_LABELS
            ]
            combined = gr.Textbox(
                label="Combined text",
                lines=12,
                max_lines=24,
                interactive=False,
                buttons=["copy"],
            )

    demo.load(
        load_app,
        outputs=[drafts_state, history, *segment_boxes, combined, status],
    )

    gr.on(
        [box.change for box in segment_boxes],
        refresh_combined,
        inputs=segment_boxes,
        outputs=combined,
    )

    save_button.click(
        save_current,
        inputs=[drafts_state, history, *segment_boxes],
        outputs=[drafts_state, history, combined, status],
    )

    new_button.click(
        new_draft,
        outputs=[history, *segment_boxes, combined, status],
    )

    fork_button.click(
        fork_current,
        inputs=[drafts_state, history, *segment_boxes],
        outputs=[drafts_state, history, *segment_boxes, combined, status],
    )

    history.change(
        load_selected,
        inputs=[drafts_state, history],
        outputs=[*segment_boxes, combined, status],
    )


if __name__ == "__main__":
    demo.launch()
