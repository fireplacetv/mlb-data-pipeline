# MLB Data Pipeline — How to Work Here

This is a guide for Claude Code and future developers on building and maintaining this project.

## Source of Truth

**Read `ARCHITECTURE.md` first.** It is the current, agreed specification for the entire pipeline. Build one milestone at a time, in order. Each milestone has acceptance checks; all must pass before moving on.

If `ARCHITECTURE.md` and a library's current docs disagree on API details, **follow the library docs and update `ARCHITECTURE.md`** to reflect what you found.

## Coding Conventions

### Python

- **Type hints on all functions.** Python 3.12+.
- **Small functions, one job each.** Configuration comes from `config.py` (which reads env vars), never scattered `os.environ` calls throughout the code.
- Use the `logging` module, never `print()`.
- Fail loudly with clear messages. Don't catch and continue except for per-day isolation in pipelines (§6.3 / §6.4 in ARCHITECTURE.md).

### Tests

- New code comes with `pytest` tests in the same change.
- Tests never touch the network; use recorded fixtures in `tests/fixtures/`.

### SQL (dbt)

- Lowercase keywords and identifiers everywhere.
- One CTE per logical step.
- Every DuckDB-specific line gets a one-line comment explaining why (§8 in ARCHITECTURE.md).
- Every model has a description and tests in its YAML.

### Logging

- Every pipeline run logs:
  - The date window it loaded.
  - Row counts per resource.
  - Schema changes (new tables, new columns, variant columns).
  - Duration per step.
- Log to `data/logs/` and stdout, using the `logging` module.

### Scope

- **Don't add features, abstractions, or dependencies beyond the current milestone.**
- If the spec is ambiguous or wrong, propose an edit to `ARCHITECTURE.md` rather than guessing.

### Documentation

- Update `README.md` and `docs/` in the same change as the code.
- Docs are Markdown, live in the repo, and change in the same PR as the code they describe.

## Building a Milestone

1. Read the milestone spec in `ARCHITECTURE.md` (§10).
2. Implement the changes.
3. Ensure all acceptance checks pass.
4. Update `README.md` and `docs/` for anything new.
5. When the checks pass, move to the next milestone.

## Key Files

- **`ARCHITECTURE.md`:** The spec. Read it.
- **`README.md`:** Overview and Quick Start.
- **`docs/setup.md`:** Environment setup for a new user.
- **`docs/usage.md`:** Command reference.
- **`docs/configuration.md`:** Every environment variable and config option.
- **`docs/roadmap/`:** Design docs for future development phases.
- **`config.py`:** Reads env vars, exposes paths and settings.
- **`.dlt/config.toml`:** Non-secret dlt settings (checked in).
- **`.dlt/secrets.toml`:** Secrets like API keys (gitignored, empty today).

## Running Commands

All commands run through `docker compose`. Examples:

```bash
docker compose build                           # Build the image
docker compose run --rm pipeline python -m mlb.pipelines.statcast   # Ingest Statcast
docker compose run --rm dbt build               # Build dbt staging layer
docker compose run --rm pipeline pytest         # Run tests
docker compose run --rm pipeline ruff check .   # Lint
```

See `README.md` Quick Start for the full list.
