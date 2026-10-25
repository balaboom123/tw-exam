from html import escape

import pytest

from app.providers.tcte_tve.client import parse_year_page

BASE = "https://web1.tcte.edu.tw/EXAM/099_4y/"


def button(url, label):
    return (
        f'<input type="button" value="{escape(label, quote=True)}" '
        f"onclick=\"location.href='{url}'\">"
    )


def page(questions, answers):
    return (
        "<table><tr><td>共同科目</td><td>數學科</td><td>"
        + "".join(button(url, label) for url, label in questions)
        + "</td><td>"
        + "".join(button(url, label) for url, label in answers)
        + "</td></tr></table>"
    )


@pytest.mark.parametrize("forms", ["ABC", "ABCD", "ABCS", "ABCZ"])
def test_labeled_buttons_preserve_every_math_form_and_pair_by_label(forms):
    html = page(
        [(f"{form}-question.pdf", f"數學({form})試題") for form in forms],
        [(f"{form}-answer.pdf", f"數學（{form}）標準答案") for form in reversed(forms)],
    )
    papers = parse_year_page(html, BASE)
    assert [paper.subject_name_raw for paper in papers] == [
        f"共同科目 數學({form})" for form in forms
    ]
    assert [paper.subject_code for paper in papers] == [
        f"math-{form.lower()}" if form in "ABC" else form.lower() for form in forms
    ]
    for form, paper in zip(forms, papers, strict=True):
        assert paper.files == {
            "question": BASE + f"{form}-question.pdf",
            "answer": BASE + f"{form}-answer.pdf",
        }


def test_one_shared_question_can_have_separate_labeled_answer_forms():
    html = page(
        [("combined-question.pdf", "試題")],
        [(f"{form}-answer.pdf", f"數學({form})答案") for form in "ABCD"],
    )
    papers = parse_year_page(html, BASE)
    assert len(papers) == 4
    assert {paper.files["question"] for paper in papers} == {BASE + "combined-question.pdf"}
    assert {paper.files["answer"] for paper in papers} == {
        BASE + f"{form}-answer.pdf" for form in "ABCD"
    }


@pytest.mark.parametrize("role", ["question", "answer"])
def test_conflicting_native_math_form_links_require_review(role):
    questions = [("A-question.pdf", "數學(A)試題")]
    answers = [("A-answer.pdf", "數學(A)答案")]
    target = questions if role == "question" else answers
    target.append((f"A-different-{role}.pdf", f"數學(A){role}"))
    with pytest.raises(ValueError, match="Conflicting.*mathematics"):
        parse_year_page(page(questions, answers), BASE)


def test_exact_repeated_native_math_link_does_not_duplicate_a_paper():
    item = ("A-question.pdf", "數學(A)試題")
    papers = parse_year_page(page([item, item], [("A-answer.pdf", "數學(A)答案")]), BASE)
    assert len(papers) == 1


def test_missing_form_cannot_inherit_another_form_from_multiple_question_books():
    html = page(
        [("A-question.pdf", "數學(A)試題"), ("B-question.pdf", "數學(B)試題")],
        [("A-answer.pdf", "數學(A)答案"), ("C-answer.pdf", "數學(C)答案")],
    )
    with pytest.raises(ValueError, match="Unpaired.*mathematics"):
        parse_year_page(html, BASE)


def test_explicit_single_form_is_not_an_unlabeled_shared_question_book():
    html = page(
        [("A-question.pdf", "數學(A)試題")],
        [("A-answer.pdf", "數學(A)答案"), ("B-answer.pdf", "數學(B)答案")],
    )
    with pytest.raises(ValueError, match="Unpaired.*mathematics"):
        parse_year_page(html, BASE)


def test_unlabeled_multiple_math_books_cannot_silently_keep_the_first():
    html = page(
        [("first-question.pdf", "試題"), ("second-question.pdf", "試題")],
        [("first-answer.pdf", "答案"), ("second-answer.pdf", "答案")],
    )
    with pytest.raises(ValueError, match="Unlabeled.*mathematics"):
        parse_year_page(html, BASE)
