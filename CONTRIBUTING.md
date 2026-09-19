# Contributing

Thanks for taking an interest in this project. Issues and pull requests are
welcome — **in English or Vietnamese**, whichever you are comfortable with.

---

## Before you start

The server drives Windows-only tooling (`decap.exe`) and reads Autodesk's ReCap
project format, so meaningful development on those layers needs Windows. The
point-cloud analysis layer is plain Python and portable; `tests/test_repo_layout.py`
and `tests/test_tool_contracts.py` run everywhere.

You do **not** need ReCap installed to work on this repository. Tests that need it
skip themselves.

---

## Setting up

```powershell
git clone https://github.com/xuantinhnbs-rgb/recap-mcp.git
cd recap-mcp

pip install -r requirements.txt
pip install -r requirements-dev.txt

python install.py --check     # server loads, tool count printed
```

---

## Before opening a pull request

Run the same three checks CI runs:

```powershell
ruff check .                  # lint (config in pyproject.toml)
pytest                        # the suite
python install.py --check     # loads the server, counts its tools
```

All three must pass.

---

## Code conventions

- **Comments and docstrings are written in Vietnamese.** Keep it that way in
  files that already use it; a bilingual codebase where each file is internally
  consistent is easier to read than one where every file is half and half.
- **Anything printed to a console stays ASCII.** `install.py` says why in a
  comment: the default Windows console is cp1252, and one accented character in a
  status line kills the diagnostic script at the moment someone needs it most.
  Docstrings and comments are unaffected — they never reach `print`.
- **Every tool returns a dict with an `ok` key** and is wrapped in `@safe`, so an
  exception becomes `{"ok": false, "error": "..."}` rather than a traceback the
  model cannot act on.
- **A tool's docstring is its description in the MCP schema.** It is what the
  model reads to decide whether to call the tool, so write it for that reader.
- `ruff` settings live in `pyproject.toml`. `UP` (pyupgrade) is deliberately off —
  tool signatures use `Optional[...]` / `Dict[...]`, and those annotations
  generate the JSON schema the model sees.

---

## Adding a tool — checklist

1. Write the function in the right module (`rcp.py`, `decap.py`, `pointcloud.py`)
   and keep the MCP wrapper in `server.py` thin.
2. Decorate with `@mcp.tool()` and `@safe`, in that order.
3. Return a dict. Include the evidence for any claim it makes — a count that was
   measured, not a count that was requested.
4. **Add it to the tool table in both `README.md` and `README.vi.md`.**
   `test_tool_contracts.py` fails if either is missing it, and the tool-count
   heading is checked against the source, so the numbers cannot drift.
5. Add a test whose expected value you can compute by hand.

---

## Testing conventions

**Every fixture has an analytic answer.** Not "the output looks reasonable", not
a reference value copied from a previous run — a number derivable on paper. A
plane tilted 30°, a wall leaning by a known gradient, a 3-4-5 triangle. When a
regression appears, this is the difference between knowing something changed and
knowing what the right answer was.

**Test files must not run anything at import time.** pytest imports a test module
during collection: module-level work runs there, with its output swallowed, and
contributes zero tests while the suite still reports green. Put shared setup in a
fixture and every assertion inside a `test_*` function. If you are unsure whether
a file is contributing, run `pytest --collect-only` and count.

**Claims in the documentation are tested where they can be.** The tool counts,
the tool lists, the absence of machine-specific paths in the docs — those are all
assertions in `tests/test_repo_layout.py` and `tests/test_tool_contracts.py`,
because a number repeated across documents is not corroborated by being repeated.

---

## Screenshots

If you change something a screenshot in `docs/images/` shows, regenerate it and
update its entry in [docs/images/README.md](docs/images/README.md). Read the
rules at the bottom of that file first — particularly the one about checking what
the capture discloses before it is committed. An image that has been committed
lives in git history permanently; deleting the file in a later commit does not
remove it.
