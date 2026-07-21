#!/usr/bin/env bash
set -euo pipefail

readonly RIFE_COMMIT="17d8c7a1005b37f4c97bfee04e316aaec7fdc536"
readonly MODEL_FILE_ID="1ZKjcbmt1hypiFprJPIKW0Tt0lr_2i7bg"
readonly MODEL_ARCHIVE_SHA="e63d481b7ae5d4a4e6ad7ac5b410ff78f3bf7be3b51b2e38ca8152747abde5b4"

if [[ $# -ne 1 || "$1" != /* ]]; then
  echo "usage: $0 /absolute/external/runtime/root" >&2
  exit 2
fi

runtime_root="${1%/}"
mkdir -p "$runtime_root"
if [[ ! -d "$runtime_root/.git" ]]; then
  git clone https://github.com/hzwer/Practical-RIFE.git "$runtime_root"
fi
actual_commit="$(git -C "$runtime_root" rev-parse HEAD)"
if [[ "$actual_commit" != "$RIFE_COMMIT" ]]; then
  echo "refusing unpinned Practical-RIFE checkout: expected $RIFE_COMMIT, found $actual_commit" >&2
  exit 1
fi

python3.11 -m venv "$runtime_root/.venv"
python_bin="$runtime_root/.venv/bin/python"
"$python_bin" -m pip install --upgrade "pip==26.1.2"
"$python_bin" -m pip install \
  "torch==2.13.0" \
  "torchvision==0.28.0" \
  "numpy==1.23.5" \
  "tqdm==4.69.0" \
  "scikit-video==1.1.11" \
  "opencv-python==4.11.0.86" \
  "moviepy==1.0.3" \
  "scipy==1.15.3" \
  "gdown==6.1.0"

download_dir="$runtime_root/downloads"
archive="$download_dir/rife-v4.25.zip"
mkdir -p "$download_dir"
if [[ ! -f "$archive" ]]; then
  "$runtime_root/.venv/bin/gdown" "$MODEL_FILE_ID" -O "$archive"
fi
actual_archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
if [[ "$actual_archive_sha" != "$MODEL_ARCHIVE_SHA" ]]; then
  echo "RIFE 4.25 archive checksum mismatch" >&2
  exit 1
fi

model_dir="$runtime_root/train_log"
mkdir -p "$model_dir"
unzip -jn "$archive" \
  train_log/flownet.pkl \
  train_log/IFNet_HDv3.py \
  train_log/RIFE_HDv3.py \
  train_log/refine.py \
  -d "$model_dir"

declare -A expected=(
  ["flownet.pkl"]="6615790efd627772917205db291f51cd392528a157ecbb2ecaeec3bff8eb6de2"
  ["IFNet_HDv3.py"]="655b4c772b037967b86c2dd31c8fa3b5323b79dd9a0e0088708d89149bbc8a32"
  ["RIFE_HDv3.py"]="81bbd0648e499de79e44768d284005d9d57d0f6eb7c30adae407f22675055730"
  ["refine.py"]="0c5698b4a05b9f6ab551740575c1c35e248e5b1829bab6445186081ebe15f032"
)
for filename in "${!expected[@]}"; do
  actual_sha="$(sha256sum "$model_dir/$filename" | awk '{print $1}')"
  if [[ "$actual_sha" != "${expected[$filename]}" ]]; then
    echo "RIFE 4.25 model checksum mismatch: $filename" >&2
    exit 1
  fi
done

"$python_bin" -c 'import torch; assert torch.cuda.is_available()'
echo "Practical-RIFE 4.25 runtime verified at $runtime_root"
echo "Configure python=$python_bin, script=$runtime_root/inference_video.py, model_directory=$model_dir"
