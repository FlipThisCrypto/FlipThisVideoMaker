#!/usr/bin/env bash
set -euo pipefail

readonly LATENTSYNC_COMMIT="a229c3948406bc2cf6eaf4873e662e70c6a04746"
readonly WEIGHT_REVISION="32a20d29aead0498e3e885e90dbbe8027da1b61b"

if [[ $# -ne 1 || "$1" != /* ]]; then
  echo "usage: $0 /absolute/external/runtime/root" >&2
  exit 2
fi

runtime_root="${1%/}"
mkdir -p "$runtime_root"
if [[ ! -d "$runtime_root/.git" ]]; then
  git clone --no-checkout https://github.com/bytedance/LatentSync.git "$runtime_root"
  git -C "$runtime_root" checkout --detach "$LATENTSYNC_COMMIT"
fi
actual_commit="$(git -C "$runtime_root" rev-parse HEAD)"
if [[ "$actual_commit" != "$LATENTSYNC_COMMIT" ]]; then
  echo "refusing unpinned LatentSync checkout: expected $LATENTSYNC_COMMIT, found $actual_commit" >&2
  exit 1
fi

uv venv --python 3.10 "$runtime_root/.venv"
python_bin="$runtime_root/.venv/bin/python"
uv pip install --python "$python_bin" -r "$runtime_root/requirements.txt"

huggingface_cli="$runtime_root/.venv/bin/huggingface-cli"
"$huggingface_cli" download ByteDance/LatentSync-1.5 \
  latentsync_unet.pt \
  whisper/tiny.pt \
  auxiliary/syncnet_v2.model \
  auxiliary/sfd_face.pth \
  --revision "$WEIGHT_REVISION" \
  --local-dir "$runtime_root/checkpoints"

declare -A expected=(
  ["latentsync_unet.pt"]="6440b49a7ccceff56cdc001f5f17605216337f5bbd66fa360139768926e23f51"
  ["whisper/tiny.pt"]="65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9"
  ["auxiliary/syncnet_v2.model"]="961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442"
  ["auxiliary/sfd_face.pth"]="d54a87c2b7543b64729c9a25eafd188da15fd3f6e02f0ecec76ae1b30d86c491"
)
for filename in "${!expected[@]}"; do
  actual_sha="$(sha256sum "$runtime_root/checkpoints/$filename" | awk '{print $1}')"
  if [[ "$actual_sha" != "${expected[$filename]}" ]]; then
    echo "LatentSync checkpoint mismatch: $filename" >&2
    exit 1
  fi
done

(
  cd "$runtime_root"
  CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" "$python_bin" - <<'PY'
from latentsync.utils.face_detector import FaceDetector

FaceDetector("cuda")
PY
)

declare -A insightface_expected=(
  ["det_10g.onnx"]="5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"
  ["2d106det.onnx"]="f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf"
)
insightface_root="$runtime_root/checkpoints/auxiliary/models/buffalo_l"
for filename in "${!insightface_expected[@]}"; do
  actual_sha="$(sha256sum "$insightface_root/$filename" | awk '{print $1}')"
  if [[ "$actual_sha" != "${insightface_expected[$filename]}" ]]; then
    echo "LatentSync InsightFace model mismatch: $filename" >&2
    exit 1
  fi
done

CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" "$python_bin" -c \
  'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'

echo "LatentSync 1.5 runtime verified at $runtime_root"
echo "Code license: Apache-2.0; official model weights: OpenRAIL++"
