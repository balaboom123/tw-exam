"""File-role meanings shared by CEEC's administered examination archives."""

from __future__ import annotations

import re


def listing_file_type(label: str, *, alternative_question: bool = False) -> str:
    """Keep covers and scoring instructions separate from answer corrections."""
    if label == "試題內容":
        return "question_alt" if alternative_question else "question"
    if label == "封面":
        return "question_cover"
    if label == "答題卷":
        return "answer_sheet"
    if re.search(r"評分(?:原則|標準|說明)", label):
        return "scoring_guidelines"
    if "答案" in label:
        return "corrected_answer" if "更正" in label or "修正" in label else "answer"
    raise RuntimeError(f"Unrecognized CEEC download role: {label!r}")
