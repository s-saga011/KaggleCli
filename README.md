# KaggleCli (kbin)

Kaggle無料GPU枠で使うバイナリ（llama.cpp等）をローカルにバックアップし、
Kaggle datasetとして戻して再利用するためのCLI。
毎回の自前ビルド（llama.cppで20〜27分）をdatasetマウント＋コピー数秒に短縮する。

本体は `kbin.py` 1ファイル。依存はkaggle CLI（認証済み）のみ。

## フロー

ビルドの入口は3つ、出口は全部同じ dataset:

```
[入口1] Kaggle上でビルド ──> kbin save <kernel> <name>
[入口2] 手元Linux/WSL2でクロスビルド ──> kbin build <recipe> --host <ssh先>
[入口3] どこかでビルド済みのtar.gz ──> kbin import <file> <name>
                    │
                    ▼
       ~/kaggle-bincache/<name>/<日時>/   ← ローカル正本（版管理つき）
                    │
                    ▼ kbin push <name>
       Kaggle dataset <user>/kbin-<name>
                    │
                    ▼ 利用側kernel: dataset_sources に追加し
                      kbin snippet <name> の復元セルを貼る（数秒で展開）
```

## コマンド

| コマンド | 動作 |
|---|---|
| `kbin.py save <kernel> <name> [--all] [--note "..."]` | kernel出力をローカル保存。デフォルトはtar.gz/tgz/zipのみ対象 |
| `kbin.py import <file> <name> [--note "..."]` | ローカルのビルド成果物(tar.gz等)を取り込む |
| `kbin.py build <recipe> --host <ssh先> [--wsl <distro> --exchange-dir <C:/...>] [--name n] [--push]` | recipes/のレシピをssh先でビルド→回収→登録。`--push`でdataset化まで一気に |
| `kbin.py push <name>` | latestを dataset `<user>/kbin-<name>` にcreate/version |
| `kbin.py list` | バックアップ一覧（版数・サイズ・note） |
| `kbin.py snippet <name>` | notebook側の復元セル（コピー＋chmod）を出力 |

## クロスビルド（build / GitHub Actions）

nvccはGPU実物なしで任意アーキ(sm60/75/86…)のコードを吐け、リンクもtoolkit同梱の
スタブで足りるので、**ビルド箱にGPUは不要**。条件はLinux x86_64 +
Kaggleイメージと同世代のglibc（= Ubuntu 22.04）だけ。

**手元のLinux/WSL2でビルド**（10C/20Tマシンで約6分。実測でKaggle P100/T4動作確認済み）:

```
python3 kbin.py build llamacpp --host x299 --wsl Ubuntu \
  --exchange-dir C:/Users/youei/work/AI/kbuild --name llamacpp-cuda --push
```

- レシピ = `recipes/<name>.sh`。契約は「成果物tar.gzを `$KBIN_OUT` に書く」だけ
- WSL2ホストは `--wsl <distro>` + `--exchange-dir`（WSL内/tmpはscpから見えない +
  PowerShell経由のバイナリstdoutは壊れるため、Windows側パスで受け渡し）

**GitHub Actionsでビルド**（ビルド箱すら不要）: `.github/workflows/build-llamacpp.yml` を
workflow_dispatchで起動 → artifactを `gh run download` で回収 → `kbin import`。
ランナーは **ubuntu-22.04固定**（latest=24.04はglibcが新しすぎてKaggleで動かない）。

## ビルドレシピ/kernelの作法

- 成果物は tar.gz 1個にまとめる（`tar czf ... -C build/bin .`）
- アーキは P100=60 / T4=75 を含める。手元GPUで動作確認したいならそれも足す（例: `60;75;86`）
- `-DBUILD_SHARED_LIBS=OFF`（スタティック）にすると.soの同梱・LD_LIBRARY_PATH不要
- BUILDINFO.txt（commit/arch/フラグ/ビルド日）を同梱し、`--note` にも要点を残す

## 注意

- **Kaggleはdataset作成時にtar.gz/zipを自動展開する**（2026-08確認）→ snippetは展開後ディレクトリ/生tar.gz両対応
- datasetのファイルは実行権限が落ちる → snippetのchmodが必須
- Ubuntu 22.04のapt標準nvcc(11.5)はgcc 11と非互換 → recipes/llamacpp.shはCUDA 12.6を自動導入
- バイナリはKaggleイメージ（glibc/CUDAランタイム）に依存。イメージ更新で壊れたら再ビルド
- 保存先は `KBIN_HOME` 環境変数で変更可（デフォルト `~/kaggle-bincache`）
