# UI Explorer

A small, headed-only Playwright crawler for taking a first pass through an
unknown web app or SPA.

It visits internal routes, clicks safe-looking navigation controls, records
obvious failures, captures screenshots, and writes JSON/Markdown artifacts. It
can optionally send the run summary to an OpenAI-compatible chat completions
endpoint for follow-up notes.

## Install

```bash
cd ui-explorer
python3 -m venv .venv
source .venv/bin/activate
pip install .
playwright install chromium
```

## Run

```bash
ui-explorer \
  --app-url http://localhost:3000 \
  --llm-endpoint http://localhost:11434/v1/chat/completions \
  --llm-model llama3.1:8b
```

The browser always runs headed. If the first page looks like a login screen or
redirects away from the app URL, the explorer pauses so you can log in. It
continues after the browser returns to the original app origin/path.

Artifacts are written under `artifacts/run-YYYYMMDD-HHMMSS/`.

## Safety Defaults

The crawler avoids controls with labels such as delete, remove, archive, submit,
save, send, pay, purchase, logout, and sign out. It does not fill forms. It
limits route count, click count, and runtime.

This is intentionally dumb. Its job is to map the app and flag obvious problems,
not to understand the product yet.
