# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-09-19

First public release. The server was developed inside a larger working
repository alongside several other MCP servers; this is the point at which it
became a repository of its own.

### Added

- **25 MCP tools** across three layers with different capabilities and limits:
  reading `.rcp` projects, headless processing through `decap.exe`, and
  quantitative analysis of LAS/LAZ point clouds. The layer table in the README
  states what each one can and cannot do.
- `install.py` — detects this machine's Python interpreter and repository path,
  verifies dependencies, loads the server to confirm its tools register, reports
  where ReCap was found, and writes `.mcp.json`. `--check` verifies without
  writing; `--claude-desktop` also writes Claude Desktop's config.
- Bilingual documentation: [README.md](README.md) (English) and
  [README.vi.md](README.vi.md) (Vietnamese).
- Proof images in [docs/images/](docs/images/), each with the exact command
  sequence that produced it recorded in `docs/images/README.md`.
- CI on Windows across Python 3.10–3.13, plus `ruff` on Linux. ReCap is **not**
  installed on the runner, which is deliberate: the suite has to pass without it.
- Community files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue
  and pull-request templates, Dependabot.
- `tests/test_tool_contracts.py` — derives the tool count from the source with an
  AST pass and requires the loaded server, both READMEs and that count to agree.
  A number repeated across documents is not corroborated by being repeated, so
  the documented figure is now checked by something that runs.
- `tests/test_repo_layout.py` — checks what someone else receives on clone:
  valid config example, `install.py` pointing at a script that exists, every
  documented image present and every present image documented, and no
  machine-specific absolute paths left in the public text.

### Fixed

- **`tests/test_mcp.py` contributed zero tests while appearing to pass.** It was
  written as a top-level script, so pytest ran the whole thing during collection
  with its output swallowed, counted no tests, and would have aborted the entire
  session with `sys.exit(1)` on any machine without ReCap. Rewritten as ordinary
  pytest functions with a fixture holding the server subprocess, and the parts
  that need a ReCap install now skip rather than fail.
- **Two protocol tests did not skip on a machine without ReCap**, so the first CI
  run on a clean Windows runner failed. The skip conditions had been guessed from a
  proxy — the presence of ReCap's sample project — and two tests that needed the
  installation itself were never marked at all. They now take their precondition
  from the server's own `check_recap_installation` response, so the test and the
  thing it tests cannot disagree. The path where ReCap is *absent* gained a test of
  its own: that branch only ever executes on CI, so nothing else would have checked
  that its error message is useful.
- **`install.py` could die on the console it exists to diagnose.** Status lines
  containing accented characters raised `UnicodeEncodeError` on the default
  Windows cp1252 console, ending the diagnostic run partway through with a
  traceback about codecs. Console output is now ASCII by convention, with the
  print helper catching the error as a second line of defence.
- **`scripts/capture-window.ps1` picked a window when several matched**, printing
  a warning and continuing. That is how a capture of an unrelated session — one
  holding real project data and a signed-in account name — reached a PNG that
  looked perfectly fine. Multiple matches are now an error listing the candidates
  with their PIDs; `-ProcessId` selects one unambiguously and `-Force` restores
  the old behaviour deliberately.

### Documentation

- Removed the absolute paths of the author's machine from the install
  instructions; `install.py` generates them now.
- Rewrote the setup section, which assumed a configuration file belonging to the
  parent repository.

[Unreleased]: https://github.com/xuantinhnbs-rgb/recap-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/xuantinhnbs-rgb/recap-mcp/releases/tag/v1.0.0
