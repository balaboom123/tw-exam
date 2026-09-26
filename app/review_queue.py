"""Lossless storage for provider review ledgers; ReviewItem owns the fields."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from typing import Any, Literal, TypedDict

from app.models import ReviewItem

REVIEW_FIELDS = tuple(field.name for field in fields(ReviewItem) if field.name != "provider_id")
YEAR_COLUMN = REVIEW_FIELDS.index("year_roc")


class CompactReviewQueue(TypedDict):
    schema_version: Literal[2]
    provider_id: str
    fields: list[str]
    strings: list[tuple[int, str]]
    items: list[list[int]]


def _prefix_length(previous: str, current: str) -> int:
    length = 0
    for before, after in zip(previous, current, strict=False):
        if before != after:
            break
        length += 1
    return length


def encode_review_queue(
    reviews: Iterable[ReviewItem], provider_id: str
) -> CompactReviewQueue | list[ReviewItem]:
    records = list(reviews)
    if not records:
        return []
    if not provider_id or any(item.provider_id not in {"", provider_id} for item in records):
        raise ValueError("Review queue contains a different provider owner")
    values: set[str] = set()
    for item in records:
        for field in REVIEW_FIELDS:
            if field == "year_roc":
                continue
            value: object = getattr(item, field)
            if not isinstance(value, str):
                raise ValueError("Review queue text fields must be strings")
            values.add(value)
    strings = sorted(values)
    lookup = {value: index for index, value in enumerate(strings)}
    table: list[tuple[int, str]] = []
    previous = ""
    for value in strings:
        prefix = _prefix_length(previous, value)
        table.append((prefix, value[prefix:]))
        previous = value
    rows: list[list[int]] = []
    for item in records:
        if type(item.year_roc) is not int:
            raise ValueError("Review queue year must be an integer")
        rows.append(
            [
                item.year_roc if field == "year_roc" else lookup[getattr(item, field)]
                for field in REVIEW_FIELDS
            ]
        )
    return {
        "schema_version": 2,
        "provider_id": provider_id,
        "fields": list(REVIEW_FIELDS),
        "strings": table,
        "items": rows,
    }


def _legacy_review(entry: object, provider_id: str) -> ReviewItem:
    if not isinstance(entry, dict):
        raise ValueError("Expected a review queue record")
    record: dict[str, Any] = dict(entry)
    record["provider_id"] = record.get("provider_id") or provider_id
    if provider_id and record["provider_id"] != provider_id:
        raise ValueError("Review queue contains a different provider owner")
    required = {"raw_category", "normalized_candidate", "source_exam_id", "year_roc"}
    if not required.issubset(record) or type(record["year_roc"]) is not int:
        raise ValueError("Review queue record is missing keys or has an invalid year")
    if set(record).difference((*REVIEW_FIELDS, "provider_id")):
        raise ValueError("Review queue record has unsupported fields")
    if any(not isinstance(value, str) for key, value in record.items() if key != "year_roc"):
        raise ValueError("Review queue text fields must be strings")
    return ReviewItem(**record)


def decode_review_queue(payload: object, provider_id: str = "") -> list[ReviewItem]:
    if isinstance(payload, list):
        return [_legacy_review(entry, provider_id) for entry in payload]
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("Unsupported review queue schema_version")
    owner = payload.get("provider_id")
    if not isinstance(owner, str) or not owner or (provider_id and owner != provider_id):
        raise ValueError("Review queue contains a different provider owner")
    if payload.get("fields") != list(REVIEW_FIELDS):
        raise ValueError("Review queue fields differ from the supported contract")
    table, rows = payload.get("strings"), payload.get("items")
    if not isinstance(table, list) or not isinstance(rows, list):
        raise ValueError("Review queue tables must be arrays")
    strings: list[str] = []
    previous = ""
    for entry in table:
        if (
            not isinstance(entry, (list, tuple))
            or len(entry) != 2
            or type(entry[0]) is not int
            or not isinstance(entry[1], str)
            or not 0 <= entry[0] <= len(previous)
        ):
            raise ValueError("Invalid review queue prefix table entry")
        current = previous[: entry[0]] + entry[1]
        if strings and current <= previous:
            raise ValueError("Review queue strings must be unique and sorted")
        strings.append(current)
        previous = current
    reviews: list[ReviewItem] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(REVIEW_FIELDS):
            raise ValueError("Review queue row has the wrong field count")
        record: dict[str, Any] = {"provider_id": owner}
        for column, (field, value) in enumerate(zip(REVIEW_FIELDS, row, strict=True)):
            if type(value) is not int:
                raise ValueError("Review queue cells must be integers")
            if column == YEAR_COLUMN:
                record[field] = value
            elif not 0 <= value < len(strings):
                raise ValueError("Review queue string reference is out of range")
            else:
                record[field] = strings[value]
        reviews.append(ReviewItem(**record))
    return reviews
