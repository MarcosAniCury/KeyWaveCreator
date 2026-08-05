from __future__ import annotations

import sys
from pathlib import Path

import pytest

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken
from keywave_creator.infrastructure.process import ProcessRunner, ToolLocator


def test_tool_locator_prefers_explicit_reviewed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "reviewed-tool.exe"
    executable.write_bytes(b"tool")
    monkeypatch.setenv("KEYWAVE_TEST_TOOL", str(executable))

    resolved = ToolLocator().resolve("missing-tool.exe", environment_variable="KEYWAVE_TEST_TOOL")

    assert resolved == executable.resolve()


def test_tool_locator_reports_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEYWAVE_TEST_TOOL", raising=False)
    monkeypatch.setattr("keywave_creator.infrastructure.process.shutil.which", lambda _: None)

    with pytest.raises(CreatorError) as raised:
        ToolLocator().resolve("definitely-not-a-real-keywave-tool.exe")

    assert raised.value.code is CreatorErrorCode.TOOL_NOT_FOUND


def test_process_runner_captures_bounded_success_output() -> None:
    result = ProcessRunner().run(
        (sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"),
        timeout_seconds=10,
        cancellation=CancellationToken(),
    )

    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert result.elapsed_seconds >= 0


def test_process_runner_maps_failure_and_timeout() -> None:
    with pytest.raises(CreatorError) as failed:
        ProcessRunner().run(
            (sys.executable, "-c", "import sys; print('diagnostic', file=sys.stderr); sys.exit(3)"),
            timeout_seconds=10,
            cancellation=CancellationToken(),
        )
    assert failed.value.code is CreatorErrorCode.PROCESS_FAILED
    assert "diagnostic" in failed.value.diagnostic

    with pytest.raises(CreatorError) as timed_out:
        ProcessRunner().run(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            timeout_seconds=0.05,
            cancellation=CancellationToken(),
        )
    assert timed_out.value.code is CreatorErrorCode.PROCESS_TIMEOUT


def test_process_runner_honors_cancellation() -> None:
    cancellation = CancellationToken()
    cancellation.cancel()

    with pytest.raises(CreatorError) as raised:
        ProcessRunner().run(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            timeout_seconds=10,
            cancellation=cancellation,
        )

    assert raised.value.code is CreatorErrorCode.CANCELLED
