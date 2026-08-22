# KaggleCli (kbin) — Claude向けガイド

## これは何

Kaggle無料GPU枠でビルドしたバイナリ（llama.cpp等）を再利用するためのCLI。
Kaggleのバッチ実行はコンテナが毎回まっさらでビルドし直しになる（llama.cppで約20分）。
これを「一度ビルド → Macにバックアップ → Kaggle datasetとして戻す → 以後はコピー数秒」にする。

本体は `kbin.py` 1ファイルのみ。`python3 kbin.py <cmd>` で実行（依存はkaggle CLIだけ）。

## 前提

- `kaggle` CLI（2.x）が認証済みであること（`~/.kaggle/kaggle.json` にusername/key）
- バックアップ正本はMac側 `~/kaggle-bincache/<name>/<日時>/`（`KBIN_HOME`で変更可）
- Kaggle側dataset名は `<user>/kbin-<name>` に固定

## コマンド

```bash
python3 kbin.py save <kernel_ref> <name> [--all] [--note "..."]
    # kernel出力をDLしてローカル保存。デフォルトはtar.gz/tgz/zipのみ拾う
    # 例: python3 kbin.py save shosaga/llamacpp-bench-t4x2 llamacpp-cuda \
    #        --note "commit 2115b73, arch 60;75, static"

python3 kbin.py push <name>
    # latest版を dataset <user>/kbin-<name> へ。既存ならversion、新規ならcreate

python3 kbin.py import <tar.gz> <name> [--note "..."]
    # Kaggle外(x299 WSL2等)でビルドした成果物を取り込む。以降のpushは同じ

python3 kbin.py build <recipe> --host <ssh先> [--wsl <distro> --exchange-dir <C:/...>] [--push]
    # recipes/<recipe>.sh をssh先で実行→成果物回収→登録。--pushでdataset化まで
    # レシピの契約: 成果物tar.gzを $KBIN_OUT に書く。冗長ログはリモート/tmpに逃がす
    # 例: python3 kbin.py build llamacpp --host x299 --wsl Ubuntu \
    #        --exchange-dir C:/Users/youei/work/AI/kbuild --name llamacpp-cuda --push

python3 kbin.py list        # バックアップ一覧（版数・サイズ・note）
python3 kbin.py snippet <name>   # notebook側の復元セルを標準出力に出す
```

## 典型フロー（新しいバイナリを登録する）

1. Kaggle上のビルドkernelで成果物をtar.gzにして `/kaggle/working` に置く
   ```python
   !tar czf /kaggle/working/llamacpp-bin.tar.gz -C /tmp/llama.cpp/build/bin .
   ```
2. kernel完走後、Macで `kbin.py save <kernel> <name> --note "<commit/arch>"`
3. `kbin.py push <name>` でdataset化
4. 利用側kernelの `kernel-metadata.json` に `"dataset_sources": ["<user>/kbin-<name>"]` を追加し、
   `kbin.py snippet <name>` が出力するセルをnotebook先頭に貼る

## ハマりどころ（重要）

- **Kaggleはdataset作成時にtar.gz/zipを自動展開する**（2026-08確認）。復元コードは「展開後ディレクトリ」「生tar.gz」両対応にする（snippetは対応済み）
- **datasetのファイルは実行権限が落ちる** → snippetのchmodを省略しない
- Ubuntu 22.04のapt標準nvcc(11.5)はgcc 11と非互換（std_function.hのparameter packsバグ）→ recipes/llamacpp.shはCUDA 12.6を自動導入する
- Windows+WSL2ホストへのssh実行はstdinパススルー可（`ssh host "wsl -d Ubuntu -u root -- bash -s" < script`）。ただし**バイナリのssh stdout回収はPowerShellが壊すので不可** → 成果物はWindows側パス(--exchange-dir)経由でscp
- **datasetマウントパスは新形式** `/kaggle/input/datasets/<owner>/<slug>/`（旧 `/kaggle/input/<slug>/` は404。2026-08確認）
- バイナリはビルド時のKaggleイメージ（glibc/CUDAランタイム）に依存。イメージ更新で動かなくなったら再ビルドして save し直す
- GPUアーキ違いに注意: T4=sm75, P100=sm60。両方で使うなら `-DCMAKE_CUDA_ARCHITECTURES="60;75"` のfatビルドにする
- llama.cppをKaggleでビルドする場合、`CUDA::cuda_driver` が見つからずCMakeが失敗する
  → `-DCUDA_cuda_driver_LIBRARY=/usr/local/nvidia/lib64/libcuda.so.1` を直指定
  （`GGML_CUDA_NO_VMM=ON` でも回避できるが `-sm row` が使えなくなる）
- `--note` にcommit hash・arch・static/sharedを必ず残す。後から「このバイナリ何だっけ」を防ぐ

## 変更時の作法

- 1ファイル主義を守る（kbin.pyを分割しない）。一人開発ツールなので抽象化より簡潔さ優先
- 新コマンドを足したらREADME.mdの表とこのファイルも更新する
