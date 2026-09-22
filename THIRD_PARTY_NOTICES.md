# Third-party notices

KeyWave Creator releases include or depend on Qt/PySide6, Nuitka, yt-dlp,
yt-dlp-ejs, Deno, FFmpeg, NumPy, SciPy, SoundFile, jsonschema, platformdirs and
their transitive dependencies. The optional neural analysis extra uses Beat
This. Release automation must generate an exact version inventory and ship the
applicable license texts beside the binaries.

Build tooling downloads pinned yt-dlp, Deno, and FFmpeg release artifacts and
verifies their SHA-256 hashes before packaging. Those executables are not
committed to this repository.

The YouTube downloader is pinned to yt-dlp 2026.08.19 (Python package 2026.8.19).
Its release-specific third-party license inventory is available at
https://github.com/yt-dlp/yt-dlp/blob/2026.08.19/THIRD_PARTY_LICENSES.txt.
