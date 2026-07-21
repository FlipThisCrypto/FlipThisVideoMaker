# Linux installation

Install Python 3.12, `uv`, Node 22 with Corepack, FFmpeg/ffprobe, and optionally NVIDIA drivers. Then:

```bash
./scripts/setup_linux.sh
./scripts/doctor.sh
./scripts/start.sh
```

`start.sh` binds development services to localhost and starts a CPU worker. Use
`./scripts/start_workers.sh all` only on a host where GPU 0 and GPU 1 are independently usable.
Stop PID-managed development processes with `./scripts/stop.sh`. Templates in `scripts/systemd/`
use `/opt/FlipThisVideoMaker`; copy and edit them before enabling user or system services.

The setup is rerunnable and never downloads models. Put generated data on NVMe by changing
`FTVM_DATA_DIR`; model/cache paths and external endpoints belong in `.env` and `config/providers.yaml`.

## Local Wan2.2 first/last-frame runtime

The optional installer pins ComfyUI v0.9.2, verifies its exact Git commit, downloads only the four
official Wan2.2 I2V-A14B FP8 workflow files, and verifies their SHA-256 checksums. Supply an absolute
external path with at least 45 GB free; neither runtime nor weights enter Git:

```bash
./scripts/install-wan22-flf.sh /srv/flipthis/providers/wan22
./scripts/run-wan22-flf.sh /srv/flipthis/providers/wan22 gpu1 8189
```

The launcher binds loopback only, exposes exactly one physical GPU, and uses maximum model offload.
Configure a second endpoint for GPU 0 rather than making one process see both cards. A runtime is
healthy only after its live node/model/device probe passes. Boundary uploads remain in the external
ComfyUI input directory and must follow the workstation's private-media retention policy.

## Practical-RIFE 4.25 delivery runtime

Practical-RIFE requires Python 3.11 or earlier. The installer pins the upstream Git commit, Python
packages, official 4.25 archive, and all four model-file checksums outside Git:

```bash
./scripts/install-practical-rife.sh /srv/flipthis/providers/practical-rife
```

Copy the three paths printed by the installer into the `rife-local` entry in
`config/providers.yaml`, then set that entry to `enabled: true`. The worker's
`CUDA_VISIBLE_DEVICES` mapping determines the one physical GPU used; the adapter does not invent a
model-specific device flag. Live health verifies Python imports, CUDA availability, and exact model
checksums rather than treating configured paths as proof of readiness.

## Local LPIPS perceptual QA runtime

LPIPS runs on CPU in a separate Python 3.11 environment. The installer pins all Python packages,
prefetches the official AlexNet-backed LPIPS 0.1 model, and checksums both weight files:

```bash
./scripts/install-lpips.sh /srv/flipthis/providers/lpips
```

Copy the printed paths into `lpips-local` in `config/providers.yaml`, enable that provider, and set
`FTVM_PERCEPTUAL_METRIC_PROVIDER_ID=lpips-local`. Health is not ready until the real model loads.
No image leaves the workstation. When selected, every chain QA report records start/end distance and
provider/model provenance. Lower LPIPS distance means greater perceptual similarity; version 1
records it diagnostically and does not invent an uncalibrated universal pass threshold.
