#!/usr/bin/env bash
set -euo pipefail

readonly COMFY_TAG="v0.9.2"
readonly COMFY_COMMIT="8f40b43e0204d5b9780f3e9618e140e929e80594"
readonly HF_REPO="Comfy-Org/Wan_2.2_ComfyUI_Repackaged"

if [[ $# -ne 1 || "$1" != /* ]]; then
  echo "usage: $0 /absolute/external/runtime/root" >&2
  exit 2
fi

runtime_root="${1%/}"
comfy_root="$runtime_root/comfyui"
mkdir -p "$runtime_root"

if [[ ! -d "$comfy_root/.git" ]]; then
  git clone --branch "$COMFY_TAG" --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$comfy_root"
fi

actual_commit="$(git -C "$comfy_root" rev-parse HEAD)"
if [[ "$actual_commit" != "$COMFY_COMMIT" ]]; then
  echo "refusing unpinned ComfyUI checkout: expected $COMFY_COMMIT, found $actual_commit" >&2
  exit 1
fi

python3 -m venv "$comfy_root/.venv"
"$comfy_root/.venv/bin/python" -m pip install --upgrade pip
"$comfy_root/.venv/bin/python" -m pip install -r "$comfy_root/requirements.txt" "requests==2.32.5" "huggingface_hub[cli]"

download_dir="$runtime_root/downloads"
mkdir -p "$download_dir"
"$comfy_root/.venv/bin/hf" download "$HF_REPO" \
  --local-dir "$download_dir" \
  split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors \
  split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors \
  split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors \
  split_files/vae/wan_2.1_vae.safetensors

declare -A expected=(
  ["split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"]="6122e79d55e0f235698d11d657f3b196c5273c830da00b2b013c5a048d5e6a42"
  ["split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"]="5471a457b6ac404202a5fbe6c11595a3d5641fc766b00f38763f72303fffc21e"
  ["split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"]="c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68"
  ["split_files/vae/wan_2.1_vae.safetensors"]="2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b"
)

for relative_path in "${!expected[@]}"; do
  source_path="$download_dir/$relative_path"
  actual_sha="$(sha256sum "$source_path" | awk '{print $1}')"
  if [[ "$actual_sha" != "${expected[$relative_path]}" ]]; then
    echo "checksum mismatch: $relative_path" >&2
    exit 1
  fi
  model_kind="$(basename "$(dirname "$relative_path")")"
  target_dir="$comfy_root/models/$model_kind"
  target_path="$target_dir/$(basename "$relative_path")"
  mkdir -p "$target_dir"
  if [[ -e "$target_path" && ! -L "$target_path" ]]; then
    echo "refusing to replace existing model: $target_path" >&2
    exit 1
  fi
  ln -sfn "$source_path" "$target_path"
done

echo "Wan2.2 FLF runtime verified at $comfy_root"
