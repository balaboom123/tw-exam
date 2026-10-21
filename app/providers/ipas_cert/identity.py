"""Native iPAS programme names; grades belong to each named programme."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Programme:
    code: str
    label: str

    @property
    def canonical_id(self) -> str:
        return f"ipas-cert-{self.code.lower()}"


PROGRAMMES = (
    Programme("3DP", "3D列印積層製造工程師"),
    Programme("AIAP", "AI應用規劃師"),
    Programme("AIOT", "AIoT應用工程師"),
    Programme("ANT", "天線設計工程師"),
    Programme("CPM", "色彩規劃管理師"),
    Programme("CV", "無形資產評價管理師"),
    Programme("EMC", "電磁相容工程師"),
    Programme("EVM", "電動車機電整合工程師"),
    Programme("FQA", "食品品保工程師"),
    Programme("ISE", "資訊安全工程師"),
    Programme("MDMT", "工具機機械設計工程師"),
    Programme("NZ", "淨零碳規劃管理師"),
    Programme("OIA", "營運智慧分析師"),
    Programme("PCB", "電路板製程工程師"),
    Programme("PMAE", "塑膠材料應用工程師"),
    Programme("SPE", "智慧生產工程師"),
)

GRADE_LABELS = {"basic": "初級", "intermediate": "中級", "advanced": "高級"}
ROUTE_LABELS = {
    "data-analysis": "數據分析",
    "machine-learning": "機器學習",
}
RESOURCE_STORAGE_YEAR = 2026


def programme_for_code(code: str) -> Programme:
    for programme in PROGRAMMES:
        if programme.code == code:
            return programme
    raise ValueError(f"Unreviewed iPAS programme: {code}")


def canonical_for_event(source_exam_id: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"ipas-cert-([a-z0-9]+)-.+", source_exam_id)
    if match is None:
        return None
    for programme in PROGRAMMES:
        if programme.code.lower() == match.group(1):
            return programme.canonical_id, f"iPAS {programme.label}"
    return None
