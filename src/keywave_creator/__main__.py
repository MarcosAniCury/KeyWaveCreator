"""KeyWave Creator command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from threading import Event, Thread
from types import TracebackType
from typing import TextIO

from . import __version__
from .application.errors import CreatorError
from .application.models import (
    CancellationToken,
    CreateLevelRequest,
    ProgressUpdate,
    SourceKind,
)
from .application.output import infer_local_metadata
from .contract.errors import ContractError
from .contract.package import PackageReader

GAME_CREATION_PROTOCOL_VERSION = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keywave-creator")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    validate = subparsers.add_parser("validate", help="Validate a .keywave package")
    validate.add_argument("package", type=Path)
    create = subparsers.add_parser("create", help="Create a .keywave package")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--local", type=str, help="Authorized local audio/video file")
    source.add_argument("--youtube", type=str, help="One authorized public YouTube video")
    create.add_argument("--title", required=True)
    create.add_argument("--artist", required=True)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--seed", default="keywave-v1")
    create.add_argument("--no-video", action="store_true")
    create.add_argument("--chart-offset-ms", type=int, default=0)
    create.add_argument("--video-offset-ms", type=int, default=0)
    game_create = subparsers.add_parser(
        "game-create",
        help="Create a level for KeyWave Game through the machine-readable protocol",
    )
    game_create.add_argument("--protocol-version", required=True, type=int)
    game_create.add_argument("--local", required=True, type=Path)
    game_create.add_argument("--output-directory", required=True, type=Path)
    game_create.add_argument("--cancel-file", type=Path)
    game_create.add_argument("--events-file", type=Path)
    game_create.add_argument("--seed", default="keywave-v1")
    game_create.add_argument("--no-video", action="store_true")
    subparsers.add_parser("gui", help="Launch the graphical Creator")
    subparsers.add_parser("self-test", help="Run the packaged application smoke test")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        try:
            package = PackageReader().read(args.package)
        except ContractError as exc:
            print(json.dumps({"ok": False, "code": exc.issue.code, "message": exc.issue.message}))
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "packageId": package.manifest.package_id,
                    "title": package.manifest.title,
                    "charts": [chart.difficulty for chart in package.charts],
                }
            )
        )
        return 0
    if args.command == "game-create":
        return _run_game_create(args)
    if args.command == "create":
        from .infrastructure.bootstrap import create_default_service

        source_kind = SourceKind.LOCAL if args.local else SourceKind.YOUTUBE
        source_value = args.local or args.youtube

        def report(update: ProgressUpdate) -> None:
            print(
                json.dumps(
                    {
                        "stage": update.stage.value,
                        "progress": update.fraction,
                        "message": update.message,
                    }
                ),
                file=sys.stderr,
            )

        try:
            result = create_default_service().create(
                CreateLevelRequest(
                    source_kind=source_kind,
                    source=source_value,
                    title=args.title,
                    artist=args.artist,
                    destination=args.output,
                    seed=args.seed,
                    include_video=not args.no_video,
                    chart_offset_ms=args.chart_offset_ms,
                    video_offset_ms=args.video_offset_ms,
                ),
                progress=report,
            )
        except CreatorError as error:
            print(json.dumps({"ok": False, "code": error.code, "message": error.public_message}))
            return 1
        print(
            json.dumps(
                {
                    "ok": True,
                    "packageId": result.package_id,
                    "output": str(result.destination),
                    "noteCounts": result.note_counts,
                    "bpm": result.bpm,
                    "confidence": result.confidence,
                }
            )
        )
        return 0
    if args.command == "self-test":
        print(json.dumps({"ok": True, "version": __version__}))
        return 0
    if args.command in {None, "gui"}:
        from .presentation import run_gui

        return run_gui()
    build_parser().print_help()
    return 0


def _run_game_create(args: argparse.Namespace) -> int:
    """Run the stable game-to-Creator protocol using newline-delimited JSON."""
    with _GameProtocolWriter(args.events_file) as protocol:
        if args.protocol_version != GAME_CREATION_PROTOCOL_VERSION:
            protocol.emit_result(
                ok=False,
                code="unsupported_protocol",
                message="The installed KeyWave Creator is not compatible with this game version.",
            )
            return 2

        from .infrastructure.bootstrap import create_default_service

        title, artist = infer_local_metadata(args.local)
        cancellation = CancellationToken()
        stop_watcher = Event()
        watcher = _start_cancellation_watcher(args.cancel_file, cancellation, stop_watcher)

        def report(update: ProgressUpdate) -> None:
            protocol.emit(
                {
                    "type": "progress",
                    "stage": update.stage.value,
                    "progress": update.fraction,
                    "message": update.message,
                }
            )

        try:
            result = create_default_service().create(
                CreateLevelRequest(
                    source_kind=SourceKind.LOCAL,
                    source=str(args.local),
                    title=title,
                    artist=artist,
                    output_directory=args.output_directory,
                    seed=args.seed,
                    include_video=not args.no_video,
                ),
                progress=report,
                cancellation=cancellation,
            )
        except CreatorError as error:
            protocol.emit_result(
                ok=False,
                code=str(error.code),
                message=error.public_message,
            )
            return 1
        finally:
            stop_watcher.set()
            if watcher is not None:
                watcher.join(timeout=1.0)

        protocol.emit_result(
            ok=True,
            packageId=result.package_id,
            output=str(result.destination),
            title=result.title,
            artist=result.artist,
        )
        return 0


class _GameProtocolWriter:
    """Write protocol records to stdout or a GUI-safe event file."""

    def __init__(self, events_file: Path | None) -> None:
        self._events_file = events_file
        self._stream: TextIO | None = None

    def __enter__(self) -> _GameProtocolWriter:
        if self._events_file is not None:
            path = self._events_file.expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("w", encoding="utf-8", newline="\n")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stream is not None:
            self._stream.close()

    def emit_result(self, *, ok: bool, **payload: object) -> None:
        self.emit({"type": "result", "ok": ok, **payload})

    def emit(self, payload: dict[str, object]) -> None:
        serialized = json.dumps(payload, ensure_ascii=True) + "\n"
        if self._stream is None:
            sys.stdout.write(serialized)
            sys.stdout.flush()
            return

        self._stream.write(serialized)
        self._stream.flush()


def _start_cancellation_watcher(
    cancel_file: Path | None,
    cancellation: CancellationToken,
    stop_event: Event,
) -> Thread | None:
    if cancel_file is None:
        return None

    marker = cancel_file.expanduser().resolve()

    def watch() -> None:
        while not stop_event.wait(0.1):
            try:
                if marker.is_file():
                    cancellation.cancel()
                    return
            except OSError:
                continue

    watcher = Thread(target=watch, name="keywave-game-cancellation", daemon=True)
    watcher.start()
    return watcher


if __name__ == "__main__":
    sys.exit(main())
