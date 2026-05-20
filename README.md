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

For visual exploration with a vision-capable OpenAI-compatible endpoint:

```bash
ui-explorer \
  --app-url http://localhost:3000 \
  --visual-exploration \
  --vision-endpoint http://localhost:1234/v1/chat/completions \
  --vision-model qwen2.5-vl-7b-instruct
```

If `--vision-endpoint` is omitted, visual exploration uses `--llm-endpoint`.
If `--vision-model` is omitted, it uses `--llm-model`.

The browser always runs headed. If the first page looks like a login screen or
redirects away from the app URL, the explorer pauses so you can log in. It
continues after the browser returns to the original app origin/path.

Artifacts are written under `artifacts/run-YYYYMMDD-HHMMSS/`.

Default timing limits:

- `--max-seconds 1200`: total run budget
- `--page-settle-timeout-seconds 600`: wait for slow app requests after navigation
- `--vision-timeout-seconds 300`: wait for each vision model request
- `--llm-timeout-seconds 120`: wait for the final text review request

## Safety Defaults

The crawler avoids controls with labels such as delete, remove, archive, submit,
save, send, pay, purchase, logout, and sign out. It does not fill forms. It
limits route count, click count, and runtime.

This is intentionally dumb. Its job is to map the app and flag obvious problems,
not to understand the product yet.
