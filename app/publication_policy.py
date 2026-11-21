"""Eligibility decisions shared by full and indexed publication readers."""

from __future__ import annotations


def classification_allows_publication(confidence: str) -> bool:
    """Retain unresolved classifications without offering their bundles publicly.

    Empty confidence is supported for legacy migration callers. Structured
    records use the validated high, medium, or review confidence values.
    """
    return confidence != "review"
