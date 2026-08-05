"""Bounded, cancellable, shell-free external process execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from keywave_creator.application.errors import CreatorError, CreatorErrorCode
from keywave_creator.application.models import CancellationToken

MAX_DIAGNOSTIC_BYTES = 1024 * 1024


class _BinaryStream(Protocol):
    def seek(self, offset: int, whence: int = 0) -> int: ...

    def tell(self) -> int: ...

    def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    elapsed_seconds: float


class ToolLocator:
    """Resolve reviewed executables to absolute paths."""

    def resolve(self, executable_name: str, *, environment_variable: str | None = None) -> Path:
        candidates: list[Path] = []
        if environment_variable and (configured := os.environ.get(environment_variable)):
            candidates.append(Path(configured))

        application_directory = Path(sys.executable).resolve().parent
        candidates.extend(
            (
                application_directory / "tools" / executable_name,
                application_directory / executable_name,
            )
        )
        if discovered := shutil.which(executable_name):
            candidates.append(Path(discovered))

        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved.is_file():
                return resolved
        raise CreatorError(
            CreatorErrorCode.TOOL_NOT_FOUND,
            f"Required tool '{executable_name}' was not found.",
        )

    def optional(self, executable_name: str) -> Path | None:
        try:
            return self.resolve(executable_name)
        except CreatorError as error:
            if error.code is CreatorErrorCode.TOOL_NOT_FOUND:
                return None
            raise


class ProcessRunner:
    """Execute a child process with bounded diagnostics and deterministic cleanup."""

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout_seconds: float,
        cancellation: CancellationToken,
        working_directory: Path | None = None,
    ) -> CommandResult:
        arguments = tuple(os.fspath(item) for item in command)
        if not arguments:
            raise ValueError("A process command cannot be empty.")
        executable = Path(arguments[0])
        if not executable.is_absolute() or not executable.is_file():
            raise CreatorError(
                CreatorErrorCode.TOOL_NOT_FOUND,
                "The configured external tool is unavailable.",
                diagnostic=executable.name,
            )

        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        started = time.monotonic()
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                process = subprocess.Popen(
                    arguments,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=working_directory,
                    shell=False,
                    close_fds=True,
                    creationflags=creation_flags,
                )
            except OSError as error:
                raise CreatorError(
                    CreatorErrorCode.PROCESS_FAILED,
                    f"Could not start {executable.name}.",
                    diagnostic=str(error),
                ) from error

            while process.poll() is None:
                if cancellation.wait(0.05):
                    self._terminate(process)
                    cancellation.raise_if_cancelled()
                if time.monotonic() - started > timeout_seconds:
                    self._terminate(process)
                    raise CreatorError(
                        CreatorErrorCode.PROCESS_TIMEOUT,
                        f"{executable.name} did not finish in time.",
                    )

            elapsed = time.monotonic() - started
            stdout = self._read_tail(stdout_file)
            stderr = self._read_tail(stderr_file)
            if process.returncode != 0:
                raise CreatorError(
                    CreatorErrorCode.PROCESS_FAILED,
                    f"{executable.name} could not process the media.",
                    diagnostic=(stderr or stdout)[-4000:],
                )
            return CommandResult(stdout=stdout, stderr=stderr, elapsed_seconds=elapsed)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    @staticmethod
    def _read_tail(stream: _BinaryStream) -> str:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - MAX_DIAGNOSTIC_BYTES))
        return stream.read().decode("utf-8", errors="replace")
