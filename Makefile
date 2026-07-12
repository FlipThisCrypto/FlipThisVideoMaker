.PHONY: sync migrate lint format-check typecheck test smoke web-install web-lint web-test web-build validate

sync:
	uv sync --extra dev

migrate:
	uv run alembic upgrade head

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy src

test:
	uv run pytest

smoke:
	uv run flipthis-smoke

web-install:
	corepack enable
	pnpm install --frozen-lockfile

web-lint:
	pnpm lint

web-test:
	pnpm test

web-build:
	pnpm build

validate: sync migrate lint format-check typecheck test smoke web-install web-lint web-test web-build
