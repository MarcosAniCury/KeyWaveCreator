"""Stable application failures suitable for CLI and QML presentation."""

from __future__ import annotations

from enum import StrEnum


class CreatorErrorCode(StrEnum):
    INVALID_SOURCE = "INVALID_SOURCE"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    PROCESS_FAILED = "PROCESS_FAILED"
    PROCESS_TIMEOUT = "PROCESS_TIMEOUT"
    CANCELLED = "CANCELLED"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    PACKAGE_FAILED = "PACKAGE_FAILED"


class CreatorError(RuntimeError):
    """Represent one expected Creator failure with safe presentation text."""

    def __init__(
        self,
        code: CreatorErrorCode,
        message: str,
        *,
        diagnostic: str | None = None,
    ) -> None:
        self.code = code
        self.public_message = message
        self.diagnostic = diagnostic
        super().__init__(f"{code}: {message}")
