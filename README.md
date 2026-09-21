# KaggleCli (kbin)

**ビルドにKaggleのGPU枠を1秒も使わない**ためのCLI。

Kaggleで llama.cpp 等を使うたびにnotebook内でビルドすると、セッションごとに20〜27分、
週30時間しかない無料GPU枠がコンパイルに溶けていく。KaggleCliはビルドを**GPUの要らない
CPUマシン**（GitHub Actionsの無料ランナーで可、ビルドマシン所有も不要）に追い出し、
Kaggle側はdatasetから数秒で復元するだけにする。

これが成立する理屈: nvccはGPU実物なしで任意アーキ(sm60/75/…)のコードを吐け、
リンクもtoolkit同梱のスタブで足りる。GPUが要るのは実行時だけ。

本体は `kbin.py` 1ファイル。依存は kaggle CLI と gh CLI（どちらも認証済み）のみ。

同じ理屈がそのまま **Google Colab** にも効く（→ [Colab でも使える](#colab-でも使える)）。Colab 無料枠は **2 コア**しかなく、Kaggle より CUDA ビルドが遅い。

## クイックスタート

```bash
python3 kbin.py auto --push
```

`auto` が実行マシンを見て最速経路を選ぶ（`--backend` で固定も可）:

| 実行マシン | 経路 | 所要 |
|---|---|---|
| Linux | **docker（linux/amd64、ホストを汚さない）**、無ければその場でビルド | 実測19分(初回toolkit込)〜 |
| Windows | **自機のWSL**、無ければdocker、どちらも無ければ `wsl --install` 試行 | 実測12分 |
| Mac (Apple Silicon) | GitHub Actionsに委譲（= `ci`）。`--backend docker` でRosettaビルドも可 | ci実測72分 / Rosetta実測22分 |

dockerバックエンドは `--platform linux/amd64` の ubuntu:22.04 コンテナでレシピを実行する。
glibcがKaggleと同世代に固定され、CUDA toolkitがホストに入らないのが利点。

どの経路でも ローカルバックアップ → Kaggle dataset `<user>/kbin-llamacpp-cuda` 作成まで完走する。
Actionsだけ使いたい場合は `python3 kbin.py ci --push`。

git cloneすら不要のワンライナー版（レシピはGitHubから自動取得）:

```bash
gh api repos/s-saga011/KaggleCli/contents/kbin.py -H "Accept: application/vnd.github.raw" \
  | python3 - auto --push
# 公開後: curl -fsSL https://raw.githubusercontent.com/s-saga011/KaggleCli/main/kbin.py | python3 - auto --push
```

利用側kernelでは `kernel-metadata.json` に `"dataset_sources": ["<user>/kbin-llamacpp-cuda"]`
を足し、`python3 kbin.py snippet llamacpp-cuda` が出力する復元セルを貼るだけ（数秒で展開）。

ワークフロー本体は `.github/workflows/build-llamacpp.yml`。ランナーは **ubuntu-22.04固定**
（Kaggleイメージとglibc 2.35を合わせる。latest=24.04は不可）。

## フロー全体像

```
[推奨] kbin auto ────────── 実行マシンで下記から最速を自動選択
[CI]   GitHub Actions ───── kbin ci                              (設備ゼロ、20-30分)
[速い] 手元/ssh先のLinux ── kbin build <recipe> --host <ssh先>   (10C/20T機で約6分)
[手動] Kaggle上でビルド ─── kbin save <kernel> <name>
[持込] ビルド済みtar.gz ─── kbin import <file> <name>
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
| `kbin.py auto [--recipe llamacpp] [--name n] [--push]` | **推奨**: 実行マシンで最速経路を自動選択（Win=自機WSL / Linux=直 / 他=ci） |
| `kbin.py ci [--workflow build-llamacpp] [--artifact llamacpp-bin] [--name n] [--push]` | Actionsでビルド→回収→登録（ビルドマシン不要） |
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
python3 kbin.py build llamacpp --host linuxpc --wsl Ubuntu \
  --exchange-dir C:/Users/<you>/work/AI/kbuild --name llamacpp-cuda --push
```

- レシピ = `recipes/<name>.sh`。契約は「成果物tar.gzを `$KBIN_OUT` に書く」だけ。
  ローカルに無ければ gh api → raw.githubusercontent の順で自動取得（`KBIN_REPO`で差し替え可）
- 条件はLinux x86_64 + Ubuntu 22.04世代のglibcのみ。**GPUは不要**
- WSL2ホストは `--wsl <distro>` + `--exchange-dir`（WSL内/tmpはscpから見えない +
  PowerShell経由のバイナリstdoutは壊れるため、Windows側パスで受け渡し）

## Colab でも使える

焼いたバイナリは Kaggle 専用ではない。**Colab の T4 は Kaggle の T4 と同じ sm75** なので、
`archs` に 75 が入っていればそのまま動く。3080/3090 でも確認したいなら `60;75;86` のまま使う。

Colab 無料枠でビルドしない方がいい理由（2026-09-19 実測）:

| | Colab 無料枠 | GitHub Actions |
|---|---|---|
| CPU | **2 コア** | 4 コア |
| GPU | Tesla T4 15,360 MiB | 不要 |
| RAM / ディスク | 12 GB / 189 GB 空き | — |
| CUDA | 12.8（driver 580.82.07） | `cuda` 入力で選ぶ |

Kaggle と違って GPU 枠の時間課金ではないが、2 コアで CUDA を焼くのは単純に遅い。
焼くのは Actions、Colab は受け取るだけにする。

### 配り方（Colab CLI）

```bash
uv tool install google-colab-cli
colab new -s work --gpu T4          # 初回はブラウザで OAuth 承認
gh run download <id> -R s-saga011/KaggleCli -n llamacpp-bin -D /tmp/kb
colab upload -s work /tmp/kb/llamacpp-bin.tar.gz /content/llamacpp-bin.tar.gz
colab exec -s work --timeout 300 -f restore.py   # 展開 + chmod
colab stop -s work                  # ★止め忘れると VM が生き続ける
```

Kaggle dataset 経由と違って **tar.gz が自動展開されない**ので、自分で `tar xzf` する。
実行権限も落ちるので `chmod +x` は同じく必要。

### Release にすると認証なしで落とせる

**Artifact は public リポジトリでも API に認証が要る**（実測 HTTP 401）。
つまり Colab のセルから直接は落とせず、gh 認証済みのマシンを 1 台経由する必要がある。

`release` 入力にタグ名を入れると Release asset として公開する。
**Release は認証不要**なので、そのまま `wget` できる:

```bash
gh workflow run build-llamacpp.yml \
  -f archs="75;86" -f cuda=12-8 -f release=llamacpp-sm75-86
```

```python
# Colab のセル。gh も kaggle 認証も要らない
!wget -q https://github.com/<you>/KaggleCli/releases/download/llamacpp-sm75-86/llamacpp-bin.tar.gz
!mkdir -p bin && tar xzf llamacpp-bin.tar.gz -C bin && chmod +x bin/llama-*
```

`release` を空にすれば従来どおり Artifact のみ（保持 14 日）。Release は無期限。

**この経路はブラウザだけで完結する。** GitHub の Actions タブから
`Run workflow` を押し、フォームに入力するだけで、ローカルマシンも gh CLI も要らない。

### 上流以外（fork）を焼く

`build-llamacpp.yml` は `repo` / `ref` 入力で任意の fork を焼ける。
例: PQ2_0（Bonsai 系ternary量子化）は上流未マージで、PrismML の fork でしか読めない:

```bash
gh workflow run build-llamacpp.yml -R s-saga011/KaggleCli \
  -f repo=PrismML-Eng/llama.cpp -f ref=prism \
  -f archs="75;86" -f cuda=12-8 -f shared=OFF \
  -f targets="llama-bench llama-cli llama-server llama-mtmd-cli"
```

BUILDINFO.txt に `repo` / `ref` が刻まれるので、後から上流版と取り違えない。

## ビルドレシピ/workflowの作法

- 成果物は tar.gz 1個にまとめる（`tar czf ... -C build/bin .`）
- アーキは P100=60 / T4=75 を含める。手元GPUで動作確認したいならそれも足す（例: `60;75;86`）
- `-DBUILD_SHARED_LIBS=OFF`（スタティック）にすると.soの同梱・LD_LIBRARY_PATH不要
- BUILDINFO.txt（commit/repo/ref/arch/フラグ/ビルド日）を同梱し、`--note` にも要点を残す
- fork を焼くときは `repo` / `ref` 入力を使う。ファイルを分けない（分けると片方だけ直す事故が起きる）

## 注意

- **Kaggleはdataset作成時にtar.gz/zipを自動展開する**（2026-08確認）→ snippetは展開後ディレクトリ/生tar.gz両対応
- datasetのファイルは実行権限が落ちる → snippetのchmodが必須
- Ubuntu 22.04のapt標準nvcc(11.5)はgcc 11と非互換 → レシピ/workflowはCUDA 12.6を導入して使う
- バイナリはKaggleイメージ（glibc/CUDAランタイム）に依存。イメージ更新で壊れたら再ビルド
- 保存先は `KBIN_HOME` 環境変数で変更可（デフォルト `~/kaggle-bincache`）
- **Colab CLI は `jupyter-kernel-client` 1.0.x で壊れる**（2026-09-19）。1.0.0 で
  `KernelClient` → `JupyterKernelClient` にリネームされたが colab-cli 側は旧名を参照しており、
  `AttributeError: module 'jupyter_kernel_client' has no attribute 'KernelClient'` で落ちる。
  対処: `uv tool install --force google-colab-cli --with "jupyter-kernel-client==0.15.0"`
- Colab の `colab auth` は**セッションが無いと使えない**。先に `colab new` を実行すると OAuth が始まる

実機検証: このリポジトリのバイナリ（arch 60;75;86, static, CUDA 12.6）はKaggle P100 / T4×2 の両方で動作確認済み。
