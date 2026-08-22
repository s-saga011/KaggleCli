#!/bin/bash
# llama.cpp CUDA fat/static ビルドレシピ (Ubuntu 22.04 / WSL2想定、root実行)
#
# 契約: 成果物tar.gzを $KBIN_OUT に書く（kbin buildが先頭でexportする）
# 環境変数: ARCHS でCUDAアーキ上書き可（デフォルト "60;75;86" = P100/T4/3090）
#
# ハマり対策(2026-08実証済み):
# - Ubuntu 22.04のapt標準nvcc(11.5)はgcc 11と非互換(std_function.hバグ) → CUDA 12.6を入れる
# - ビルドログはリモート側 /tmp/kbin-llamacpp.log に逃がす(ssh出力を細く保つ)
set -e
ARCHS="${ARCHS:-60;75;86}"
LOG=/tmp/kbin-llamacpp.log
: > "$LOG"
echo "recipe=llamacpp archs=$ARCHS out=$KBIN_OUT log=$LOG"

if [ ! -x /usr/local/cuda-12.6/bin/nvcc ]; then
  echo "installing cuda-toolkit-12-6 (initial setup, a few minutes)..."
  cd /tmp
  wget -q https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i cuda-keyring_1.1-1_all.deb >> "$LOG" 2>&1
  apt-get update -qq >> "$LOG" 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cuda-toolkit-12-6 >> "$LOG" 2>&1
fi
export PATH=/usr/local/cuda-12.6/bin:$PATH
nvcc --version | tail -1

cd /root
rm -rf kbin-llamacpp
git clone --depth 1 https://github.com/ggml-org/llama.cpp kbin-llamacpp >> "$LOG" 2>&1
cd kbin-llamacpp
COMMIT=$(git rev-parse --short HEAD)
echo "commit: $COMMIT"

cmake -B build -DGGML_CUDA=ON "-DCMAKE_CUDA_ARCHITECTURES=$ARCHS" \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.6/bin/nvcc \
  -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release >> "$LOG" 2>&1
cmake --build build -j"$(nproc)" --target llama-bench llama-cli llama-server >> "$LOG" 2>&1 \
  || { echo "BUILD FAILED — tail of $LOG:"; tail -30 "$LOG"; exit 1; }

{
  echo "commit: $COMMIT"
  echo "arch: $ARCHS"
  echo "flags: BUILD_SHARED_LIBS=OFF LLAMA_CURL=OFF Release"
  echo "built_on: $(hostname) $(nvcc --version | grep release)"
  echo "built_at: $(date -Iseconds)"
  echo "targets: llama-bench llama-cli llama-server"
} > build/bin/BUILDINFO.txt
./build/bin/llama-bench --help > /dev/null && echo "smoke: llama-bench OK"
tar czf "$KBIN_OUT" -C build/bin .
echo "BUILD_DONE $KBIN_OUT"
