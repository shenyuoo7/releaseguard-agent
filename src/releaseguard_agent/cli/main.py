import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from releaseguard_agent.agents import (
    ReleaseDecisionAdviceArtifacts,
    ReleaseDecisionAdviceResult,
    ReleaseDecisionAgent,
    ReleaseDecisionExplainer,
    ReleaseDecisionWorkflowResult,
    get_default_rule_index_path,
    write_advice_artifacts,
)
from releaseguard_agent.core.default_checkers import (
    build_default_python_runner,
    get_default_python_checker_names,
)
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.observability import (
    build_trace_payload,
    write_trace_artifact,
)
from releaseguard_agent.reports import (
    ReleaseChecklistArtifacts,
    ReportArtifacts,
    build_release_checklist_payload,
    build_report_payload,
    write_release_checklist_artifact,
    write_report_artifacts,
)

EXIT_SUCCESS = 0
EXIT_BLOCKING_ISSUES = 1
EXIT_USAGE_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="releaseguard",
        description="Run ReleaseGuard pre-release checks.",
    )
    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser(
        "check",
        help="Run release-readiness checks for a project.",
    )
    check_parser.add_argument(
        "project_path",
        nargs="?",
        default=".",
        help="Target project path. Defaults to the current directory.",
    )
    check_parser.add_argument(
        "--skip-pytest-execution",
        action="store_true",
        help="Skip dynamic pytest collection and test execution checks.",
    )
    check_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="Output format. Defaults to text.",
    )
    check_parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Write release_report.md and check_result.json "
            "to this directory."
        ),
    )
    check_parser.add_argument(
        "--checklist-output-dir",
        default=None,
        help="Write release_checklist.md to this directory.",
    )
    check_parser.add_argument(
        "--agent-advice-output-dir",
        default=None,
        help=(
            "Write release_decision_advice.md and "
            "release_decision_advice.json to this directory."
        ),
    )
    check_parser.add_argument(
        "--trace-output-dir",
        default=None,
        help="Write trace.json to this directory.",
    )
    check_parser.set_defaults(handler=run_check_command)

    list_parser = subparsers.add_parser(
        "list-checkers",
        help="List the default Python checkers.",
    )
    list_parser.add_argument(
        "--skip-pytest-execution",
        action="store_true",
        help="Exclude the dynamic pytest execution checker from the list.",
    )
    list_parser.set_defaults(handler=run_list_checkers_command)

    return parser


def run_check_command(args: argparse.Namespace) -> int:
    project_path = Path(args.project_path).resolve()

    if not project_path.exists():
        _print_error(f"Project path does not exist: {project_path}")
        return EXIT_USAGE_ERROR

    if not project_path.is_dir():
        _print_error(f"Project path is not a directory: {project_path}")
        return EXIT_USAGE_ERROR

    include_pytest_execution = not args.skip_pytest_execution
    runner = build_default_python_runner(
        include_pytest_execution=include_pytest_execution
    )
    results = runner.run(project_path)
    summary = build_result_summary(results)

    payload = build_report_payload(
        project_path=project_path,
        include_pytest_execution=include_pytest_execution,
        results=results,
        summary=summary,
    )

    report_artifacts = None
    checklist_artifacts = None
    advice_artifacts = None
    advice_result = None

    if args.output_dir is not None:
        output_dir = Path(args.output_dir).expanduser().resolve()

        try:
            report_artifacts = write_report_artifacts(
                output_dir=output_dir,
                payload=payload,
            )
        except (OSError, TypeError, ValueError) as exc:
            _print_error(
                f"Could not write report artifacts to {output_dir}: {exc}"
            )
            return EXIT_USAGE_ERROR

    if args.checklist_output_dir is not None:
        checklist_output_dir = (
            Path(args.checklist_output_dir).expanduser().resolve()
        )

        try:
            checklist_payload = build_release_checklist_payload(
                project_path=project_path,
                include_pytest_execution=include_pytest_execution,
                results=results,
                summary=summary,
            )
            checklist_artifacts = write_release_checklist_artifact(
                output_dir=checklist_output_dir,
                payload=checklist_payload,
            )
        except (OSError, TypeError, ValueError) as exc:
            _print_error(
                "Could not write release checklist artifact to "
                f"{checklist_output_dir}: {exc}"
            )
            return EXIT_USAGE_ERROR

    if args.agent_advice_output_dir is not None:
        agent_advice_output_dir = (
            Path(args.agent_advice_output_dir).expanduser().resolve()
        )

        try:
            advice_result = build_agent_advice_result(
                project_path=project_path,
                results=results,
            )
            advice_artifacts = write_advice_artifacts(
                output_dir=agent_advice_output_dir,
                advice_result=advice_result,
            )
        except (OSError, TypeError, ValueError) as exc:
            _print_error(
                "Could not write Agent advice artifacts to "
                f"{agent_advice_output_dir}: {exc}"
            )
            return EXIT_USAGE_ERROR

    if args.trace_output_dir is not None:
        trace_output_dir = Path(args.trace_output_dir).expanduser().resolve()

        try:
            trace_payload = build_cli_trace_payload(
                args=args,
                project_path=project_path,
                include_pytest_execution=include_pytest_execution,
                summary=summary,
                report_artifacts=report_artifacts,
                checklist_artifacts=checklist_artifacts,
                advice_artifacts=advice_artifacts,
                advice_result=advice_result,
            )
            write_trace_artifact(
                output_dir=trace_output_dir,
                payload=trace_payload,
            )
        except (OSError, TypeError, ValueError) as exc:
            _print_error(
                f"Could not write trace artifact to {trace_output_dir}: {exc}"
            )
            return EXIT_USAGE_ERROR

    if args.output_format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(
            format_text_report(
                project_path=project_path,
                include_pytest_execution=include_pytest_execution,
                results=results,
                summary=summary,
            )
        )

    if summary["blocking"] > 0:
        return EXIT_BLOCKING_ISSUES

    return EXIT_SUCCESS


def run_list_checkers_command(args: argparse.Namespace) -> int:
    names = get_default_python_checker_names(
        include_pytest_execution=not args.skip_pytest_execution
    )

    for name in names:
        print(name)

    return EXIT_SUCCESS


def build_result_summary(results: list[CheckResult]) -> dict[str, object]:
    status_counts = {status.value: 0 for status in CheckStatus}
    risk_counts = {risk.value: 0 for risk in RiskLevel}

    for result in results:
        status_counts[result.status.value] += 1
        risk_counts[result.risk_level.value] += 1

    blocking_count = sum(
        1 for result in results if result.should_block_release
    )

    return {
        "total": len(results),
        "passed": status_counts[CheckStatus.PASSED.value],
        "failed": status_counts[CheckStatus.FAILED.value],
        "warning": status_counts[CheckStatus.WARNING.value],
        "skipped": status_counts[CheckStatus.SKIPPED.value],
        "blocking": blocking_count,
        "status_counts": status_counts,
        "risk_counts": risk_counts,
    }


def build_agent_advice_result(
    *,
    project_path: Path,
    results: list[CheckResult],
) -> ReleaseDecisionAdviceResult:
    """Build Agent advice from already computed checker results."""
    check_results = tuple(results)
    agent = ReleaseDecisionAgent.from_rule_index(
        get_default_rule_index_path()
    )
    decision = agent.decide(check_results)
    explanation = ReleaseDecisionExplainer().explain(decision)

    return ReleaseDecisionAdviceResult(
        workflow_result=ReleaseDecisionWorkflowResult(
            project_path=project_path,
            check_results=check_results,
            decision=decision,
        ),
        explanation=explanation,
    )


def build_cli_trace_payload(
    *,
    args: argparse.Namespace,
    project_path: Path,
    include_pytest_execution: bool,
    summary: dict[str, object],
    report_artifacts: ReportArtifacts | None,
    checklist_artifacts: ReleaseChecklistArtifacts | None,
    advice_artifacts: ReleaseDecisionAdviceArtifacts | None,
    advice_result: ReleaseDecisionAdviceResult | None,
) -> dict[str, object]:
    created_at = _utc_now_iso()

    return build_trace_payload(
        run_id=f"releaseguard-check-{created_at}",
        created_at=created_at,
        project_path=project_path,
        command_args=_build_check_trace_command_args(args),
        environment_summary={
            "include_pytest_execution": include_pytest_execution,
            "output_format": args.output_format,
        },
        input_artifacts={
            "rule_index": get_default_rule_index_path(),
        },
        output_artifacts=_build_trace_output_artifacts(
            report_artifacts=report_artifacts,
            checklist_artifacts=checklist_artifacts,
            advice_artifacts=advice_artifacts,
        ),
        decision_summary=_build_trace_decision_summary(
            summary=summary,
            advice_result=advice_result,
        ),
    )


def _build_check_trace_command_args(
    args: argparse.Namespace,
) -> list[str]:
    command_args = [
        "check",
        str(args.project_path),
    ]

    if args.skip_pytest_execution:
        command_args.append("--skip-pytest-execution")

    if args.output_format != "text":
        command_args.extend(["--format", args.output_format])

    if args.output_dir is not None:
        command_args.extend(["--output-dir", str(args.output_dir)])

    if args.checklist_output_dir is not None:
        command_args.extend(
            [
                "--checklist-output-dir",
                str(args.checklist_output_dir),
            ]
        )

    if args.agent_advice_output_dir is not None:
        command_args.extend(
            [
                "--agent-advice-output-dir",
                str(args.agent_advice_output_dir),
            ]
        )

    if args.trace_output_dir is not None:
        command_args.extend(
            [
                "--trace-output-dir",
                str(args.trace_output_dir),
            ]
        )

    return command_args


def _build_trace_output_artifacts(
    *,
    report_artifacts: ReportArtifacts | None,
    checklist_artifacts: ReleaseChecklistArtifacts | None,
    advice_artifacts: ReleaseDecisionAdviceArtifacts | None,
) -> dict[str, str]:
    artifacts: dict[str, str] = {}

    if report_artifacts is not None:
        artifacts["release_report"] = str(report_artifacts.markdown_path)
        artifacts["check_result"] = str(report_artifacts.json_path)

    if checklist_artifacts is not None:
        artifacts["release_checklist"] = str(
            checklist_artifacts.markdown_path
        )

    if advice_artifacts is not None:
        artifacts["release_decision_advice_markdown"] = str(
            advice_artifacts.markdown_path
        )
        artifacts["release_decision_advice_json"] = str(
            advice_artifacts.json_path
        )

    return artifacts


def _build_trace_decision_summary(
    *,
    summary: dict[str, object],
    advice_result: ReleaseDecisionAdviceResult | None,
) -> dict[str, object]:
    blocking_count = int(summary["blocking"])
    decision_summary: dict[str, object] = {
        "status": "blocked" if blocking_count > 0 else "ready",
        "release_allowed": blocking_count == 0,
        "blocking_count": blocking_count,
    }

    if advice_result is not None:
        decision = advice_result.workflow_result.decision
        decision_summary.update(
            {
                "status": decision.status.value,
                "release_allowed": decision.release_allowed,
                "blocking_rule_ids": list(decision.blocking_rule_ids),
                "warning_rule_ids": list(decision.warning_rule_ids),
                "missing_rule_evidence_count": (
                    decision.missing_rule_evidence_count
                ),
            }
        )

    return decision_summary


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def format_text_report(
    *,
    project_path: Path,
    include_pytest_execution: bool,
    results: list[CheckResult],
    summary: dict[str, object],
) -> str:
    lines = [
        "ReleaseGuard Agent",
        f"Project: {project_path}",
        f"Pytest execution: {_enabled_label(include_pytest_execution)}",
        (
            "Summary: "
            f"total: {summary['total']} | "
            f"passed: {summary['passed']} | "
            f"failed: {summary['failed']} | "
            f"warning: {summary['warning']} | "
            f"skipped: {summary['skipped']} | "
            f"blocking: {summary['blocking']}"
        ),
        "",
    ]

    for result in results:
        lines.extend(format_text_result(result))
        lines.append("")

    return "\n".join(lines).rstrip()


def format_text_result(result: CheckResult) -> list[str]:
    rule_text = f" [{result.rule_id}]" if result.rule_id else ""
    lines = [
        f"[{result.status.value.upper()}]{rule_text} {result.title}",
        f"  checker: {result.checker_name}",
        f"  risk: {result.risk_level.value}",
        f"  message: {result.message}",
    ]

    if result.file_path:
        lines.append(f"  file_path: {result.file_path}")

    if result.evidence:
        lines.append("  evidence:")
        for item in result.evidence:
            lines.append(f"    - {item}")

    if result.recommendation:
        lines.append(f"  recommendation: {result.recommendation}")

    return lines


def _enabled_label(enabled: bool) -> str:
    if enabled:
        return "enabled"

    return "disabled"


def _print_error(
    message: str,
    stream: TextIO | None = None,
) -> None:
    if stream is None:
        stream = sys.stderr

    print(f"Error: {message}", file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE_ERROR

    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
