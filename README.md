# KeyWave Creator

The Creator reads authorized local media or queues authorized public YouTube
videos and playlists. A Spotify playlist can identify its tracks, which KeyWave
then conservatively matches to public YouTube music videos before using the same
normalization and generation pipeline. Spotify audio is never downloaded.

The package contract under `contract/` is canonical for both Python and Unity.

Run the GUI:

```powershell
.venv\Scripts\keywave-creator.exe gui
```

The graphical flow accepts public YouTube video/playlist URLs and Spotify
playlist URLs at any time. Playlist discovery runs separately while the bounded
generation queue processes up to two videos concurrently and keeps later entries
waiting, with independent progress, cancellation, failure, and result actions.
Lists are limited to 200 items. Each YouTube result uses the normal title,
artist, collision-safe filename, and `Music\KeyWave Levels` output behavior.

Spotify setup uses the official Authorization Code flow with PKCE:

1. Create a Spotify developer app and copy its Client ID into Creator.
2. Register `http://127.0.0.1:9657/callback` as its redirect URI.
3. Add a Spotify playlist URL and approve the browser sign-in.

The Client ID is remembered in the update-stable Creator settings file. Access
tokens remain in memory only. Port `9657` must be available while Spotify sign-in
is in progress. Under Spotify's current Web API policy, playlist
items are available only for playlists the signed-in user owns or collaborates
on. Tracks without a confident YouTube match are skipped and reported; cover,
karaoke, reaction, slowed, sped-up, nightcore, and unrelated remix results are
penalized unless that modifier is part of the Spotify title.

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
diagnostics; yt-dlp runs with updates, user config, cookies, and access bypass
disabled. Playlist discovery is bounded and never turns on credential import.
