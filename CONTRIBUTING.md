# Contributing

Read `AGENTS.md`, `docs/current-status.md`, and the ADRs before changing code. Keep the core free of
model dependencies and backend-specific payloads. Every schema change needs an Alembic migration;
every feature or escaped regression needs an automated assertion or smoke probe.

Run `make validate` before proposing a commit. Do not stage generated media, databases, credentials,
model weights, the nested `skills` repository, or `skills.7z`.
