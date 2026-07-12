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
