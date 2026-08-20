# Physician Pain Point Insights

Upload physician interview transcripts (or audio recordings), and get back
structured pain points, per-interview summaries, and a cross-interview
insights dashboard - powered by Claude.

## What it does

1. **Ingest** - upload text transcripts (`.txt`, `.md`, `.docx`, `.pdf`) or
   audio recordings (`.mp3`, `.wav`, `.m4a`, `.mp4`, `.webm`, `.ogg`), one at a
   time or in bulk.
2. **Parse/transcribe** - text files are parsed directly; audio is
   transcribed via the OpenAI Whisper API or a local `faster-whisper` model.
3. **Extract** - each transcript is sent to Claude, which pulls out every
   distinct pain point (theme, description, a verbatim supporting quote,
   severity 1-5, and how prominently it was emphasized), plus an interview
   summary and single top insight.
4. **Aggregate** - the `/insights` dashboard rolls pain points up across all
   processed interviews by theme, ranks them by impact (mention count x
   average severity), and can generate a Claude-written executive brief with
   recommendations.

## Setup

```bash
cd physician-insights
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY (required)
python run.py
```

Then open http://localhost:8000.

### Audio transcription (optional)

Only needed if you plan to upload audio files rather than text transcripts.
Pick one:

- **OpenAI Whisper API**: `pip install openai` and set `OPENAI_API_KEY` in
  `.env`. Simplest option, costs per minute of audio.
- **Local, offline**: `pip install faster-whisper`. No API key, downloads a
  model on first use, needs more local compute.

If neither is configured, uploading an audio file will fail with a clear
error message telling you how to enable one of the two paths.

## How themes stay comparable across interviews

Rather than letting Claude invent a free-form label per pain point (which
would make cross-interview aggregation useless - "EHR burden" vs "EHR
burnout" vs "documentation overload" never rolling up together), the prompt
constrains it to a fixed vocabulary defined in `config.py`
(`Config.PAIN_POINT_THEMES`): EHR & Documentation Burden, Prior
Authorization & Insurance Hassles, Administrative & Paperwork Burden,
Reimbursement & Billing, Staffing Shortages, Work-Life Balance & Burnout,
Patient Volume & Time Pressure, Patient Communication & Expectations,
Technology & Interoperability, Regulatory & Compliance, Compensation &
Financial Pressure, Career Development & Support, Malpractice & Liability
Concerns, and an `Other` catch-all with a short custom label. Edit that list
to match your own taxonomy if needed.

## Project layout

```
app.py                    FastAPI routes (upload, interviews, insights)
config.py                 Env config + controlled theme vocabulary
models.py                 SQLAlchemy models (Interview, PainPoint, InsightsReport)
extraction/
  parser.py                txt/docx/pdf -> plain text
  transcription.py          audio -> text (Whisper API or local faster-whisper)
  analyzer.py                Claude structured extraction (per interview)
  aggregator.py               cross-interview theme rollup + executive report
templates/, static/        Jinja2 templates + CSS for the web UI
```

## Notes on scale

Uploads are processed synchronously (parse/transcribe, then one Claude call
per interview) inside the request handler - simple and fine for occasional
or small-batch use. For large batches or a production deployment, move
`_process_one_file` in `app.py` to a background task queue (Celery, RQ, or
FastAPI `BackgroundTasks`) so uploads return immediately and processing
happens out-of-band.
