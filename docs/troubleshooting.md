# Troubleshooting

## API health fails

Run `uv run alembic upgrade head`, confirm `FTVM_DATABASE_URL`, and request
`http://127.0.0.1:8000/api/v1/health`. Startup intentionally does not create missing tables.

## Worker does not claim a job

Worker assignment must exactly match `Job.gpu_assignment`: `cpu`, `gpu0`, or `gpu1`. GPU workers do
not steal CPU work. Inspect `/api/v1/jobs` for state and assignment.

## FFmpeg render fails

Confirm `ffmpeg` and `ffprobe` are on `PATH` (or set their environment paths), then run
`uv run flipthis-smoke`. Partial files remain under the unique run directory for diagnosis; completed
assets are never overwritten.

## Browser shows proxy errors

Vite proxies `/api` to `127.0.0.1:8000`. Start the API before the web dev server or expect temporary
proxy errors during an intentional API restart.

## No GPU is reported

GPU discovery returns an empty list when `nvidia-smi` is absent or fails. The mock milestone is fully
CPU-only. A configured GPU worker can start without proving CUDA/model execution.
