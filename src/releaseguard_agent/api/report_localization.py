from __future__ import annotations

import copy
from datetime import datetime
from typing import Any


STATUS_LABELS = {
    "passed": "已通过",
    "warning": "警告",
    "failed": "未通过",
    "blocking": "阻断",
    "skipped": "不适用",
    "error": "执行错误",
}

SEVERITY_LABELS = {
    "info": "信息",
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
}

ROUTE_LABELS = {
    "scan": "读取项目并执行确定性检查",
    "evidence_agent": "证据智能体检索规则依据",
    "risk_agent": "风险智能体分析发布风险",
    "fix_planner_agent": "修复规划智能体生成计划",
    "verifier_agent": "复检智能体比较修改前后",
    "deterministic_fallback": "模型异常，保留确定性结果",
    "finalize_clean": "完成无阻断项目审查",
    "verification_complete": "完成修复后复检",
    "manual_review": "证据不足，转人工复核",
    "deterministic_complete": "完成确定性基础扫描",
}


_RULE_COPY: dict[str, dict[str, Any]] = {
    "RG-DEPS-001": {
        "title": "Python 依赖声明",
        "passed": "项目已提供可识别的 Python 依赖声明。",
        "warning": "项目缺少可识别的 Python 依赖声明。",
        "failed": "发布所需的 Python 依赖没有明确声明。",
        "recommendation": "在 requirements.txt 或 pyproject.toml 中明确并维护运行依赖。",
    },
    "RG-CONFIG-001": {
        "title": "环境变量示例文件",
        "passed": "项目已提供 .env.example，可用于说明所需环境变量。",
        "warning": "项目缺少 .env.example，部署所需配置不易复现。",
        "failed": "项目没有安全的环境变量示例文件。",
        "recommendation": "新增不包含真实密钥的 .env.example，并说明每个配置项用途。",
    },
    "RG-TEST-001": {
        "title": "测试目录组织",
        "passed": "项目根目录已包含 tests/ 测试目录。",
        "warning": "项目根目录缺少 tests/，现有测试文件可能分散在其他位置。",
        "failed": "项目没有统一的测试目录。",
        "recommendation": "创建 tests/ 并迁移现有测试文件，随后检查导入和测试收集结果。",
    },
    "RG-TEST-002": {
        "title": "Pytest 测试文件发现",
        "passed": "已发现符合 Pytest 命名规则的测试文件。",
        "warning": "没有发现可被 Pytest 自动收集的测试文件。",
        "failed": "项目缺少可执行的 Pytest 测试文件。",
        "recommendation": "增加 test_*.py 或 *_test.py 测试文件并验证收集结果。",
    },
    "RG-TEST-003": {
        "title": "Pytest 测试收集",
        "passed": "Pytest 能够收集到至少一个测试。",
        "warning": "Pytest 没有收集到预期测试。",
        "failed": "Pytest 测试收集失败。",
        "skipped": "本次审查未执行 Pytest 测试收集。",
        "recommendation": "运行 python -m pytest --collect-only -q 并修复收集错误。",
    },
    "RG-TEST-004": {
        "title": "Pytest 收集命令",
        "passed": "Pytest 收集命令执行成功。",
        "warning": "Pytest 收集命令存在异常。",
        "failed": "Pytest 收集命令执行失败。",
        "skipped": "本次审查未执行 Pytest 收集命令。",
        "recommendation": "修复测试导入或配置后重新运行收集命令。",
    },
    "RG-TEST-005": {
        "title": "Pytest 测试执行",
        "passed": "项目测试执行成功。",
        "warning": "项目测试存在失败或执行异常。",
        "failed": "项目测试未通过。",
        "skipped": "本次快速审查未执行项目测试。",
        "recommendation": "在目标项目环境中运行 python -m pytest -q 并处理失败用例。",
    },
    "RG-TEST-006": {
        "title": "Pytest 配置固定",
        "passed": "项目已提供可识别的 Pytest 配置。",
        "warning": "项目根目录缺少固定的 Pytest 配置，本地与持续集成的发现行为可能不一致。",
        "failed": "项目缺少可复现的 Pytest 配置。",
        "recommendation": "新增 pytest.ini，或在 pyproject.toml 中配置 [tool.pytest.ini_options]。",
    },
    "RG-TEST-007": {
        "title": "src 布局导入配置",
        "passed": "src 布局的测试导入配置已明确。",
        "warning": "项目使用 src 布局，但测试导入配置不完整。",
        "failed": "src 布局无法稳定支持测试导入。",
        "skipped": "项目未采用根目录 src/ 布局，此项不适用。",
        "recommendation": "配置可复现的包安装或 Python 导入路径。",
    },
    "RG-FASTAPI-001": {
        "title": "FastAPI 依赖声明",
        "passed": "已同时发现 FastAPI 源码使用和依赖声明。",
        "warning": "发现 FastAPI 源码使用，但依赖声明不完整。",
        "failed": "FastAPI 运行依赖缺失。",
        "skipped": "未检测到 FastAPI 源码使用，此项不适用。",
        "recommendation": "在项目依赖文件中明确声明 FastAPI。",
    },
    "RG-FASTAPI-002": {
        "title": "FastAPI 应用实例",
        "passed": "已检测到明确的 FastAPI 应用实例。",
        "warning": "没有检测到明确的 FastAPI 应用实例。",
        "failed": "FastAPI 项目缺少可识别的应用实例。",
        "skipped": "未检测到 FastAPI 项目特征，此项不适用。",
        "recommendation": "确认发布入口中存在可导入的 FastAPI() 应用对象。",
    },
    "RG-FLASK-001": {
        "title": "Flask 依赖声明",
        "passed": "Flask 依赖声明完整。",
        "warning": "发现 Flask 源码使用，但依赖声明不完整。",
        "failed": "Flask 运行依赖缺失。",
        "skipped": "未检测到 Flask 源码使用，此项不适用。",
        "recommendation": "在项目依赖文件中明确声明 Flask。",
    },
    "RG-FLASK-002": {
        "title": "Flask 应用实例",
        "passed": "已检测到 Flask 应用实例。",
        "warning": "没有检测到明确的 Flask 应用实例。",
        "failed": "Flask 项目缺少可识别的应用实例。",
        "skipped": "未检测到 Flask 源码使用，此项不适用。",
        "recommendation": "确认发布入口中存在可导入的 Flask 应用对象。",
    },
    "RG-FLASK-003": {
        "title": "Flask 生产启动方式",
        "passed": "发布启动方式未依赖 Flask 开发服务器。",
        "warning": "发布配置可能依赖 Flask 开发服务器。",
        "failed": "生产发布依赖 Flask 开发服务器。",
        "skipped": "未检测到 Flask 源码使用，此项不适用。",
        "recommendation": "使用适合生产环境的 WSGI 服务器和启动命令。",
    },
    "RG-SEC-002": {
        "title": "Flask 调试模式",
        "passed": "未发现生产环境启用 Flask 调试模式。",
        "warning": "发现可能启用 Flask 调试模式的配置。",
        "failed": "生产路径启用了 Flask 调试模式。",
        "skipped": "未检测到 Flask 源码使用，此项不适用。",
        "recommendation": "确保发布环境关闭 debug，并通过环境配置区分开发与生产。",
    },
}

for _rule_id, _title in {
    "RG-DOCKER-001": "根目录 Dockerfile",
    "RG-DOCKER-002": "Dockerfile FROM 指令",
    "RG-DOCKER-003": "FROM 指令位置",
    "RG-DOCKER-004": "Dockerfile WORKDIR 指令",
    "RG-DOCKER-005": "Dockerfile COPY 或 ADD 指令",
    "RG-DOCKER-006": "容器依赖安装步骤",
    "RG-DOCKER-007": "容器启动指令",
    "RG-DOCKER-008": "Dockerfile 指令风格",
}.items():
    _RULE_COPY[_rule_id] = {
        "title": _title,
        "passed": f"{_title}检查已通过。",
        "warning": f"{_title}存在需要确认的问题。",
        "failed": f"{_title}未满足发布要求。",
        "skipped": "未检测到明确的根目录容器发布意图，此项不适用。",
        "recommendation": f"根据项目的容器发布方式补齐或修正{_title}。",
    }


def localize_check(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Build a stable zh-CN view model without changing checker facts."""
    status = str(raw.get("status", "error"))
    severity = str(raw.get("risk_level", "info"))
    rule_id = str(raw.get("rule_id") or "")
    copy_block = _RULE_COPY.get(rule_id, {})
    result = copy.deepcopy(raw)
    result.update(
        {
            "check_result_id": f"CHECK-{index:03d}-{rule_id or 'NO-RULE'}",
            "status_label": STATUS_LABELS.get(status, "未知状态"),
            "severity_label": SEVERITY_LABELS.get(severity, "未知"),
            "title": copy_block.get("title", f"{rule_id or '项目'} 发布检查"),
            "message": copy_block.get(
                status,
                f"该项检查状态为“{STATUS_LABELS.get(status, '未知')}”，请在高级详情中核对技术原文。",
            ),
            "recommendation": (
                copy_block.get("recommendation")
                if status in {"warning", "failed", "error"}
                else None
            ),
            "technical_original": {
                "title": raw.get("title"),
                "message": raw.get("message"),
                "recommendation": raw.get("recommendation"),
            },
        }
    )
    return result


def localize_evidence(
    raw: dict[str, Any],
    related_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(raw)
    rule_id = str(raw.get("rule_id") or "")
    title = rule_title_zh(rule_id)
    finding_titles = [str(item["title"]) for item in related_checks]
    result.update(
        {
            "title": title,
            "summary_zh": (
                f"规则 {rule_id} 为“{title}”提供发布检查依据。"
                + (
                    f" 本次关联问题：{'、'.join(finding_titles)}。"
                    if finding_titles
                    else ""
                )
            ),
            "related_findings": finding_titles,
        }
    )
    return result


def rule_title_zh(rule_id: str) -> str:
    return str(_RULE_COPY.get(rule_id, {}).get("title", "发布准备规则"))


def decision_view(summary: dict[str, object], *, manual_review: bool = False) -> dict[str, Any]:
    blocking = _summary_count(summary, "blocking")
    warning = _summary_count(summary, "warning")
    failed = _summary_count(summary, "failed")
    if blocking:
        return {"release_allowed": False, "label": "暂不建议发布", "state": "blocked"}
    if manual_review or failed:
        return {"release_allowed": True, "label": "需要人工复核", "state": "review"}
    if warning:
        return {
            "release_allowed": True,
            "label": f"可以发布，但建议先修复 {warning} 项问题",
            "state": "warning",
        }
    return {"release_allowed": True, "label": "可以发布", "state": "ready"}


def format_zh_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
    except (TypeError, ValueError):
        return "时间未知"
    return f"{parsed.year}年{parsed.month}月{parsed.day}日 {parsed:%H:%M:%S}"


def localize_route(route: str) -> str:
    return ROUTE_LABELS.get(route, "内部流程步骤")


def _summary_count(summary: dict[str, object], key: str) -> int:
    value = summary.get(key, 0)
    return value if isinstance(value, int) else 0
