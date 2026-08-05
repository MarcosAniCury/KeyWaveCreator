# KeyWave package contract 1.x

`.keywave` is a ZIP container whose paths and JSON are canonical and validated
before any media is exposed to the game.

```text
manifest.json
audio/song.ogg
video/background.mp4       optional
cover/cover.jpg            optional
charts/easy.json
charts/medium.json
charts/hard.json
charts/extreme.json
```

`manifest.json` declares every member's lowercase path, media type, byte size,
and SHA-256. No undeclared member is permitted. Charts use integer milliseconds
on disk, six global lanes, stable note IDs, sorted notes, taps or positive-length
holds, and no same-lane overlap. Every lower-difficulty note is preserved exactly
in each higher difficulty.

Positive `chartOffsetMs` delays chart events relative to audio. Positive
`videoOffsetMs` delays video relative to audio. Python converts to milliseconds
only at the contract boundary; Unity uses checked integer microseconds for
gameplay and DSP seconds only in its clock adapter.

Readers support compatible `1.x.y` versions but reject unknown required features.
Writers currently emit `1.0.0` and `holdNotes`. The JSON Schemas in
`schemas/1.0/` define the wire shape; typed models and cross-runtime fixtures add
semantic invariants that JSON Schema alone cannot express.

Readers treat the container as hostile: paths, duplicates/case collisions,
symlinks, entry count, compressed and expanded sizes, compression ratios, JSON
depth/UTF-8, exact members, versions/features, descriptors, hashes, media types,
chart ordering, bounds, lane profiles, and difficulty nesting are checked before
cache publication.
