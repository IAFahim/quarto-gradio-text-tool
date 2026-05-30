# Quarto Gradio Text Tool

This repository contains a Quarto website that embeds a Hugging Face Space.
The Space hosts a Gradio app for editing five text segments, saving history,
forking drafts, and copying the combined text.

## Local Development

Run the Gradio app:

```bash
uv run python space/app.py
```

Render the Quarto site:

```bash
quarto render
```

## Deployment Shape

- GitHub Pages hosts the Quarto site.
- Hugging Face Spaces hosts the Gradio app.
- Hugging Face Storage Bucket volume mounted at `/data` stores `history.json`.
