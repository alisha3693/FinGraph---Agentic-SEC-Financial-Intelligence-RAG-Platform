"""Turning answer text into the structured form the frontend renders (summary, verdict, sections)."""

import json
import logging
import re
from typing import Dict, Any, List, Optional

from .config import _llm
from .state import AgentState
from .facts import _unsupported_figures

logger = logging.getLogger(__name__)


# Structured answers for the frontend. The model still writes plain text (that stays in
# `answer`, so anything reading it keeps working); these helpers split that text into a
# summary line, a verdict, and titled sections, and add figure tiles computed in code.
SUMMARY_NOTE = (
    "\n\nStart with one line, not a bullet, that begins with \"Summary:\" and answers the question "
    "in one sentence. Then give the bullets."
)


THEMES_NOTE = (
    " Group the bullets under 2 to 4 short theme headings, each on its own line as \"### <theme>\"."
)


_HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s+(.+?)|\*\*(.+?)\*\*:?)\s*$")


def _parse_answer(text: str) -> Dict[str, Any]:
    """Split a model answer into {summary, verdict, sections: [{title, points}]}. Lines it
    doesn't recognise become points, so nothing in the answer is dropped."""
    summary, verdict = None, None
    sections: List[Dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        bare = re.sub(r"^[-•*]\s+", "", line)
        if re.match(r"^\[\d+\]\s", bare):
            # A reference-list line ("[1] SQLite SEC EDGAR Facts DB"); sources are listed separately.
            continue
        lowered = bare.lower()
        if lowered.startswith("summary:") and summary is None:
            summary = bare.split(":", 1)[1].strip()
            continue
        if lowered.startswith("verdict:"):
            verdict = bare.split(":", 1)[1].strip()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            sections.append({"title": (heading.group(1) or heading.group(2)).strip(), "points": []})
            continue
        if not sections:
            sections.append({"title": None, "points": []})
        sections[-1]["points"].append(bare)
    return {"summary": summary, "verdict": verdict, "sections": [s for s in sections if s["points"]]}


def _structured_part(key: str, answer: str, **extra: Any) -> Dict[str, Any]:
    parsed = _parse_answer(answer)
    return {
        "kind": "quantitative" if key == "figures" else "qualitative",
        "summary": parsed["summary"],
        "verdict": parsed["verdict"],
        "connections": [],
        "parts": [{"key": key, **parsed, **extra}],
    }


def _verdict_note(plan: Optional[Dict[str, Any]], basis: str) -> str:
    """Prompt line asking for a closing verdict when the question asks "which one"."""
    if not plan or not plan.get("wants_verdict"):
        return ""
    return (
        "\n\nThe question asks for a single answer (which company or year is highest, fastest, "
        "riskiest, and so on). End with one bullet that starts with \"- Verdict:\" and answers it "
        f"directly, based only on {basis}. If {basis} cannot settle it, say so in that bullet."
    )


MIXED_SUMMARY_PROMPT = """A question was answered in two parts: one from reported financial figures, one from SEC filing text.

Question: {question}

Figures part:
{figures}

Filings part:
{filings}

Respond with ONLY a JSON object, no other text, with these keys:
- "summary": one sentence answering the whole question, using both parts.
- "verdict": {verdict_rule}
- "connections": up to 3 short sentences linking a point in the figures part to a point in the filings part (for example a change in a figure and a risk or explanation the filing gives for it). Use only facts and numbers that appear in the two parts, keep their bracketed citation numbers, and return an empty list if nothing genuinely connects."""


def _mixed_structure(state: AgentState, financial_result: Dict[str, Any], qualitative_answer: str) -> Dict[str, Any]:
    """Structured answer for a question that needs both halves. The two halves ran in
    parallel without seeing each other, so one extra LLM call writes the overall summary,
    verdict, and links between them. Every figure in its output must already appear in one
    of the halves; a sentence with an unknown figure is dropped. If the call fails, each
    half's own summary and verdict are used and there are no links."""
    figures_struct = financial_result.get("structured") or _structured_part("figures", financial_result["answer"])
    figures_part = figures_struct["parts"][0]
    filings = _parse_answer(qualitative_answer)
    filings_part = {"key": "filings", **filings}

    summary = None
    verdict = figures_part.get("verdict") or filings.get("verdict")
    connections: List[str] = []
    plan = state.get("plan") or {}
    verdict_rule = (
        "the single answer to the question's \"which one\" part, in one sentence, based only on the two parts."
        if plan.get("wants_verdict") else "null."
    )
    grounding = financial_result["answer"] + "\n" + qualitative_answer
    try:
        raw = _llm().invoke(MIXED_SUMMARY_PROMPT.format(
            question=state["query"], figures=financial_result["answer"], filings=qualitative_answer,
            verdict_rule=verdict_rule,
        )).content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0) if match else raw)
        if isinstance(parsed.get("summary"), str) and not _unsupported_figures(parsed["summary"], grounding):
            summary = parsed["summary"].strip()
        if plan.get("wants_verdict") and isinstance(parsed.get("verdict"), str) \
                and not _unsupported_figures(parsed["verdict"], grounding):
            verdict = parsed["verdict"].strip()
        connections = [
            c.strip() for c in parsed.get("connections") or []
            if isinstance(c, str) and c.strip() and not _unsupported_figures(c, grounding)
        ][:3]
    except Exception:
        logger.warning("Mixed-answer summary failed; showing each part's own summary", exc_info=True)

    return {
        "kind": "mixed",
        "summary": summary,
        "verdict": verdict,
        "connections": connections,
        "parts": [figures_part, filings_part],
    }
