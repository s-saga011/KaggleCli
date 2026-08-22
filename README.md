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
| `kbin.py push <name>` | latestを dataset `<user>/kbin-<name>` にcreate/version |
| `kbin.py list` | バックアップ一覧 |
| `kbin.py snippet <name>` | notebook側の復元セル（コピー＋chmod）を出力 |

## ビルドkernel側の作法

- 成果物は tar.gz で `/kaggle/working` に置く（例: `tar czf /kaggle/working/llamacpp-bin.tar.gz -C /tmp/llama.cpp/build/bin .`）
- T4/P100両対応にするなら `-DCMAKE_CUDA_ARCHITECTURES="60;75"`
- 共有ライブラリ同梱を避けるなら `-DBUILD_SHARED_LIBS=OFF`（スタティックビルド）
- meta情報（commit hash、arch、ビルド日）を `--note` に残す

## 注意

- datasetのファイルは実行権限が落ちる → snippetのchmodが必須
- バイナリはKaggleイメージ（glibc/CUDAランタイム）に依存。イメージ更新で壊れたら再ビルド
- 保存先は `KBIN_HOME` 環境変数で変更可（デフォルト `~/kaggle-bincache`）
