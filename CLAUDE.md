# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Models
- Default to **Sonnet** for all code work.
- Use **Opus** for coordination, architecture, high-level design, and conceptual planning.
- Use **Haiku** for simple lookups, reading files, and lightweight tasks.
- Use subagents for all non-trivial code work.

## Git Workflow

1. **Never make code changes directly on `main`.** Check the current branch first; propose a branch name and confirm before creating it.
2. Commit in logical units with clear, descriptive messages.
3. When work is complete, ask the user if they want to open a pull request.
4. Never force-push without explicit user confirmation. Always confirm before destructive git operations.


`CENSUS_API_KEY` can be set in the environment or a `.env` file (located via `find_dotenv(usecwd=True)`).

## Reference

- Package architecture, data flow, and module details: [ARCHITECTURE.md](ARCHITECTURE.md)
- Authoritative dev log: `reference/dev_notes.md` — append a dated entry for significant changes.
