"""judge.py 单测：mock lib.llm.chat，覆盖加权计分和"空/非 JSON 输出重试一次"的健壮性。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from deep_research_agent import judge as dr_judge


def _resp(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_weighted_total_uses_rubric_weights():
    scores = {
        "comprehensiveness": {"score": 4}, "insight": {"score": 4},
        "instruction_following": {"score": 4}, "readability": {"score": 4},
        "citation_trust": {"score": 4},
    }
    assert dr_judge.weighted_total(scores) == 4.0


def test_weighted_total_missing_dimension_counts_as_zero():
    assert dr_judge.weighted_total({"comprehensiveness": {"score": 5}}) == 1.25


def test_judge_report_parses_valid_json():
    with patch.object(dr_judge, "chat", return_value=_resp('{"comprehensiveness": {"score": 5, "reason": "ok"}}')):
        scores = dr_judge.judge_report("报告正文")
    assert scores["comprehensiveness"]["score"] == 5


def test_judge_report_retries_once_on_empty_response_then_succeeds():
    """真实碰到过一次模型返回空内容（见 PROGRESS.md）：重试一次应该能拿到结果。"""
    responses = [_resp(""), _resp('{"insight": {"score": 3, "reason": "ok"}}')]
    with patch.object(dr_judge, "chat", side_effect=responses) as mock_chat:
        scores = dr_judge.judge_report("报告正文")
    assert mock_chat.call_count == 2
    assert scores["insight"]["score"] == 3


def test_judge_report_gives_up_after_max_attempts_returns_empty_dict():
    with patch.object(dr_judge, "chat", return_value=_resp("不是 JSON")):
        scores = dr_judge.judge_report("报告正文", attempts=2)
    assert scores == {}
