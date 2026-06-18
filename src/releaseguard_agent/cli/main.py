import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from releaseguard_agent.core.default_checkers import (
    build_default_python_runner,
    get_default_python_checker_names,
)
from releaseguard_agent.models.check_result import (
    CheckResult,
    CheckStatus,
    RiskLevel,
)
from releaseguard_agent.reports.report_writer import (
    build_report_payload,
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

    if args.output_dir is not None:
        output_dir = Path(args.output_dir).expanduser().resolve()

        try:
            write_report_artifacts(
                output_dir=output_dir,
                payload=payload,
            )
        except (OSError, TypeError, ValueError) as exc:
            _print_error(
                f"Could not write report artifacts to {output_dir}: {exc}"
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