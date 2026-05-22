"""Agent evaluation framework.

快速开始::

    from astracore.sdk import AstraCoreClient
    from astracore.eval import EvalCase, EvalRunner

    cases = [
        EvalCase(input="1 + 1 等于多少?", expected_output="2"),
        EvalCase(
            input="列出当前目录的文件",
            expected_tool_calls=["list_directory"],
            use_tools=True,
        ),
    ]

    async with AstraCoreClient() as client:
        runner = EvalRunner(client)
        report = await runner.run(cases)
        print(report.summary())

命令行::

    python -m astracore.eval --cases path/to/cases.json
"""

from astracore.eval.dataset import EvalCase
from astracore.eval.report import EvalReport, EvalResult
from astracore.eval.runner import EvalRunner

__all__ = ["EvalCase", "EvalReport", "EvalResult", "EvalRunner"]
