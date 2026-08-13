# KeyWave package contract 1.x

`.keywave` is a ZIP container whose paths and JSON are canonical and validated
before any media is exposed to the game.

```text
manifest.json
audio/song.ogg
video/background.webm      optional, VP8 (current writer)
video/background.mp4       optional, H.264 (legacy reader support)
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
in each higher difficulty. Current writers use four lanes for Easy, five for
Medium, and all six for Hard and Extreme. Readers continue to accept the legacy
five-lane Hard profile emitted before chart algorithm 1.3.0.

Positive `chartOffsetMs` delays chart events relative to audio. Positive
`videoOffsetMs` delays video relative to audio. Python converts to milliseconds
only at the contract boundary; Unity uses checked integer microseconds for
gameplay and DSP seconds only in its clock adapter.

Readers support compatible `1.x.y` versions but reject unknown required features.
Writers currently emit `1.0.0`, `holdNotes`, and `vp8Video` when video is present.
`vp8Video` gates the VP8-in-WebM media descriptor so older readers fail closed;
new readers continue to accept legacy H.264-in-MP4 packages. The JSON Schemas in
`schemas/1.0/` define the wire shape; typed models and cross-runtime fixtures add
semantic invariants that JSON Schema alone cannot express.

Readers treat the container as hostile: paths, duplicates/case collisions,
symlinks, entry count, compressed and expanded sizes, compression ratios, JSON
depth/UTF-8, exact members, versions/features, descriptors, hashes, media types,
chart ordering, bounds, lane profiles, and difficulty nesting are checked before
cache publication.
