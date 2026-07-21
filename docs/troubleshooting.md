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

The API derives online status from `last_heartbeat_at` and `FTVM_WORKER_STALE_SECONDS`. Production
claims also have a renewable `FTVM_JOB_LEASE_SECONDS` ownership lease. A worker reconciles an expired
running lease to a failed `orphaned_worker_lease`; it never automatically requeues it because a stale
row is not proof that an external provider process stopped. Inspect the worker/provider process and
job log, stop any orphan process, then use **Acknowledge orphan risk and retry**. Late writes from the
expired worker generation are rejected.

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

## Generative provider is unavailable

Discovery is not health. Enable exactly one `ltx` or `luma` entry in `config/providers.yaml`, set its
named environment credential, restart the API, and inspect `/api/v1/providers/health`. LTX requires
the 1080p `final` profile. A missing/invalid credential, disabled provider, or failed authenticated
probe deliberately prevents enqueue. Never paste a key into YAML, the UI, a Job payload, or logs.

## RIFE or LatentSync is unavailable

Both are external administrator-managed environments. Verify every configured path exists and that
the model/checkpoint directory is populated. RIFE must use the official `inference_video.py` and
4.25 model directory. LatentSync requires its repository, Python runtime, 1.5 UNet config/checkpoint,
and official SyncNet checkpoint. The adapters inherit the worker's `CUDA_VISIBLE_DEVICES`; do not
combine two 12 GB cards or add undocumented device flags.

## A chain clip is degraded

Open its immutable QA/provenance details. A clip is degraded for any failed duration, CFR, 600-frame,
start/end boundary, last-frame snap, duplicate-freeze, audio-presence, or lip-sync check. Degraded
clips cannot be accepted. Regenerate with stronger provider-native boundary conditioning or a better
prompt/target. Do not replace frame 599 or add a crossfade to force a pass.

## Hosted cancellation did not stop billing

The reviewed LTX V2 and Luma Agents contracts do not document a server-side cancellation endpoint.
Cancellation stops local polling/download, records the provider Job ID, and prevents publication, but
the hosted job may continue. Check the provider console before retrying to avoid duplicate spend.
