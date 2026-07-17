from releaseguard_agent.api.report_localization import (
    decision_view,
    format_zh_datetime,
    localize_check,
)


def _check(*, rule_id: str, status: str, title: str = "English title") -> dict[str, object]:
    return {
        "checker_name": "test_checker",
        "status": status,
        "risk_level": "medium",
        "title": title,
        "message": "English technical message.",
        "recommendation": "English recommendation.",
        "rule_id": rule_id,
        "file_path": None,
    }


def test_checker_copy_and_status_are_localized_without_changing_stable_ids() -> None:
    localized = localize_check(
        _check(rule_id="RG-TEST-006", status="warning"), 5
    )

    assert localized["check_result_id"] == "CHECK-005-RG-TEST-006"
    assert localized["rule_id"] == "RG-TEST-006"
    assert localized["status"] == "warning"
    assert localized["status_label"] == "警告"
    assert localized["severity_label"] == "中"
    assert localized["title"] == "Pytest 配置固定"
    assert "持续集成" in str(localized["message"])
    assert localized["technical_original"]["title"] == "English title"


def test_unmapped_checker_uses_safe_chinese_fallback() -> None:
    localized = localize_check(
        _check(rule_id="RG-FUTURE-999", status="failed"), 1
    )

    assert localized["status_label"] == "未通过"
    assert localized["title"] == "RG-FUTURE-999 发布检查"
    assert "高级详情" in str(localized["message"])


def test_release_decision_has_four_user_facing_states() -> None:
    assert decision_view({"blocking": 0, "warning": 0})["label"] == "可以发布"
    assert decision_view({"blocking": 0, "warning": 2})["label"] == (
        "可以发布，但建议先修复 2 项问题"
    )
    assert decision_view({"blocking": 1, "warning": 0})["label"] == "暂不建议发布"
    assert decision_view(
        {"blocking": 0, "warning": 0}, manual_review=True
    )["label"] == "需要人工复核"


def test_iso_time_is_rendered_in_chinese_format() -> None:
    rendered = format_zh_datetime("2026-07-17T09:08:03+00:00")

    assert rendered.startswith("2026年7月17日 ")
    assert rendered.endswith(":03")
