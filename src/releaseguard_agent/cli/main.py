import argparse
import json
import os
import sys
from pathlib import Path
from typing import TextIO

from releaseguard_agent.core.default_checkers import (
    get_default_python_checker_names,
)
from releaseguard_agent.models.check_result import CheckResult
from releaseguard_agent.rag import (
    RuleRetrievalService,
    build_embedding_model,
    get_default_rule_index_path,
)
from releaseguard_agent.llm import (
    LLMProviderConfigurationError,
    build_llm_runtime,
)
from releaseguard_agent.services import (
    InvalidProjectPathError,
    LLMReviewService,
    ReleaseReviewService,
    ReviewArtifactError,
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
    check_parser.add_argument(
        "--llm-analysis-output-dir",
        default=None,
        help=(
            "Optionally write LLM risk analysis/fix artifacts. Requires "
            "explicit RELEASEGUARD_LLM_* provider configuration."
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

    search_parser = subparsers.add_parser(
        "search-rules",
        help="Search trusted release-rule evidence.",
    )
    search_parser.add_argument("query")
    search_parser.add_argument(
        "--mode",
        choices=("exact", "bm25", "vector", "hybrid"),
        default="hybrid",
    )
    search_parser.add_argument("--top-k", type=int, default=5)
    search_parser.set_defaults(handler=run_search_rules_command)

    return parser


def run_check_command(args: argparse.Namespace) -> int:
    include_pytest_execution = not args.skip_pytest_execution
    service = ReleaseReviewService()
    try:
        review_result = service.review(
            project_path=Path(args.project_path),
            include_pytest_execution=include_pytest_execution,
            report_output_dir=_optional_path(args.output_dir),
            checklist_output_dir=_optional_path(args.checklist_output_dir),
            advice_output_dir=_optional_path(args.agent_advice_output_dir),
            trace_output_dir=_optional_path(args.trace_output_dir),
            command_args=_build_check_trace_command_args(args),
            output_format=args.output_format,
        )
    except (InvalidProjectPathError, ReviewArtifactError) as exc:
        _print_error(str(exc))
        return EXIT_USAGE_ERROR

    payload = review_result.report_payload
    results = list(review_result.check_results)
    summary = review_result.summary

    if args.llm_analysis_output_dir is not None:
        try:
            runtime = build_llm_runtime(os.environ)
            LLMReviewService(runtime).analyze(
                review=review_result,
                output_dir=Path(args.llm_analysis_output_dir),
            )
        except (
            LLMProviderConfigurationError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            _print_error(str(exc))
            return EXIT_USAGE_ERROR

    if args.output_format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(
            format_text_report(
                project_path=review_result.project_path,
                include_pytest_execution=include_pytest_execution,
                results=results,
                summary=summary,
            )
        )

    if not review_result.release_allowed:
        return EXIT_BLOCKING_ISSUES

    return EXIT_SUCCESS


def run_list_checkers_command(args: argparse.Namespace) -> int:
    names = get_default_python_checker_names(
        include_pytest_execution=not args.skip_pytest_execution
    )

    for name in names:
        print(name)

    return EXIT_SUCCESS


def run_search_rules_command(args: argparse.Namespace) -> int:
    try:
        embed_model = (
            build_embedding_model(os.environ)
            if args.mode in {"vector", "hybrid"}
            else None
        )
        result = RuleRetrievalService(
            get_default_rule_index_path(),
            embed_model=embed_model,
        ).retrieve(args.query, mode=args.mode, top_k=args.top_k)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_USAGE_ERROR
    print(json.dumps(result.to_dict(), indent=2))
    return EXIT_SUCCESS


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

    if args.llm_analysis_output_dir is not None:
        command_args.extend(
            ["--llm-analysis-output-dir", str(args.llm_analysis_output_dir)]
        )

    return command_args


def _optional_path(value: str | None) -> Path | None:
    if value is None:
        return None
    return Path(value)


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
