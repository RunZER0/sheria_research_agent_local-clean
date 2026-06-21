# Sheria Research Agent Local

A local-first research agent starter with:

- FastAPI backend
- one-command local web UI
- live workstream / step streaming
- DeepSeek OpenAI-compatible chat API
- Brave Search API
- Playwright Firefox headless browsing
- strict source-grounded answer mode
- citation guard and repair loop
- local SQLite conversation storage
- Kenyan legal research mode with official-domain bias

This is intentionally not a generic chatbot. It is a research workflow:

```text
User query
-> research plan
-> Brave search
-> Firefox page reading
-> evidence cards
-> source-grounded answer
-> citation verification
-> optional repair
-> final answer + source list
```

## Design direction

The frontend avoids AI-slop gradients, glassmorphism, and card stacks. It is a plain research desk: columns, rules, typography, workstream, sources, transcript.

## Requirements

Python 3.11+ is recommended.

## Setup

Developer startup checklist (copy these commands and run in order):

```powershell
cd sheria_research_agent_local
# create and activate a venv (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# install dependencies and pytest
pip install -r requirements.txt pytest

# install Playwright Firefox
python -m playwright install firefox

# copy example env and edit keys
Copy-Item .env.example .env
# open .env and paste your keys for DEEPSEEK_API_KEY and BRAVE_API_KEY
notepad .env
```

Add the following entries in `.env` (replace with your real keys):

```env
DEEPSEEK_API_KEY=your_deepseek_key_here
BRAVE_API_KEY=your_brave_api_key_here
```

## Run

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Recommended model setting

The project defaults to:

```env
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_THINKING=enabled
DEEPSEEK_REASONING_EFFORT=medium
```

Use `deepseek-v4-pro` for heavier legal reasoning once the architecture is stable.

## Environment variables

See `.env.example`.

## Important legal safety rule

This starter is for legal research support, not professional legal advice. The guard layer is intentionally strict: if sources cannot support a claim, the answer should say so.

## Project layout

```text
app/
  agent.py             Research workflow orchestration
  brave_search.py      Brave Search API client
  browser_fetch.py     Firefox headless page reader
  config.py            Settings
  deepseek_client.py   DeepSeek client wrapper
  guards.py            Citation guard and repair helpers
  main.py              FastAPI server and SSE endpoint
  schemas.py           Request/response models
  store.py             Local SQLite storage
static/
  index.html           Local chat UI
  styles.css           Brutalist/editorial interface
  app.js               Streaming UI logic
```

## Next serious upgrades

1. Add Kenya Law ingestion into your own local corpus.
2. Add Pinecone or pgvector retrieval for cached legal sources.
3. Add matter workspaces instead of single chats.
4. Add daily legal radar scheduler.
5. Add source authority ranking: Supreme Court, Court of Appeal, High Court, tribunals, regulators.
6. Add citation quote verification by re-opening each source.
7. Add uploaded document analysis.
8. Add voice mode with Groq/Deepgram transcription and Cartesia TTS.
