"""JSON Schema validation for the public `.keywave` wire format."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from .errors import ContractError, ErrorCode, ValidationIssue

SchemaName = Literal["manifest", "chart"]


def _repository_schema_path(name: SchemaName) -> Path:
    return (
        Path(__file__).resolve().parents[3] / "contract" / "schemas" / "1.0" / f"{name}.schema.json"
    )


@lru_cache(maxsize=2)
def _validator(name: SchemaName) -> Draft202012Validator:
    """Load and compile one bundled schema.

    Editable installs read the canonical repository copy. Built wheels include
    the same directory under ``keywave_creator/_schemas``.
    """
    repository_path = _repository_schema_path(name)
    if repository_path.is_file():
        schema_text = repository_path.read_text(encoding="utf-8")
    else:
        resource = files("keywave_creator").joinpath("_schemas", "1.0", f"{name}.schema.json")
        with as_file(resource) as bundled_path:
            schema_text = bundled_path.read_text(encoding="utf-8")

    import json

    schema: dict[str, Any] = json.loads(schema_text)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_wire_document(value: dict[str, Any], *, name: SchemaName, member: str) -> None:
    """Validate a parsed JSON object and raise a stable contract error."""
    errors = sorted(
        _validator(name).iter_errors(value), key=lambda error: list(error.absolute_path)
    )
    if not errors:
        return

    error = errors[0]
    pointer = "/" + "/".join(str(part) for part in error.absolute_path)
    raise ContractError(
        ValidationIssue(
            ErrorCode.INVALID_MANIFEST if name == "manifest" else ErrorCode.INVALID_CHART,
            error.message,
            member=member,
            pointer=pointer,
        )
    )
