# Changelog

## 0.18.0 - 2026-08-14

- Extract KeyWave Creator from the original monorepo into its own independently
  versioned repository, build, installer, and CI pipeline.
- Preserve the complete local-media, YouTube video/playlist, Spotify metadata,
  deterministic chart-generation, `.keywave` contract, and `game-create` CLI
  behavior from the monorepo release.
- Keep the Creator update-stable settings and output-library behavior unchanged
  so installing the standalone companion does not lose user configuration.
- Align generated packages with AutoSync v2 by analyzing and encoding from one
  canonical audio timeline, measuring playable-audio offset, and selecting the
  highest-scoring deterministic chart candidate automatically.

Earlier Creator history remains available in this repository's Git history.
