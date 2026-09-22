# Changelog

## 0.19.1 - 2026-09-22

- Update the bundled YouTube downloader to restore downloads affected by HTTP
  403 errors, keeping the Windows, Linux and Python dependency versions aligned.

- Keep the application version aligned with Game for the combined Windows and
  experimental Linux installers.

## 0.19.0 - 2026-08-15

- No user-facing changes in this release. The application version remains
  aligned with KeyWave Game for coordinated installers.

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
