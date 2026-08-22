# kbin

Kaggle無料GPU枠でビルドしたバイナリ（llama.cpp等）をMac側にバックアップし、
必要な時にKaggle datasetとして戻して再利用するためのCLI。
毎回の自前ビルド（llama.cppで約20分）をdatasetマウント＋コピー数秒に短縮する。

## フロー

```
Kaggleビルドkernel ──(成果物 tar.gz)──> /kaggle/working
        │
        ▼ kbin save <kernel> <name>        # Macにバックアップ（これが正本）
~/kaggle-bincache/<name>/<日時>/
        │
        ▼ kbin push <name>                 # Kaggle dataset (kbin-<name>) へ
利用側kernel: dataset_sources に追加 → kbin snippet <name> のセルで復元
```

## コマンド

| コマンド | 動作 |
|---|---|
| `kbin.py save <kernel> <name> [--all] [--note "..."]` | kernel出力をローカル保存。デフォルトはtar.gz/tgz/zipのみ対象 |
| `kbin.py import <file> <name> [--note "..."]` | ローカルビルドの成果物(tar.gz等)を取り込む。Kaggle外(WSL2等)でビルドした場合の入口 |
| `kbin.py build <recipe> --host <ssh先> [--wsl <distro> --exchange-dir <C:/...>] [--name n] [--push]` | recipes/のレシピをssh先でビルド→回収→登録。`--push`でdataset化まで一気に |
| `kbin.py push <name>` | latestを dataset `<user>/kbin-<name>` にcreate/version |
| `kbin.py list` | バックアップ一覧 |
| `kbin.py snippet <name>` | notebook側の復元セル（コピー＋chmod）を出力 |

## ビルドkernel側の作法

- 成果物は tar.gz で `/kaggle/working` に置く（例: `tar czf /kaggle/working/llamacpp-bin.tar.gz -C /tmp/llama.cpp/build/bin .`）
- T4/P100両対応にするなら `-DCMAKE_CUDA_ARCHITECTURES="60;75"`
- 共有ライブラリ同梱を避けるなら `-DBUILD_SHARED_LIBS=OFF`（スタティックビルド）
- meta情報（commit hash、arch、ビルド日）を `--note` に残す

## クロスビルド（build）

Kaggle上のビルドは遅い（4vCPU、llama.cppで20〜27分）。手元のLinux箱（WSL2可）でビルドすれば数分:

```
python3 kbin.py build llamacpp --host x299 --wsl Ubuntu \
  --exchange-dir C:/Users/youei/work/AI/kbuild --name llamacpp-cuda --push
```

- レシピ = `recipes/<name>.sh`。契約は「成果物tar.gzを `$KBIN_OUT` に書く」だけ
- nvccはGPU実物なしで任意アーキ(sm60/75/86…)のコードを吐けるので、ビルド箱にKaggleと同じGPUは不要
- CPU側の互換条件: Linux x86_64 + Kaggleイメージと同世代のglibc（Ubuntu 22.04ならOK）
- WSL2ホストは `--wsl <distro>` + `--exchange-dir`（WSL内/tmpはscpから見えないためWindows側パスで受け渡し）

## 注意

- **Kaggleはdataset作成時にtar.gz/zipを自動展開する**（2026-08確認）→ snippetは展開後ディレクトリ/生tar.gz両対応
- datasetのファイルは実行権限が落ちる → snippetのchmodが必須
- バイナリはKaggleイメージ（glibc/CUDAランタイム）に依存。イメージ更新で壊れたら再ビルド
- 保存先は `KBIN_HOME` 環境変数で変更可（デフォルト `~/kaggle-bincache`）
