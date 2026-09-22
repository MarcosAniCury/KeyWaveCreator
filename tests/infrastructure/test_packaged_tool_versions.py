"""Keep the reviewed downloader release consistent across all distributions."""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_windows_and_linux_downloaders_match_the_python_dependency() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = next(
        item for item in metadata["project"]["dependencies"] if item.startswith("yt-dlp[")
    )
    year, month, day = (int(part) for part in requirement.split("==")[1].split("."))
    release = f"{year:04d}.{month:02d}.{day:02d}"
    for script in ("build_creator.ps1", "build_creator_linux.sh"):
        content = (ROOT / "build" / script).read_text(encoding="utf-8")
        assert f"/yt-dlp/releases/download/{release}/" in content
        assert f"yt-dlp-{release}" in content


@pytest.mark.parametrize(
    ("script", "expected_hash"),
    [
        (
            "build_creator.ps1",
            "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a",
        ),
        (
            "build_creator_linux.sh",
            "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6",
        ),
    ],
)
def test_downloader_artifacts_use_reviewed_release_hashes(script: str, expected_hash: str) -> None:
    content = (ROOT / "build" / script).read_text(encoding="utf-8")
    assert expected_hash in content
