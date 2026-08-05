# KeyWave Creator Python Guide

## Scope

This file applies to `python/` and extends the repository guide. The supported
runtime is CPython 3.12 on Windows x64. `pyproject.toml` is the single source of
truth for build metadata, dependencies, pytest, Ruff, and mypy configuration.
Use the checked-in virtual environment only as local state; never commit it.

## Package structure and dependency direction

Keep production code under `src/keywave_creator/` and tests under `tests/`.
Evolve the package along these boundaries:

- `contract/`: canonical `.keywave` models, schema validation, compatibility,
  readers, writers, and stable contract errors.
- `domain/`: pure media-analysis and chart-generation concepts and algorithms.
- `application/`: use cases, progress/cancellation orchestration, and ports.
- `infrastructure/`: FFmpeg/ffprobe, yt-dlp, filesystem, archive, hashing, and
  platform adapters.
- `presentation/`: PySide6/QML models, controllers, and user-facing state.
- `__main__.py`: parsing/bootstrap and the composition root only.

Domain and contract modules MUST NOT import PySide6, subprocess, network clients,
or concrete filesystem adapters. Application code depends on protocols it owns;
infrastructure implements them. Presentation invokes application use cases and
maps results to view state. QML contains layout and presentation behavior, not
generation or package rules.

Avoid generic `utils.py`, `helpers.py`, or dumping-ground modules. Name a module
for the capability it owns. Keep `__init__.py` exports intentional and small.

## Language and style

- Follow PEP 8 and the repository Ruff configuration. Ruff formatting is the
  canonical formatting result; do not hand-format around it or add conflicting
  formatter rules.
- All production function signatures, attributes whose type is not obvious, and
  public constants MUST be typed. New production code passes mypy strict without
  broad ignores.
- `Any` is allowed only at an untyped boundary such as parsed JSON or Qt glue.
  Validate/narrow it immediately. Do not propagate `Any` through domain APIs.
- Prefer built-in generics (`list[str]`), `X | None`, `StrEnum`, `Protocol`,
  `dataclass(frozen=True, slots=True)`, and explicit type aliases where they make
  the model clearer.
- Use `snake_case` for modules/functions/variables, `PascalCase` for types,
  `UPPER_SNAKE_CASE` for constants, and a leading underscore for non-public
  members. Names include units and intent; avoid abbreviations other than stable
  domain terms such as BPM, FFT, SHA-256, or URL.
- Imports are absolute across package boundaries and relative only within a
  tightly cohesive package. Imports stay at module scope unless a documented
  cycle or optional dependency requires otherwise.
- Public modules and non-obvious public APIs have PEP 257 docstrings describing
  behavior, units, errors, side effects, and constraints. Do not duplicate the
  type signature in prose.
- Prefer guard clauses and small functions with one level of abstraction.
  Replace boolean-flag APIs with named strategies or enums when the flag changes
  the meaning of the operation.
- Never use mutable default arguments. Avoid module-level mutable state. Use
  `default_factory`, immutable tuples/mappings, or instance ownership.
- Catch the narrowest exception. Translate exceptions once at a boundary using
  `raise DomainSpecificError(...) from error`. Never use bare `except`, silent
  `pass`, or exceptions for normal branching.
- Use context managers for files, archives, locks, temporary directories, audio
  handles, and other resources. Use `pathlib.Path` for path logic.

## Domain modeling and determinism

- Wire-format time is integer milliseconds. Analysis may use NumPy floating
  point internally, but quantize once through a named, tested conversion before
  creating notes. Never compare floats for exact equality.
- Every generation run is identified by algorithm version, normalized options,
  and seed. Randomness flows through an injected generator; do not call global
  random state from algorithms.
- Sort explicitly with a documented canonical key. Do not rely on filesystem,
  dictionary, set, thread-completion, or archive traversal order.
- A chart-generation stage accepts immutable input and returns immutable output.
  It does not mutate a lower difficulty to create the next one. Construct higher
  charts so preservation of lower-level notes is mechanically testable.
- Separate feature extraction, beat/onset analysis, candidate generation,
  difficulty selection, lane assignment, hold inference, normalization, and
  validation. Each stage has explicit preconditions and postconditions.
- Validate finite numeric values and bounds at every external/scientific-library
  boundary. NaN and infinity never enter the contract.
- Avoid hidden caches in correctness paths. A cache key includes every input
  that can change the result, the algorithm version, and dependency-sensitive
  parameters.

## PySide6 and QML

- The GUI thread only updates QObject/QML state. Download, ffprobe, decode,
  analysis, hashing, and package writing MUST run off the GUI thread.
- Prefer a worker `QObject` moved to a `QThread` (or a deliberately bounded
  `QThreadPool`) and communicate through typed signals/slots. Respect QObject
  thread affinity; never call a GUI object from a worker thread.
- Workers expose progress, completion, structured failure, and cooperative
  cancellation. The owner connects cleanup before starting and guarantees
  `quit`, `wait`, and `deleteLater` behavior on every exit path.
- Do not create one unmanaged thread per task. Bound concurrency around CPU,
  disk, and external tools; scientific libraries may already use worker threads.
- QML-facing QObject properties have explicit `Property`, notify signals, and
  stable names. Use `QAbstractListModel` for changing collections instead of
  repeatedly replacing untyped Python lists.
- QML does not receive domain exceptions or arbitrary dictionaries. Controllers
  map typed application state to presentation DTOs and localized-ready English
  messages.
- Keep the event loop responsive: no polling or sleeps on the GUI thread. Use
  signals, timers, and process callbacks. UI state transitions are explicit
  (`idle`, `validating`, `acquiring`, `analyzing`, `packaging`, `complete`,
  `cancelled`, `failed`).
- UI tests assert controller/model behavior separately from expensive analysis.
  Add a launch smoke test for QML load errors and missing resources.

## Processes, media, and network boundaries

- Centralize each external tool behind one adapter. Resolve the reviewed binary
  path explicitly; do not depend on current-directory or ambiguous PATH lookup
  in production bundles.
- Invoke tools with an argument sequence and `shell=False`. Set an explicit
  working directory, text encoding when reading text, timeout, output limit,
  checked status, and cancellation/termination policy. Never concatenate user
  input into a command string.
- Drain stdout/stderr without deadlock. Parse progress separately from diagnostic
  output and retain a bounded tail for failure reports. Do not load unbounded
  process output into memory.
- Validate media with ffprobe before expensive work: actual container/streams,
  duration, dimensions, frame rate, codecs, and decode viability. Extensions and
  MIME labels are hints only.
- Normalize through FFmpeg to the one documented internal representation. Make
  encoding flags explicit and deterministic where the tool permits; surface
  unsupported codecs and corrupt media as stable application errors.
- yt-dlp handles only the authorized public-single-video flow. The bundled
  version is pinned, has no self-updater, does not import cookies or credentials,
  and uses bounded format selection. Store neither the source URL query string
  nor downloader secrets in normal logs or the package.
- Network operations have connect/read timeouts, cancellation, bounded retries
  only for transient idempotent failures, and no infinite retry. Never bypass
  TLS validation.

## Filesystem and `.keywave` safety

- Treat every input archive as hostile. Preflight all entries before reading
  payloads. Reject absolute/UNC/drive paths, backslashes, `.`/`..`, empty path
  segments, NULs, directories, links/reparse points, duplicates, case-folded
  collisions, unexpected members, excessive counts/sizes/ratios, and unsupported
  compression.
- Do not call `ZipFile.extractall()` or trust `zipfile.Path` sanitization for an
  imported package. Stream validated members to owned destinations or read JSON
  under hard limits.
- Compare resolved output paths against the resolved staging root. Archive names
  use forward slashes, lowercase stable names, and no user-derived paths.
- Stream hashing and large media copies in bounded chunks. Verify size and
  SHA-256 before publishing or consuming a member.
- Write into a unique `TemporaryDirectory`/staging directory on the destination
  filesystem, flush and close resources, validate the finished archive, then use
  an atomic replace. Preserve an existing valid output if any step fails.
- Cleanup is best-effort but observable. Cancellation removes owned partial
  files and never recursively deletes a path that was not created by the current
  operation.

## Errors and logging

- Define stable error codes by layer. Error messages are context, not a program
  interface; tests assert codes and structured fields before prose.
- Library/domain code uses `logging`, never `print`. The CLI may write its
  documented machine-readable result to stdout and diagnostics to stderr.
- Log operation identifiers, stage, tool version, elapsed time, and safe failure
  context. Do not log full authenticated URLs, environment dumps, cookies,
  tokens, or raw private paths in user-visible diagnostics.
- Preserve tracebacks in diagnostic logs while presenting concise recovery text
  to QML. A broad top-level handler is allowed only at a process/thread boundary
  and must record, translate, clean up, and choose an explicit exit state.

## Performance and scientific code

- Use NumPy/SciPy/librosa vector operations when they improve clarity and are
  measured to matter. Avoid accidental quadratic passes over long timelines.
- Bound arrays based on the 30-minute/1080p30 contract and release intermediates
  when a stage completes. Process video/audio in streams or chunks where full
  materialization is unnecessary.
- Do not add multiprocessing casually to a frozen Qt application. Define spawn,
  serialization, cancellation, packaging, and shutdown behavior first.
- Benchmark representative short and maximum-size synthetic inputs. Record
  algorithm version and environment with results. A microbenchmark does not
  replace an end-to-end memory/runtime measurement.

## Tests

- Unit tests cover pure stages and invariants with tiny deterministic data.
- Property tests cover ordering, lane bounds, nested difficulty preservation,
  time quantization, serialization round trips, and hostile archive paths.
- Contract tests validate schemas, hashes, canonical output, required features,
  version rejection, limits, and the Python-to-Unity synthetic fixture.
- Integration tests use the real FFmpeg/ffprobe adapter with generated media;
  unit tests use a fake port, not a fake subprocess module patched everywhere.
- Tests must not depend on the internet, a real YouTube video, wall-clock sleeps,
  locale, timezone, user profile, random global state, or execution order.
- Use `tmp_path`; never write test output into source fixtures. Assert cleanup and
  preservation of an existing destination on failure.
- A regression test names the behavior, not the implementation. Prefer plain
  asserts and fixtures with the narrowest useful scope.

## Required commands

Run from `python/` with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest
```

During iteration, target a test module or node. Before handoff, run all four
commands for production Python changes. If Windows Application Control blocks a
native mypy component, capture that exact diagnostic and do not call typing
verified.

For a dependency or build change, additionally build a wheel in an isolated
environment, install it, run the CLI help/validation smoke test, launch the QML
application from the packaged layout, and inspect bundled licenses/resources.

## Review rules

Flag Python changes that introduce untyped domain data, UI-thread work, global
randomness, unordered output, `shell=True`, unsafe archive extraction, unbounded
media/process reads, non-atomic publication, broad exception swallowing,
internet-dependent tests, or a production dependency without distribution and
license treatment.

