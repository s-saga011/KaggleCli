# KaggleCli (kbin)

**ビルドにKaggleのGPU枠を1秒も使わない**ためのCLI。

Kaggleで llama.cpp 等を使うたびにnotebook内でビルドすると、セッションごとに20〜27分、
週30時間しかない無料GPU枠がコンパイルに溶けていく。KaggleCliはビルドを**GPUの要らない
CPUマシン**（GitHub Actionsの無料ランナーで可、ビルドマシン所有も不要）に追い出し、
Kaggle側はdatasetから数秒で復元するだけにする。

これが成立する理屈: nvccはGPU実物なしで任意アーキ(sm60/75/…)のコードを吐け、
リンクもtoolkit同梱のスタブで足りる。GPUが要るのは実行時だけ。

本体は `kbin.py` 1ファイル。依存は kaggle CLI と gh CLI（どちらも認証済み）のみ。

## クイックスタート（GitHub Actions、ビルドマシン不要）

```bash
python3 kbin.py ci --push
```

これだけで GitHub Actionsでビルド（約20〜30分）→ artifact回収 → ローカルバックアップ →
Kaggle dataset `<user>/kbin-llamacpp-cuda` 作成まで完走する。

git cloneすら不要のワンライナー版:

```bash
gh api repos/s-saga011/KaggleCli/contents/kbin.py -H "Accept: application/vnd.github.raw" \
  | python3 - ci --push
# 公開後: curl -fsSL https://raw.githubusercontent.com/s-saga011/KaggleCli/main/kbin.py | python3 - ci --push
```

利用側kernelでは `kernel-metadata.json` に `"dataset_sources": ["<user>/kbin-llamacpp-cuda"]`
を足し、`python3 kbin.py snippet llamacpp-cuda` が出力する復元セルを貼るだけ（数秒で展開）。

ワークフロー本体は `.github/workflows/build-llamacpp.yml`。ランナーは **ubuntu-22.04固定**
（Kaggleイメージとglibc 2.35を合わせる。latest=24.04は不可）。

## フロー全体像

```
[推奨] GitHub Actions ─── kbin ci
[速い] 手元のLinux/WSL2 ── kbin build <recipe> --host <ssh先>   (10C/20T機で約6分)
[手動] Kaggle上でビルド ── kbin save <kernel> <name>
[持込] ビルド済みtar.gz ── kbin import <file> <name>
                  │
                  ▼
     ~/kaggle-bincache/<name>/<日時>/   ← ローカル正本（版管理つき）
                  │
                  ▼ kbin push <name>
     Kaggle dataset <user>/kbin-<name>
                  │
                  ▼ 利用側kernel: dataset_sources + snippet復元セル
```

## コマンド

| コマンド | 動作 |
|---|---|
| `kbin.py ci [--workflow build-llamacpp] [--artifact llamacpp-bin] [--name n] [--push]` | **推奨**: Actionsでビルド→回収→登録。`--push`でdataset化まで |
| `kbin.py build <recipe> --host <ssh先> [--wsl <distro> --exchange-dir <C:/...>] [--name n] [--push]` | 手元/リモートLinuxでビルド→回収→登録（オプション、速い） |
| `kbin.py save <kernel> <name> [--all] [--note "..."]` | Kaggle kernel出力をローカル保存 |
| `kbin.py import <file> <name> [--note "..."]` | ビルド済みtar.gzを取り込む |
| `kbin.py push <name>` | latestを dataset `<user>/kbin-<name>` にcreate/version |
| `kbin.py list` | バックアップ一覧（版数・サイズ・note） |
| `kbin.py snippet <name>` | notebook側の復元セル（コピー＋chmod）を出力 |

## オプション: 手元マシンでビルド（Actionsより速い）

Linux箱（WSL2可）がsshで見えるなら、4vCPUのActionsランナーより大幅に速い（実測6分 vs 20〜30分）:

```bash
python3 kbin.py build llamacpp --host <linux箱> --name llamacpp-cuda --push
# Windows+WSL2ホストの場合
python3 kbin.py build llamacpp --host x299 --wsl Ubuntu \
  --exchange-dir C:/Users/youei/work/AI/kbuild --name llamacpp-cuda --push
```

- レシピ = `recipes/<name>.sh`。契約は「成果物tar.gzを `$KBIN_OUT` に書く」だけ。
  ローカルに無ければ gh api → raw.githubusercontent の順で自動取得（`KBIN_REPO`で差し替え可）
- 条件はLinux x86_64 + Ubuntu 22.04世代のglibcのみ。**GPUは不要**
- WSL2ホストは `--wsl <distro>` + `--exchange-dir`（WSL内/tmpはscpから見えない +
  PowerShell経由のバイナリstdoutは壊れるため、Windows側パスで受け渡し）

## ビルドレシピ/workflowの作法

- 成果物は tar.gz 1個にまとめる（`tar czf ... -C build/bin .`）
- アーキは P100=60 / T4=75 を含める。手元GPUで動作確認したいならそれも足す（例: `60;75;86`）
- `-DBUILD_SHARED_LIBS=OFF`（スタティック）にすると.soの同梱・LD_LIBRARY_PATH不要
- BUILDINFO.txt（commit/arch/フラグ/ビルド日）を同梱し、`--note` にも要点を残す

## 注意

- **Kaggleはdataset作成時にtar.gz/zipを自動展開する**（2026-08確認）→ snippetは展開後ディレクトリ/生tar.gz両対応
- datasetのファイルは実行権限が落ちる → snippetのchmodが必須
- Ubuntu 22.04のapt標準nvcc(11.5)はgcc 11と非互換 → レシピ/workflowはCUDA 12.6を導入して使う
- バイナリはKaggleイメージ（glibc/CUDAランタイム）に依存。イメージ更新で壊れたら再ビルド
- 保存先は `KBIN_HOME` 環境変数で変更可（デフォルト `~/kaggle-bincache`）

実機検証: このリポジトリのバイナリ（arch 60;75;86, static, CUDA 12.6）はKaggle P100 / T4×2 の両方で動作確認済み。
