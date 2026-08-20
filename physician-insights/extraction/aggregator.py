"""Cross-interview theme aggregation and executive insight-report generation."""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List

import anthropic
from sqlalchemy.orm import Session

from config import Config
from models import Interview, PainPoint


@dataclass
class ThemeRollup:
    theme: str
    mention_count: int = 0
    interview_count: int = 0
    avg_severity: float = 0.0
    top_quotes: List[str] = field(default_factory=list)

    @property
    def impact_score(self) -> float:
        """Ranking score: how often it comes up, weighted by how severe it is."""
        return self.mention_count * self.avg_severity


def rollup_by_theme(session: Session) -> List[ThemeRollup]:
    """Group all pain points (across every processed interview) by theme."""
    points = (
        session.query(PainPoint)
        .join(Interview)
        .filter(Interview.status == "done")
        .all()
    )

    by_theme: dict[str, list[PainPoint]] = defaultdict(list)
    for p in points:
        by_theme[p.display_theme()].append(p)

    rollups = []
    for theme, pts in by_theme.items():
        severities = [p.severity for p in pts]
        interview_ids = {p.interview_id for p in pts}
        sorted_quotes = sorted(pts, key=lambda p: p.severity, reverse=True)
        rollups.append(
            ThemeRollup(
                theme=theme,
                mention_count=len(pts),
                interview_count=len(interview_ids),
                avg_severity=sum(severities) / len(severities) if severities else 0,
                top_quotes=[p.quote for p in sorted_quotes[:3] if p.quote],
            )
        )

    rollups.sort(key=lambda r: r.impact_score, reverse=True)
    return rollups


class ReportError(Exception):
    pass


def generate_insights_report(session: Session) -> str:
    """Ask Claude to synthesize an executive brief across all processed interviews."""
    if not Config.ANTHROPIC_API_KEY:
        raise ReportError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file to generate "
            "an insights report."
        )

    rollups = rollup_by_theme(session)
    if not rollups:
        raise ReportError("No processed interviews with pain points yet.")

    interview_count = session.query(Interview).filter(Interview.status == "done").count()

    lines = [f"Data from {interview_count} processed physician interview(s):\n"]
    for r in rollups:
        lines.append(
            f"- Theme: {r.theme} | mentioned {r.mention_count}x across "
            f"{r.interview_count} interview(s) | avg severity {r.avg_severity:.1f}/5"
        )
        for q in r.top_quotes:
            lines.append(f'    quote: "{q}"')
    data_block = "\n".join(lines)

    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    try:
        response = client.messages.create(
            model=Config.ANTHROPIC_MODEL,
            max_tokens=4096,
            system=(
                "You are a healthcare research analyst. You are given aggregated "
                "pain-point data extracted from multiple physician interviews. Write "
                "a concise executive brief in Markdown with:\n"
                "1. A 'Top Pain Points' section ranking the 3-6 most significant "
                "themes, each with a one-sentence synthesis and one illustrative "
                "quote.\n"
                "2. A short 'Recommendations' section with 2-4 concrete, actionable "
                "suggestions a healthcare organization could take in response.\n"
                "Be specific and avoid generic filler. Base every claim only on the "
                "data provided."
            ),
            messages=[{"role": "user", "content": data_block}],
        )
    except anthropic.APIStatusError as e:
        raise ReportError(f"Claude API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ReportError(f"Could not reach the Claude API: {e}") from e

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise ReportError("Claude did not return report text.")
    return text
