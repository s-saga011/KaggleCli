#!/usr/bin/env python3
"""kbin — Kaggleでビルドしたバイナリ等のMac側バックアップ&再利用ツール

フロー:
  1. Kaggle上のビルド用kernelが成果物を /kaggle/working に出す（tar.gz推奨）
  2. kbin save <kernel> <name>   : kernel出力をMacにバックアップ（正本）
  3. kbin push <name>            : バックアップをKaggle datasetにアップロード
  4. kbin snippet <name>         : notebook側で使うコピー&chmodセルを表示
  5. kbin list                   : バックアップ一覧

保存先: ~/kaggle-bincache/<name>/<日時>/ （KBIN_HOME で変更可）
dataset id: <user>/kbin-<name>
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

KBIN_HOME = os.environ.get("KBIN_HOME", os.path.expanduser("~/kaggle-bincache"))


def sh(cmd, **kw):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def kaggle_user():
    # kaggle CLIの認証情報からユーザー名を取る
    cfg = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(cfg):
        return json.load(open(cfg))["username"]
    out = subprocess.run(["kaggle", "config", "view"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "username" in line:
            return line.split()[-1]
    sys.exit("kaggleユーザー名が特定できない。~/.kaggle/kaggle.json を確認")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_save(args):
    """kernel出力をダウンロードしてローカルにバックアップ"""
    with tempfile.TemporaryDirectory() as tmp:
        sh(["kaggle", "kernels", "output", args.kernel, "-p", tmp])
        # 対象ファイル収集（デフォルト: tar.gz/tgz/zip、--all で全部）
        picked = []
        for root, _, files in os.walk(tmp):
            for f in files:
                if f.endswith(".log"):
                    continue
                if args.all or f.endswith((".tar.gz", ".tgz", ".zip")):
                    picked.append(os.path.join(root, f))
        if not picked:
            sys.exit("成果物が見つからない（tar.gz/tgz/zipなし）。--all で全ファイル対象になる")

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(KBIN_HOME, args.name, stamp)
        os.makedirs(dest, exist_ok=True)
        meta = {"name": args.name, "kernel": args.kernel, "saved_at": stamp, "files": {}}
        for p in picked:
            shutil.copy2(p, dest)
            meta["files"][os.path.basename(p)] = {
                "size": os.path.getsize(p), "sha256": sha256(p)}
            print(f"  saved: {os.path.basename(p)} ({os.path.getsize(p) / 2**20:.1f} MB)")
        if args.note:
            meta["note"] = args.note
        with open(os.path.join(dest, "meta.json"), "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        # latest を更新
        latest = os.path.join(KBIN_HOME, args.name, "latest")
        if os.path.islink(latest):
            os.unlink(latest)
        os.symlink(stamp, latest)
        print(f"OK: {dest}")


def cmd_import(args):
    """ローカルファイル(x299等でビルドしたtar.gz)をバックアップに取り込む"""
    if not os.path.exists(args.file):
        sys.exit(f"ファイルが無い: {args.file}")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(KBIN_HOME, args.name, stamp)
    os.makedirs(dest, exist_ok=True)
    shutil.copy2(args.file, dest)
    base = os.path.basename(args.file)
    meta = {"name": args.name, "source": os.path.abspath(args.file),
            "saved_at": stamp,
            "files": {base: {"size": os.path.getsize(args.file),
                             "sha256": sha256(args.file)}}}
    if args.note:
        meta["note"] = args.note
    with open(os.path.join(dest, "meta.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    latest = os.path.join(KBIN_HOME, args.name, "latest")
    if os.path.islink(latest):
        os.unlink(latest)
    os.symlink(stamp, latest)
    print(f"OK: {dest} ({os.path.getsize(args.file) / 2**20:.1f} MB)")


def cmd_push(args):
    """ローカルバックアップ(latest)をKaggle datasetへ"""
    src = os.path.join(KBIN_HOME, args.name, "latest")
    if not os.path.exists(src):
        sys.exit(f"バックアップが無い: {src}（先に kbin save）")
    user = kaggle_user()
    ds_id = f"{user}/kbin-{args.name}"
    with tempfile.TemporaryDirectory() as tmp:
        for f in os.listdir(src):
            shutil.copy2(os.path.join(src, f), tmp)
        with open(os.path.join(tmp, "dataset-metadata.json"), "w") as f:
            json.dump({"id": ds_id, "title": f"kbin {args.name}",
                       "licenses": [{"name": "CC0-1.0"}]}, f)
        # 既存datasetなら version、無ければ create
        exists = subprocess.run(["kaggle", "datasets", "status", ds_id],
                                capture_output=True, text=True).returncode == 0
        if exists:
            sh(["kaggle", "datasets", "version", "-p", tmp,
                "-m", f"kbin push {datetime.date.today()}", "--dir-mode", "zip"])
        else:
            sh(["kaggle", "datasets", "create", "-p", tmp, "--dir-mode", "zip"])
    print(f"OK: https://www.kaggle.com/datasets/{ds_id}")
    print(f"kernel-metadata.json に追加: \"dataset_sources\": [\"{ds_id}\"]")


def cmd_list(args):
    if not os.path.isdir(KBIN_HOME):
        print("(バックアップなし)")
        return
    for name in sorted(os.listdir(KBIN_HOME)):
        d = os.path.join(KBIN_HOME, name)
        if not os.path.isdir(d):
            continue
        vers = sorted(v for v in os.listdir(d) if v != "latest")
        latest = os.path.realpath(os.path.join(d, "latest")) if vers else ""
        meta_p = os.path.join(latest, "meta.json")
        note = ""
        total = 0
        if os.path.exists(meta_p):
            m = json.load(open(meta_p))
            total = sum(v["size"] for v in m["files"].values())
            note = m.get("note", "")
        print(f"{name}: {len(vers)}版 latest={os.path.basename(latest)} "
              f"{total / 2**20:.1f}MB {note}")


def cmd_snippet(args):
    user = kaggle_user()
    print(f"""# --- notebook側: kbin-{args.name} からバイナリ復元 ---
# kernel-metadata.json: "dataset_sources": ["{user}/kbin-{args.name}"]
import glob, os, subprocess
SRC = "/kaggle/input/datasets/{user}/kbin-{args.name}"
os.makedirs("/tmp/bin", exist_ok=True)
for f in glob.glob(f"{{SRC}}/*"):
    if f.endswith((".tar.gz", ".tgz")):
        subprocess.run(["tar", "xzf", f, "-C", "/tmp/bin"], check=True)
    else:
        subprocess.run(["cp", f, "/tmp/bin/"], check=True)
subprocess.run("chmod +x /tmp/bin/* 2>/dev/null; chmod +x /tmp/bin/**/* 2>/dev/null",
               shell=True)
print(os.listdir("/tmp/bin"))""")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("save", help="kernel出力をローカルにバックアップ")
    p.add_argument("kernel", help="kernel ref (例: shosaga/llamacpp-bench-t4x2)")
    p.add_argument("name", help="バックアップ名 (例: llamacpp-cuda)")
    p.add_argument("--all", action="store_true", help="tar.gz以外の全ファイルも保存")
    p.add_argument("--note", help="メモ (例: 'commit 2115b73, arch 60;75, static')")
    p.set_defaults(fn=cmd_save)

    p = sub.add_parser("import", help="ローカルビルド成果物(tar.gz等)を取り込む")
    p.add_argument("file", help="取り込むファイル")
    p.add_argument("name", help="バックアップ名")
    p.add_argument("--note", help="メモ (例: 'x299 WSL2, commit xxx, arch 60;75;86')")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("push", help="latestをKaggle datasetへアップロード")
    p.add_argument("name")
    p.set_defaults(fn=cmd_push)

    p = sub.add_parser("list", help="バックアップ一覧")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("snippet", help="notebook側の復元セルを表示")
    p.add_argument("name")
    p.set_defaults(fn=cmd_snippet)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
