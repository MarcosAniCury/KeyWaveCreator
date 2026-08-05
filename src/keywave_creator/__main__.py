"""KeyWave Creator command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .application.errors import CreatorError
from .application.models import CreateLevelRequest, ProgressUpdate, SourceKind
from .contract.errors import ContractError
from .contract.package import PackageReader


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


if __name__ == "__main__":
    sys.exit(main())
