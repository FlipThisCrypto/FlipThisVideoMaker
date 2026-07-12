# Troubleshooting

## API health fails

Run `uv run alembic upgrade head`, confirm `FTVM_DATABASE_URL`, and request
`http://127.0.0.1:8000/api/v1/health`. Startup intentionally does not create missing tables.

## Worker does not claim a job

Worker assignment must exactly match `Job.gpu_assignment`. Logical worker IDs, assignments, and
physical GPU mappings come from `config/workers.yaml`; GPU workers do not steal CPU work. Inspect
`/api/v1/workers` for configured/runtime state and `/api/v1/jobs` for queue assignment.

A GPU worker locks its configured physical device and then checks `FTVM_MIN_FREE_VRAM_MB`. A missing
or failed `nvidia-smi` probe, a missing physical index, or insufficient free VRAM fails closed: the job
remains queued and its attempt count does not increase.

## Worker is stale after a crash

The API derives online status from `last_heartbeat_at` and `FTVM_WORKER_STALE_SECONDS`. A stale row is
not proof that an external provider process stopped, so the application does not automatically
requeue its current job. Inspect the worker/provider process and job log, stop any orphan process,
then use the explicit retry action when safe.

## FFmpeg render fails

Confirm `ffmpeg` and `ffprobe` are on `PATH` (or set their environment paths), then run
`uv run flipthis-smoke`. Partial files remain under the unique run directory for diagnosis; completed
assets are never overwritten.

## Browser shows proxy errors

Vite proxies `/api` to `127.0.0.1:8000`. Start the API before the web dev server or expect temporary
proxy errors during an intentional API restart.

## No GPU is reported

GPU discovery returns an empty list when `nvidia-smi` is absent or fails. The mock milestone is fully
CPU-only. A configured GPU worker can start for lifecycle diagnostics, but it will not claim GPU jobs
until its exact physical device passes admission. This does not prove CUDA or model execution.
