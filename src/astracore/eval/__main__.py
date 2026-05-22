"""CLI entry point: python -m astracore.eval --cases cases.json [--output report.json]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def _load_cases(path: str):
    from uuid import UUID

    from astracore.eval.dataset import EvalCase
    from astracore.modules.chat.domain.chat_options import ChatOptions

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}")
    cases = []
    for item in data:
        skill_id_raw = item.get("skill_id")
        options = ChatOptions(
            model_profile=item.get("model_profile"),
            use_tools=bool(item.get("use_tools", False)),
            enable_thinking=bool(item.get("enable_thinking", False)),
            thinking_budget=int(item.get("thinking_budget", 8000)),
            enable_rag=bool(item.get("enable_rag", False)),
            enable_web=bool(item.get("enable_web", False)),
            skill_id=UUID(skill_id_raw) if skill_id_raw else None,
            disable_skill=bool(item.get("disable_skill", False)),
        )
        cases.append(
            EvalCase(
                input=item.get("input", ""),
                expected_output=item.get("expected_output"),
                expected_tool_calls=item.get("expected_tool_calls"),
                options=options,
                tags=item.get("tags", []),
                workflow_name=item.get("workflow_name"),
                workflow_tasks=item.get("workflow_tasks"),
            )
        )
    return cases


async def _main(args: argparse.Namespace) -> int:
    from astracore.eval.runner import EvalRunner
    from astracore.sdk import AstraCoreClient

    cases = _load_cases(args.cases)
    print(f"Loaded {len(cases)} eval case(s) from {args.cases}")

    async with AstraCoreClient() as client:
        runner = EvalRunner(
            client,
            judge_profile=args.judge_profile,
            relevance_threshold=args.threshold,
            concurrency=args.concurrency,
        )
        report = await runner.run(cases)

    print(report.summary())

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report.to_json())
        print(f"\nReport written to {args.output}")

    return 0 if report.pass_rate >= args.threshold else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m astracore.eval",
        description="AstraCoreAI Agent Evaluation Framework",
    )
    parser.add_argument("--cases", required=True, help="Path to JSON file with EvalCase list")
    parser.add_argument("--output", default=None, help="Write JSON report to this file")
    parser.add_argument(
        "--judge-profile",
        default=None,
        dest="judge_profile",
        help="LLM profile for relevance scoring (omit to skip)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Pass rate threshold for non-zero exit code (default: 0.7)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Concurrent eval cases (default: 4)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
