# KeyWave Creator

The Creator reads authorized local media or downloads one authorized public
YouTube video, normalizes it, extracts bounded streaming audio features, derives
four deterministic nested charts, and emits a validated `.keywave` package.

The package contract under `contract/` is canonical for both Python and Unity.

Run the GUI:

```powershell
.venv\Scripts\keywave-creator.exe gui
```

The graphical flow needs only a public YouTube video URL. The Creator reads the
video title and artist, creates a safe `.keywave` filename, and saves it under
`Music\KeyWave Levels` by default. Choosing another output folder is optional;
that preference is persisted for future launches. Shared single-video URLs may
contain playlist context, which is ignored rather than downloaded.

Create from a local file:

```powershell
.venv\Scripts\keywave-creator.exe create `
  --local C:\Music\authorized.mp4 `
  --title "Example" `
  --artist "Artist" `
  --output C:\Music\example.keywave
```

Validate a package:

```powershell
.venv\Scripts\keywave-creator.exe validate C:\Music\example.keywave
```

Generation is deterministic for the same normalized media, metadata, offsets,
algorithm version, and seed. FFmpeg/ffprobe are process-isolated with bounded
diagnostics; yt-dlp runs with updates, config, playlists, cookies, and access
bypass disabled.
