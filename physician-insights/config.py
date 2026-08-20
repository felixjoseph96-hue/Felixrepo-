"""Central configuration loaded from environment / .env file."""
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///physician_insights.db")

    # Claude (pain-point extraction + insight synthesis)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

    # Audio transcription
    # If set, the OpenAI Whisper API is used (fast, no local model download).
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    # Fallback when OPENAI_API_KEY is not set: a local faster-whisper model.
    LOCAL_WHISPER_MODEL: str = os.getenv("LOCAL_WHISPER_MODEL", "base")

    # Uploads
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads")
    MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "200"))

    # Controlled vocabulary of physician pain-point themes.
    # Keeping this fixed (rather than fully freeform) is what makes
    # cross-interview aggregation on the /insights dashboard meaningful.
    PAIN_POINT_THEMES: list[str] = [
        "EHR & Documentation Burden",
        "Prior Authorization & Insurance Hassles",
        "Administrative & Paperwork Burden",
        "Reimbursement & Billing",
        "Staffing Shortages",
        "Work-Life Balance & Burnout",
        "Patient Volume & Time Pressure",
        "Patient Communication & Expectations",
        "Technology & Interoperability",
        "Regulatory & Compliance",
        "Compensation & Financial Pressure",
        "Career Development & Support",
        "Malpractice & Liability Concerns",
        "Other",
    ]

    ALLOWED_TEXT_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
    ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".mp4", ".webm", ".ogg"}
