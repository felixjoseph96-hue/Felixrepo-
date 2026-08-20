"""
FastAPI app for discovering and summarizing physician pain points from
interview transcripts and recordings.

Routes
------
GET  /                          Upload page (single or bulk)
POST /upload                    Parse/transcribe + analyze one or more files
GET  /interviews                List of processed/pending interviews
GET  /interviews/{id}           Full pain-point breakdown for one interview
POST /interviews/{id}/reprocess Re-run Claude analysis on an existing transcript
POST /interviews/{id}/delete    Remove an interview and its pain points
GET  /insights                  Cross-interview theme dashboard
POST /insights/generate         Generate a fresh executive insights report
GET  /api/insights.json         JSON API for the aggregated theme rollup
"""
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import markdown as markdown_lib
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import Config
from extraction.aggregator import ReportError, generate_insights_report, rollup_by_theme
from extraction.analyzer import AnalyzerError, analyze_transcript
from extraction.parser import ParseError, parse_text_file
from extraction.transcription import TranscriptionError, transcribe_audio
from models import Interview, InsightsReport, PainPoint, get_session, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(Config.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="Physician Pain Point Insights",
    description="Discover and summarize real pain points from physician interviews",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _save_upload(upload: UploadFile) -> Path:
    safe_name = f"{int(time.time() * 1000)}_{Path(upload.filename).name}"
    dest = Path(Config.UPLOAD_DIR) / safe_name
    with open(dest, "wb") as f:
        f.write(upload.file.read())
    return dest


def _process_one_file(session, upload: UploadFile, specialty: str, physician_name: str) -> Interview:
    ext = Path(upload.filename).suffix.lower()
    dest = _save_upload(upload)

    if ext in Config.ALLOWED_AUDIO_EXTENSIONS:
        source_type = "audio"
    elif ext in Config.ALLOWED_TEXT_EXTENSIONS:
        source_type = "text"
    else:
        raise ParseError(
            f"Unsupported file type '{ext}'. Allowed: "
            f"{sorted(Config.ALLOWED_TEXT_EXTENSIONS | Config.ALLOWED_AUDIO_EXTENSIONS)}"
        )

    interview = Interview(
        filename=upload.filename,
        source_type=source_type,
        specialty=specialty or None,
        physician_name=physician_name or None,
        status="pending",
    )
    session.add(interview)
    session.flush()  # assign an id

    try:
        if source_type == "audio":
            interview.raw_text = transcribe_audio(dest)
        else:
            interview.raw_text = parse_text_file(dest)

        interview.status = "processing"
        session.commit()

        analysis = analyze_transcript(interview.raw_text)

        interview.summary = analysis.summary
        interview.top_insight = analysis.top_insight
        if analysis.specialty_guess and not interview.specialty:
            interview.specialty = analysis.specialty_guess
        for pp in analysis.pain_points:
            session.add(
                PainPoint(
                    interview_id=interview.id,
                    theme=pp.theme if pp.theme in Config.PAIN_POINT_THEMES else "Other",
                    custom_label=pp.custom_label
                    if pp.theme in Config.PAIN_POINT_THEMES
                    else pp.theme,
                    description=pp.description,
                    quote=pp.quote,
                    severity=pp.severity,
                    frequency_signal=pp.frequency_signal,
                )
            )
        interview.status = "done"
        interview.processed_at = datetime.utcnow()

    except (ParseError, TranscriptionError, AnalyzerError) as e:
        interview.status = "error"
        interview.error_message = str(e)
        logger.warning("Failed to process %s: %s", upload.filename, e)

    session.commit()
    return interview


@app.get("/", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", {})


@app.post("/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    specialty: str = Form(""),
    physician_name: str = Form(""),
):
    session = get_session()
    try:
        results = []
        for upload_file in files:
            if not upload_file.filename:
                continue
            interview = _process_one_file(session, upload_file, specialty, physician_name)
            results.append(interview.id)
    finally:
        session.close()

    if not results:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(results) == 1:
        return RedirectResponse(f"/interviews/{results[0]}", status_code=303)
    return RedirectResponse("/interviews", status_code=303)


@app.get("/interviews", response_class=HTMLResponse)
def list_interviews(request: Request):
    session = get_session()
    try:
        interviews = session.query(Interview).order_by(Interview.created_at.desc()).all()
        return templates.TemplateResponse(request, "interviews.html", {"interviews": interviews})
    finally:
        session.close()


@app.get("/interviews/{interview_id}", response_class=HTMLResponse)
def interview_detail(request: Request, interview_id: int):
    session = get_session()
    try:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="Interview not found")
        pain_points = sorted(interview.pain_points, key=lambda p: p.severity, reverse=True)
        return templates.TemplateResponse(
            request, "interview_detail.html", {"interview": interview, "pain_points": pain_points}
        )
    finally:
        session.close()


@app.post("/interviews/{interview_id}/reprocess")
def reprocess_interview(interview_id: int):
    session = get_session()
    try:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="Interview not found")
        if not interview.raw_text:
            raise HTTPException(status_code=400, detail="No transcript text stored to reprocess.")

        for pp in list(interview.pain_points):
            session.delete(pp)
        interview.summary = None
        interview.top_insight = None
        interview.error_message = None
        interview.status = "processing"
        session.commit()

        try:
            analysis = analyze_transcript(interview.raw_text)
            interview.summary = analysis.summary
            interview.top_insight = analysis.top_insight
            for pp in analysis.pain_points:
                session.add(
                    PainPoint(
                        interview_id=interview.id,
                        theme=pp.theme if pp.theme in Config.PAIN_POINT_THEMES else "Other",
                        custom_label=pp.custom_label
                        if pp.theme in Config.PAIN_POINT_THEMES
                        else pp.theme,
                        description=pp.description,
                        quote=pp.quote,
                        severity=pp.severity,
                        frequency_signal=pp.frequency_signal,
                    )
                )
            interview.status = "done"
            interview.processed_at = datetime.utcnow()
        except AnalyzerError as e:
            interview.status = "error"
            interview.error_message = str(e)

        session.commit()
        return RedirectResponse(f"/interviews/{interview_id}", status_code=303)
    finally:
        session.close()


@app.post("/interviews/{interview_id}/delete")
def delete_interview(interview_id: int):
    session = get_session()
    try:
        interview = session.get(Interview, interview_id)
        if interview is None:
            raise HTTPException(status_code=404, detail="Interview not found")
        session.delete(interview)
        session.commit()
        return RedirectResponse("/interviews", status_code=303)
    finally:
        session.close()


@app.get("/insights", response_class=HTMLResponse)
def insights_dashboard(request: Request):
    session = get_session()
    try:
        rollups = rollup_by_theme(session)
        latest_report = (
            session.query(InsightsReport).order_by(InsightsReport.generated_at.desc()).first()
        )
        done_count = session.query(Interview).filter(Interview.status == "done").count()
        report_html = (
            markdown_lib.markdown(latest_report.content_markdown) if latest_report else None
        )
        return templates.TemplateResponse(
            request,
            "insights.html",
            {
                "rollups": rollups,
                "report": latest_report,
                "report_html": report_html,
                "done_count": done_count,
            },
        )
    finally:
        session.close()


@app.post("/insights/generate")
def generate_report():
    session = get_session()
    try:
        try:
            content = generate_insights_report(session)
        except ReportError as e:
            raise HTTPException(status_code=400, detail=str(e))

        done_count = session.query(Interview).filter(Interview.status == "done").count()
        report = InsightsReport(interview_count=done_count, content_markdown=content)
        session.add(report)
        session.commit()
        return RedirectResponse("/insights", status_code=303)
    finally:
        session.close()


@app.get("/api/insights.json")
def api_insights():
    session = get_session()
    try:
        rollups = rollup_by_theme(session)
        return JSONResponse(
            [
                {
                    "theme": r.theme,
                    "mention_count": r.mention_count,
                    "interview_count": r.interview_count,
                    "avg_severity": round(r.avg_severity, 2),
                    "impact_score": round(r.impact_score, 2),
                    "top_quotes": r.top_quotes,
                }
                for r in rollups
            ]
        )
    finally:
        session.close()
