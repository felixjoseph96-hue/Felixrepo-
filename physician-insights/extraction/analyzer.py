"""Extract structured pain points from a single interview transcript using Claude."""
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from config import Config

_client: Optional[anthropic.Anthropic] = None


class AnalyzerError(Exception):
    pass


class ExtractedPainPoint(BaseModel):
    theme: str = Field(description="Closest matching theme from the provided vocabulary")
    custom_label: Optional[str] = Field(
        default=None,
        description="Short label for the pain point when theme == 'Other'",
    )
    description: str = Field(description="1-2 sentence description of the pain point")
    quote: str = Field(description="Verbatim supporting quote lifted from the transcript")
    severity: int = Field(ge=1, le=5, description="How burdensome this is for the physician, 1-5")
    frequency_signal: Literal["low", "medium", "high"] = Field(
        description="How prominently/repeatedly the physician emphasized this in the interview"
    )


class InterviewAnalysis(BaseModel):
    summary: str = Field(description="2-4 sentence neutral summary of the interview")
    specialty_guess: Optional[str] = Field(
        default=None, description="Physician's specialty if inferable from context, else null"
    )
    pain_points: List[ExtractedPainPoint]
    top_insight: str = Field(
        description="Single sentence: the most important, actionable takeaway from this interview"
    )


def _get_client() -> anthropic.Anthropic:
    global _client
    if not Config.ANTHROPIC_API_KEY:
        raise AnalyzerError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file to enable "
            "pain-point extraction."
        )
    if _client is None:
        _client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = f"""You are a qualitative researcher analyzing a transcript of an \
interview with a physician about the challenges they face in their day-to-day practice.

Read the entire transcript and extract every distinct pain point the physician \
raises. A pain point is a specific frustration, obstacle, or burden they describe \
experiencing - not a neutral fact or a positive observation.

For each pain point:
- Choose the single closest matching theme from this fixed list: \
{", ".join(Config.PAIN_POINT_THEMES)}. Only use "Other" when nothing else fits, and \
in that case set custom_label to a short (2-5 word) label for it.
- Write a concise 1-2 sentence description in your own words.
- Pull a real, verbatim quote from the transcript that best supports it. Never \
invent or paraphrase the quote - copy the exact words.
- Rate severity 1-5: how burdensome this seems for the physician (5 = severe, \
actively driving burnout or considering leaving practice; 1 = minor annoyance).
- Rate frequency_signal (low/medium/high): how prominently or repeatedly they \
emphasized this point in the conversation.

Do not fabricate pain points that are not actually supported by the transcript. If \
the transcript contains no clear pain points, return an empty pain_points list.

Also provide:
- summary: a neutral 2-4 sentence summary of the interview as a whole.
- specialty_guess: the physician's medical specialty if it can be inferred, else null.
- top_insight: one sentence capturing the single most important, actionable \
takeaway from this interview.
"""


def analyze_transcript(transcript: str) -> InterviewAnalysis:
    """Send a transcript to Claude and return structured pain-point analysis."""
    if not transcript or not transcript.strip():
        raise AnalyzerError("Transcript is empty - nothing to analyze.")

    client = _get_client()

    try:
        response = client.messages.parse(
            model=Config.ANTHROPIC_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Transcript:\n\n{transcript}"}],
            output_format=InterviewAnalysis,
        )
    except anthropic.APIStatusError as e:
        raise AnalyzerError(f"Claude API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AnalyzerError(f"Could not reach the Claude API: {e}") from e

    if response.parsed_output is None:
        raise AnalyzerError("Claude did not return a parseable analysis for this transcript.")

    return response.parsed_output
