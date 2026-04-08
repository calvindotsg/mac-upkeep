# Contributing

## Development Setup

> **Note:** macOS is required for development and testing.

```bash
git clone https://github.com/calvindotsg/mac-upkeep.git
cd mac-upkeep
uv sync
```

## Code Style

```bash
uv run ruff check src/ tests/     # Lint
uv run ruff format src/ tests/    # Format
```

## Testing

```bash
uv run pytest                     # Run tests
uv run pytest --cov               # With coverage
```

## Dependencies

After modifying `pyproject.toml` dependencies, run `uv lock` to update the lockfile. CI runs `uv lock --check` and will fail on stale lockfiles.

## Commit Conventions

This project uses [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).

| Type | When |
|------|------|
| `feat` | New task, CLI subcommand, or user-facing behavior |
| `fix` | Bug fix (wrong exit code, broken task, etc.) |
| `docs` | README, CLAUDE.md, CONTRIBUTING.md changes |
| `chore` | Dependencies, config files |
| `ci` | GitHub Actions workflows |
| `test` | Test additions or fixes |
| `refactor` | Code restructuring without behavior change |

Examples:

```
feat: add docker system prune task
fix: handle missing gcloud gracefully
docs: update README with new task table
chore: update typer to 0.13
ci: add macOS runner to test matrix
test: add test for mo purge failure path
```

## Release Process

Automated via [release-please](https://github.com/googleapis/release-please). Use conventional commits — `feat:` and `fix:` trigger version bumps. See CLAUDE.md for full flow.
