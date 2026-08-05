"""Stable validation errors shared by Creator and Game."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_CONTAINER = "INVALID_CONTAINER"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    INVALID_PATH = "INVALID_PATH"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"
    INVALID_CHART = "INVALID_CHART"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    PACKAGE_VALIDATION_FAILED = "PACKAGE_VALIDATION_FAILED"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: ErrorCode
    message: str
    member: str | None = None
    pointer: str | None = None


class ContractError(ValueError):
    """Raised when an untrusted package violates the public contract."""

    def __init__(self, issue: ValidationIssue) -> None:
        self.issue = issue
        super().__init__(f"{issue.code}: {issue.message}")
