# Development

Use Python 3.12 and install the locked core/dev environment with `uv sync --extra dev`. Use the
Corepack-pinned pnpm version and `pnpm install --frozen-lockfile` for the web workspace.

Schema changes require a new Alembic revision and validation against a new SQLite file, including
downgrade/upgrade and `alembic check`. Application startup never creates tables.

Generated data belongs under `FTVM_DATA_DIR` and is excluded from version control. Tests may use
`Base.metadata.create_all()` only for isolated fixtures. Production-like paths and smoke runs use
Alembic.

Before a commit, run every command in `AGENTS.md`. Add a focused regression test for each escaped
failure and rerun the smoke pipeline for media/worker changes. At every coherent phase boundary,
update `docs/current-status.md` and relevant ADRs before committing. Preserve the nested `skills`
repository and `skills.7z` as user-owned material.
