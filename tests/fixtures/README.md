# Real fixtures (local only)

This folder holds pages saved from the stores' websites. They are **not** in git: the repository
is public and the sites' content may not be redistributed (Eurospin's terms forbid it explicitly).

- Tests that need them are marked `real_fixtures` and are skipped when the files are missing.
- `uv run pisaprices fixtures capture` (task T1.10) downloads them again, politely, into this folder.
- CI runs on `tests/synthetic/`: hand-written pages with the same structure and invented products.
