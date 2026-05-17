# Contributing to morpc-census

## Setup

```bash
git clone https://github.com/jinskeep-morpc/morpc-census.git
pip install -e /path/to/morpc-census/[dev]
```

## Running tests

```bash
# Offline tests only (no Census API key required)
pytest

# Include live network tests (requires CENSUS_API_KEY in environment or .env)
pytest -m network
```

Tests are split by the `network` marker. The default `pytest` run (`-m 'not network'`) is safe to run locally without credentials.

## Making changes

1. Create a GitHub issue describing the problem and intended solution.
2. Create a branch (one branch per roadmap phase or per logical feature).
3. Make changes and commit in logical chunks.
4. Write tests for all changed behavior.
5. Append a dated entry to `reference/dev_notes.md` summarising the change.
6. Open a pull request and request review.

## Versioning

This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`.

| Increment | When |
|-----------|------|
| `PATCH`   | Backwards-compatible bug fixes |
| `MINOR`   | New backwards-compatible functionality |
| `MAJOR`   | Breaking changes to the public API |

**Breaking changes** include:
- Removing or renaming public functions, classes, or parameters
- Changing return types or shapes in a way callers must update for
- Dropping support for a previously supported Python version

Breaking changes must be noted in `CHANGELOG.md` under the relevant version entry before the release is tagged.

## Releasing

1. Update `CHANGELOG.md` — move items from `[Unreleased]` to a new `[X.Y.Z]` section.
2. Create and push a git tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. Create a GitHub release from the tag — this triggers the publish workflow.
