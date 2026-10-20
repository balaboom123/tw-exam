"""Official GEPT level and practice-set context, separate from storage dates."""

from __future__ import annotations

# Retain the existing acquisition partition and event keys. The redesigned
# site's /2022/ directory does not establish a paper's publication or exam year.
STORAGE_YEAR_AD = 2022
LEVELS = (
    ("elementary", "初級", "https://www.gept.org.tw/Exam_Intro/t01_introduction.asp"),
    ("intermediate", "中級", "https://www.gept.org.tw/Exam_Intro/t02_introduction.asp"),
    ("high-intermediate", "中高級", "https://www.gept.org.tw/Exam_Intro/t03_introduction.asp"),
    ("advanced", "高級", "https://www.gept.org.tw/Exam_Intro/t04_introduction.asp"),
    ("superior", "優級", "https://www.gept.org.tw/Exam_Intro/t05_introduction.asp"),
)


def event_code(level_code: str) -> str:
    if level_code not in {code for code, _, _ in LEVELS}:
        raise ValueError("Unsupported GEPT source level")
    return f"gept-cert-{level_code}-{STORAGE_YEAR_AD}"


def category_identity(
    source_exam_id: str, category_code: str, year_ad: int
) -> tuple[str, str, tuple[tuple[str, str], ...]] | None:
    if year_ad != STORAGE_YEAR_AD:
        return None
    for level, label, _ in LEVELS:
        if source_exam_id != event_code(level):
            continue
        if category_code == level:
            return level, label, ()
        if level == "advanced":
            for practice_set in (1, 2):
                if category_code == f"advanced-practice-{practice_set}":
                    return (
                        level,
                        label,
                        ((f"practice-set-{practice_set}", f"練習題{practice_set}"),),
                    )
    return None
