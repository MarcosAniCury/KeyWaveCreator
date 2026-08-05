"""Generate the tiny rights-free cross-runtime contract fixture."""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from pathlib import Path

from keywave_creator.contract.models import Chart, Difficulty, Note, NoteType
from keywave_creator.contract.package import assemble_package

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "contract" / "fixtures" / "1.0" / "valid" / "synthetic.keywave"


def run_ffmpeg(*arguments: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
    )


def note_id(index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"keywave-fixture-note:{index}"))


def charts() -> tuple[Chart, ...]:
    additions = (
        (
            Difficulty.EASY,
            (
                Note(id=note_id(1), time_ms=500, lane=0),
                Note(
                    id=note_id(2),
                    time_ms=1_000,
                    lane=4,
                    type=NoteType.HOLD,
                    duration_ms=1_000,
                ),
            ),
        ),
        (Difficulty.MEDIUM, (Note(id=note_id(3), time_ms=1_500, lane=3),)),
        (
            Difficulty.HARD,
            (
                Note(id=note_id(4), time_ms=2_500, lane=1),
                Note(id=note_id(5), time_ms=2_500, lane=5),
            ),
        ),
        (Difficulty.EXTREME, (Note(id=note_id(6), time_ms=3_500, lane=2),)),
    )
    accumulated: list[Note] = []
    result: list[Chart] = []
    for difficulty, extra in additions:
        accumulated.extend(extra)
        result.append(
            Chart(
                chart_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"keywave-fixture-chart:{difficulty.value}")
                ),
                difficulty=difficulty,
                notes=tuple(sorted(accumulated)),
            )
        )
    return tuple(result)


def main() -> None:
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="keywave-fixture-") as temporary:
        temp = Path(temporary)
        audio = temp / "song.ogg"
        video = temp / "background.mp4"
        run_ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=5",
            "-c:a",
            "libvorbis",
            "-q:a",
            "5",
            str(audio),
        )
        run_ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "color=c=0x07111F:s=320x180:r=30:d=5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-movflags",
            "+faststart",
            str(video),
        )
        assemble_package(
            destination=DESTINATION,
            title="Synthetic Pulse",
            artist="KeyWave Tests",
            duration_ms=5_000,
            charts=charts(),
            audio_path=audio,
            video_path=video,
            cover_path=None,
            generator_version="0.1.0",
            algorithm_version="fixture-1",
            seed="fixture",
            generation_confidence=1.0,
        )


if __name__ == "__main__":
    main()
