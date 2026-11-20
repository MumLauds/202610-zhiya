from app.services.reading_agent import matching_answer_excerpt


def test_matching_answer_excerpt_includes_detail_late_in_clause():
    page = "在夜色渐浓的森林深处小兔沿着弯弯曲曲的小路举起提灯照亮脚下的石头。"
    excerpt = matching_answer_excerpt(page, "我看见了提灯")

    assert excerpt is not None
    quoted_text = excerpt.strip("…")
    assert "提灯" in quoted_text
    assert len(quoted_text) <= 18
    assert quoted_text in page


def test_matching_answer_excerpt_rejects_shared_boilerplate():
    page = "小兔觉得风越来越大，赶紧把窗户关上。"

    assert matching_answer_excerpt(page, "我觉得他会回家") is None


def test_matching_answer_excerpt_accepts_two_character_story_detail():
    page = "小兔觉得风越来越大，赶紧把窗户关上。"

    assert matching_answer_excerpt(page, "小兔") == "小兔觉得风越来越大"
