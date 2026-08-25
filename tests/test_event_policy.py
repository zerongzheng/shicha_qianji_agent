"""异常事件后处理策略选择测试。"""

from __future__ import annotations

from app.experiments.event_policy import EventPolicyTrial, select_event_policy


def _trial(
    min_event_length: int,
    merge_gap: int,
    *,
    event_f1: float,
    event_recall: float,
    false_events: float,
) -> EventPolicyTrial:
    """构造只保留选择逻辑所需差异的测试记录。"""

    return EventPolicyTrial(
        split="validation",
        min_event_length=min_event_length,
        merge_gap=merge_gap,
        file_count=17,
        point_f1=0.30,
        event_f1=event_f1,
        event_recall=event_recall,
        average_false_events=false_events,
        healthy_false_events=0.0,
    )


def test_event_policy_never_trades_baseline_recall_for_fewer_alerts() -> None:
    """低误报候选若降低验证集召回，不得成为冻结策略。"""

    baseline = _trial(3, 5, event_f1=0.45, event_recall=0.90, false_events=4.0)
    reliable = _trial(5, 10, event_f1=0.52, event_recall=0.90, false_events=3.0)
    silent = _trial(12, 30, event_f1=0.60, event_recall=0.80, false_events=1.0)

    assert select_event_policy((baseline, reliable, silent), baseline) == reliable


def test_event_policy_prefers_lower_false_events_when_f1_is_equal() -> None:
    """召回和事件 F1 相同时，应选择工单负担更低的策略。"""

    baseline = _trial(3, 5, event_f1=0.45, event_recall=0.90, false_events=4.0)
    noisy = _trial(5, 10, event_f1=0.52, event_recall=0.90, false_events=3.0)
    concise = _trial(5, 20, event_f1=0.52, event_recall=0.90, false_events=2.0)

    assert select_event_policy((baseline, noisy, concise), baseline) == concise
