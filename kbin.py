#!/usr/bin/env python3
"""kbin — ビルドにKaggleのGPU枠を1秒も使わないためのCLI

ビルドはGPU不要のCPUマシンに追い出し、Kaggle側はdatasetから数秒で復元する。

推奨フロー（ビルドマシン不要）:
  kbin ci --push                 : GitHub Actionsでビルド→回収→dataset化まで一発
  kbin snippet <name>            : notebook側で使う復元セルを表示

その他の入口:
  kbin build <recipe> --host <ssh先>  : 手元/リモートLinuxでビルド（速い、約6分）
  kbin save <kernel> <name>           : Kaggle上でビルドした成果物を回収
  kbin import <file> <name>           : ビルド済みtar.gzを取り込む

保存先: ~/kaggle-bincache/<name>/<日時>/ （KBIN_HOME で変更可）
dataset id: <user>/kbin-<name>
"""
import argparse
import datetime
import glob as _glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

KBIN_HOME = os.environ.get("KBIN_HOME", os.path.expanduser("~/kaggle-bincache"))


def sh(cmd, **kw):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def kaggle_user():
    # kaggle CLIの認証情報からユーザー名を取る
    cfg = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(cfg):
        return json.load(open(cfg, encoding="utf-8"))["username"]
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
        with open(os.path.join(dest, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        set_latest(args.name, stamp)
        print(f"OK: {dest}")


def set_latest(name, stamp):
    """latestポインタ更新。symlink不可環境(Windows非管理者)はLATESTファイルで代替"""
    d = os.path.join(KBIN_HOME, name)
    latest = os.path.join(d, "latest")
    try:
        if os.path.islink(latest):
            os.unlink(latest)
        os.symlink(stamp, latest)
    except OSError:
        with open(os.path.join(d, "LATEST"), "w", encoding="utf-8") as f:
            f.write(stamp)


def get_latest(name):
    """latestの実体ディレクトリを返す（symlink or LATESTファイル）。無ければNone"""
    d = os.path.join(KBIN_HOME, name)
    latest = os.path.join(d, "latest")
    if os.path.exists(latest):
        return os.path.realpath(latest)
    lf = os.path.join(d, "LATEST")
    if os.path.exists(lf):
        p = os.path.join(d, open(lf, encoding="utf-8").read().strip())
        return p if os.path.isdir(p) else None
    return None


def store_file(path, name, note=None, source=None):
    """1ファイルをバックアップに登録し latest を更新（import/build共通）"""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(KBIN_HOME, name, stamp)
    os.makedirs(dest, exist_ok=True)
    shutil.copy2(path, dest)
    base = os.path.basename(path)
    meta = {"name": name, "source": source or os.path.abspath(path),
            "saved_at": stamp,
            "files": {base: {"size": os.path.getsize(path),
                             "sha256": sha256(path)}}}
    if note:
        meta["note"] = note
    with open(os.path.join(dest, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    set_latest(name, stamp)
    print(f"OK: {dest} ({os.path.getsize(path) / 2**20:.1f} MB)")


def cmd_import(args):
    """ローカルファイル(x299等でビルドしたtar.gz)をバックアップに取り込む"""
    if not os.path.exists(args.file):
        sys.exit(f"ファイルが無い: {args.file}")
    store_file(args.file, args.name, note=args.note)


def load_recipe(name):
    """レシピ解決: ①スクリプト隣のrecipes/ ②gh api ③raw.githubusercontent
    ②③のおかげで `gh api .../kbin.py | python3 - build ...` のワンライナーでも動く"""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        base = ""
    local = os.path.join(base, "recipes", f"{name}.sh")
    if base and os.path.exists(local):
        return open(local, encoding="utf-8").read()
    repo = os.environ.get("KBIN_REPO", "s-saga011/KaggleCli")
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/recipes/{name}.sh",
                        "-H", "Accept: application/vnd.github.raw"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        print(f"[recipe] gh api {repo}/recipes/{name}.sh")
        return r.stdout
    import urllib.request
    url = f"https://raw.githubusercontent.com/{repo}/main/recipes/{name}.sh"
    try:
        body = urllib.request.urlopen(url, timeout=15).read().decode()
        print(f"[recipe] {url}")
        return body
    except Exception:
        sys.exit(f"レシピ取得失敗: {name}（local/gh api/raw いずれも不可。"
                 f"private repoの場合は gh auth login が必要）")


def cmd_build(args):
    """レシピをビルドホストでssh実行し、成果物を回収してバックアップ登録する

    レシピの契約: recipes/<recipe>.sh は $KBIN_OUT に成果物tar.gzを書く。
    冗長なビルドログはリモート側の /tmp/kbin-*.log に逃がす（ssh出力を細く保つ）。
    WSL2ホスト(Windows)の場合は --wsl <distro> と --exchange-dir <C:/...> を指定:
    成果物はWindows側パス経由で受け渡し（WSL内/tmpはscpから見えないため）。
    """
    recipe_body = load_recipe(args.recipe)
    name = args.name or args.recipe
    if args.wsl:
        if not args.exchange_dir:
            sys.exit("--wsl には --exchange-dir (Windows側パス 例 C:/Users/x/work) が必要")
        win = args.exchange_dir.replace("\\", "/").rstrip("/")
        mnt = f"/mnt/{win[0].lower()}{win[2:]}"  # C:/foo -> /mnt/c/foo
        kbin_out = f"{mnt}/kbin-{name}.tar.gz"
        remote_cmd = f"wsl -d {args.wsl} -u root -- bash -c 'cat > /tmp/kbin-recipe.sh && bash /tmp/kbin-recipe.sh'"
        scp_src = f"{args.host}:{win}/kbin-{name}.tar.gz"
    else:
        kbin_out = f"/tmp/kbin-{name}.tar.gz"
        remote_cmd = "cat > /tmp/kbin-recipe.sh && bash /tmp/kbin-recipe.sh"
        scp_src = f"{args.host}:{kbin_out}"
    script = f"export KBIN_OUT='{kbin_out}'\n" + recipe_body
    print(f"[build] host={args.host} recipe={args.recipe} out={kbin_out}")
    r = subprocess.run(["ssh", args.host, remote_cmd],
                       input=script.encode(), check=False)
    if r.returncode != 0:
        sys.exit(f"リモートビルド失敗 (rc={r.returncode})。リモートの/tmp/kbin-*.logを確認")
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, f"kbin-{name}.tar.gz")
        sh(["scp", "-q", scp_src, local])
        note = args.note or f"kbin build: recipe={args.recipe} host={args.host}"
        store_file(local, name, note=note, source=f"{args.host}:{args.recipe}")
    if args.push:
        args.name = name
        cmd_push(args)


def _wsl_list():
    """wsl -l -q のディストロ一覧。Windowsのwsl.exeはUTF-16LEを吐く点に注意"""
    try:
        r = subprocess.run(["wsl", "-l", "-q"], capture_output=True)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout
    text = raw.decode("utf-16-le", errors="ignore") if b"\x00" in raw \
        else raw.decode(errors="ignore")
    return [l.strip() for l in text.splitlines() if l.strip()]


def _docker_ok():
    """dockerデーモンが応答するか（デーモン停止時はハングするのでtimeout必須）"""
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _docker_build(recipe_body, name):
    """linux/amd64のubuntu:22.04コンテナ内でレシピを実行（全OS共通のクロスビルド）

    - glibcはKaggleイメージと同じUbuntu 22.04世代に固定される
    - ホストを汚さない（CUDA toolkitはコンテナ内。aptキャッシュはnamed volumeで再利用）
    - Apple SiliconではRosetta 2エミュレーションになりビルドは数倍遅い点に注意
    """
    out_dir = tempfile.mkdtemp(prefix="kbin-docker-")
    kbin_out = f"/out/kbin-{name}.tar.gz"
    script = f"export KBIN_OUT='{kbin_out}'\n" + recipe_body
    cmd = ["docker", "run", "--rm", "-i", "--platform", "linux/amd64",
           "-v", f"{out_dir}:/out",
           "-v", "kbin-apt-cache:/var/cache/apt/archives",
           "ubuntu:22.04", "bash", "-c", "cat > /tmp/kbin-recipe.sh && bash /tmp/kbin-recipe.sh"]
    print(f"[docker] linux/amd64 ubuntu:22.04 でビルド -> {out_dir}")
    r = subprocess.run(cmd, input=script.encode())
    path = os.path.join(out_dir, f"kbin-{name}.tar.gz")
    if r.returncode != 0 or not os.path.exists(path):
        sys.exit(f"dockerビルド失敗 (rc={r.returncode})")
    return path


def _local_build(recipe_body, name, wsl_distro=None):
    """このマシン上でレシピを実行して成果物パスを返す（Linux直 or 自機WSL）"""
    if wsl_distro:
        tmpdir = os.environ.get("TEMP", r"C:\Temp")
        win_out = os.path.join(tmpdir, f"kbin-{name}.tar.gz")
        w = win_out.replace("\\", "/")
        kbin_out = f"/mnt/{w[0].lower()}{w[2:]}"  # C:/foo -> /mnt/c/foo
        cmd = ["wsl", "-d", wsl_distro, "-u", "root", "--",
               "bash", "-c", "cat > /tmp/kbin-recipe.sh && bash /tmp/kbin-recipe.sh"]
        out_path = win_out
    else:
        out_path = f"/tmp/kbin-{name}.tar.gz"
        kbin_out = out_path
        runner = ["bash", "-c", "cat > /tmp/kbin-recipe.sh && bash /tmp/kbin-recipe.sh"]
        cmd = runner if os.geteuid() == 0 else ["sudo", *runner]
    script = f"export KBIN_OUT='{kbin_out}'\n" + recipe_body
    print(f"[auto] ローカルビルド: {' '.join(cmd)} -> {out_path}")
    r = subprocess.run(cmd, input=script.encode())
    if r.returncode != 0 or not os.path.exists(out_path):
        sys.exit(f"ローカルビルド失敗 (rc={r.returncode})。/tmp/kbin-*.log を確認")
    return out_path


def _pick_backend(args):
    """auto時のバックエンド選択:
    Linux:   docker(あれば、ホストを汚さない) > local直
    Windows: wsl(既存なら最速) > docker > wsl-install試行
    Mac等:   ci (dockerはRosettaで数倍遅いので明示 --backend docker のみ)"""
    if args.backend != "auto":
        return args.backend
    if sys.platform.startswith("linux"):
        return "docker" if _docker_ok() else "local"
    if sys.platform == "win32":
        distro = os.environ.get("KBIN_WSL_DISTRO", "Ubuntu")
        distros = _wsl_list()
        if distros and any(distro.lower() in d.lower() for d in distros):
            return "wsl"
        if _docker_ok():
            return "docker"
        return "wsl-install"
    return "ci"


def cmd_auto(args):
    """実行マシンに応じて最速のビルド経路を自動選択する（--backendで固定も可）"""
    name = args.name
    be = _pick_backend(args)
    print(f"[auto] backend = {be}")
    if be == "ci":
        return cmd_ci(args)
    if be == "wsl-install":
        print("[auto] WSLもdockerも無い → wsl --install を試行（管理者権限が必要）")
        r = subprocess.run(["wsl", "--install"])
        sys.exit("WSLのインストールを開始した。再起動後に再実行を" if r.returncode == 0
                 else "WSLインストール不可 → `kbin ci --push` (GitHub Actions) を推奨")
    recipe_body = load_recipe(args.recipe)
    if be == "docker":
        if not _docker_ok():
            sys.exit("dockerデーモンが応答しない（Docker Desktop起動を確認）")
        if sys.platform == "darwin":
            print("[auto] 注意: Apple SiliconではRosettaエミュレーションでビルドが数倍遅い。"
                  "急がないなら `kbin ci` も検討を")
        path = _docker_build(recipe_body, name)
        note_src = "docker(linux/amd64)"
    elif be == "wsl":
        distro = os.environ.get("KBIN_WSL_DISTRO", "Ubuntu")
        path = _local_build(recipe_body, name, wsl_distro=distro)
        note_src = f"local WSL({distro})"
    elif be == "local":
        path = _local_build(recipe_body, name)
        note_src = "local linux"
    else:
        sys.exit(f"不明なbackend: {be}")
    store_file(path, name,
               note=args.note or f"kbin auto: {note_src} recipe={args.recipe}")
    if args.push:
        cmd_push(args)


def cmd_ci(args):
    """GitHub Actionsでビルド→artifact回収→登録。ビルドマシン不要（推奨経路）

    workflowはubuntu-22.04ランナー上でGPU無しクロスビルドする（nvccはコード生成に
    GPU不要、リンクはtoolkit同梱スタブで足りる）。所要約20-30分。
    """
    repo = os.environ.get("KBIN_REPO", "s-saga011/KaggleCli")
    sh(["gh", "workflow", "run", args.workflow, "--repo", repo])
    print("[ci] dispatch完了、run idを取得中...")
    time.sleep(8)
    rid = subprocess.check_output(
        ["gh", "run", "list", "--repo", repo, "--workflow", args.workflow,
         "--limit", "1", "--json", "databaseId", "--jq", ".[0].databaseId"],
        text=True).strip()
    print(f"[ci] run {rid} を監視（ビルド20-30分。Ctrl+Cで中断しても "
          f"`gh run watch {rid}` で再開可）")
    r = subprocess.run(["gh", "run", "watch", rid, "--repo", repo, "--exit-status"])
    if r.returncode != 0:
        sys.exit(f"CIビルド失敗: gh run view {rid} --repo {repo} --log-failed で確認")
    with tempfile.TemporaryDirectory() as tmp:
        sh(["gh", "run", "download", rid, "--repo", repo,
            "-n", args.artifact, "-D", tmp])
        files = sorted(_glob.glob(os.path.join(tmp, "**", "*.tar.gz"),
                                  recursive=True))
        if not files:
            sys.exit(f"artifact {args.artifact} にtar.gzが無い")
        note = args.note or f"kbin ci: {args.workflow} run {rid} (github-actions)"
        store_file(files[0], args.name, note=note, source=f"actions:{repo}#{rid}")
    if args.push:
        cmd_push(args)


def cmd_push(args):
    """ローカルバックアップ(latest)をKaggle datasetへ"""
    src = get_latest(args.name)
    if not src:
        sys.exit(f"バックアップが無い: {args.name}（先に kbin save/build/import）")
    user = kaggle_user()
    ds_id = f"{user}/kbin-{args.name}"
    with tempfile.TemporaryDirectory() as tmp:
        for f in os.listdir(src):
            shutil.copy2(os.path.join(src, f), tmp)
        with open(os.path.join(tmp, "dataset-metadata.json"), "w", encoding="utf-8") as f:
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
        vers = sorted(v for v in os.listdir(d) if v not in ("latest", "LATEST"))
        latest = get_latest(name) or ""
        meta_p = os.path.join(latest, "meta.json")
        note = ""
        total = 0
        if os.path.exists(meta_p):
            m = json.load(open(meta_p, encoding="utf-8"))
            total = sum(v["size"] for v in m["files"].values())
            note = m.get("note", "")
        print(f"{name}: {len(vers)}版 latest={os.path.basename(latest)} "
              f"{total / 2**20:.1f}MB {note}")


def cmd_snippet(args):
    # 注意: Kaggleはdataset作成時にtar.gz/zipを自動展開する(2026-08確認)。
    # 展開後ディレクトリ・生tar.gz・単体ファイルの3形態すべてに対応する
    user = kaggle_user()
    print(f"""# --- notebook側: kbin-{args.name} からバイナリ復元 ---
# kernel-metadata.json: "dataset_sources": ["{user}/kbin-{args.name}"]
import glob, os, subprocess
SRC = "/kaggle/input/datasets/{user}/kbin-{args.name}"
if not os.path.isdir(SRC):
    SRC = "/kaggle/input/kbin-{args.name}"  # 旧形式パス
os.makedirs("/tmp/bin", exist_ok=True)
for f in glob.glob(f"{{SRC}}/*"):
    if os.path.basename(f) == "meta.json":
        continue
    if os.path.isdir(f):  # Kaggleがtar.gzを自動展開した場合はディレクトリになる
        subprocess.run(f"cp -r '{{f}}/.' /tmp/bin/", shell=True, check=True)
    elif f.endswith((".tar.gz", ".tgz")):
        subprocess.run(["tar", "xzf", f, "-C", "/tmp/bin"], check=True)
    else:
        subprocess.run(["cp", f, "/tmp/bin/"], check=True)
subprocess.run("chmod -R +x /tmp/bin", shell=True)
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

    p = sub.add_parser("auto", help="実行マシンで最速経路を自動選択（Linux=docker/直, Win=WSL/docker, 他=ci）")
    p.add_argument("--backend", choices=["auto", "docker", "wsl", "local", "ci"],
                   default="auto", help="経路を固定したい場合に指定")
    p.add_argument("--recipe", default="llamacpp", help="recipes/<recipe>.sh")
    p.add_argument("--name", default="llamacpp-cuda", help="バックアップ名")
    p.add_argument("--note")
    p.add_argument("--workflow", default="build-llamacpp", help="ciフォールバック時のworkflow")
    p.add_argument("--artifact", default="llamacpp-bin", help="ciフォールバック時のartifact名")
    p.add_argument("--push", action="store_true", help="登録後そのままdatasetへpush")
    p.set_defaults(fn=cmd_auto)

    p = sub.add_parser("ci", help="GitHub Actionsでビルド→回収→登録（ビルドマシン不要）")
    p.add_argument("--workflow", default="build-llamacpp", help="workflowファイル名")
    p.add_argument("--artifact", default="llamacpp-bin", help="回収するartifact名")
    p.add_argument("--name", default="llamacpp-cuda", help="バックアップ名")
    p.add_argument("--note", help="メモ (省略時はrun情報を自動記録)")
    p.add_argument("--push", action="store_true", help="登録後そのままdatasetへpush")
    p.set_defaults(fn=cmd_ci)

    p = sub.add_parser("build", help="レシピをssh先でビルド→回収→登録")
    p.add_argument("recipe", help="recipes/<recipe>.sh の名前 (例: llamacpp)")
    p.add_argument("--host", required=True, help="ssh configのホスト名")
    p.add_argument("--name", help="バックアップ名 (省略時はレシピ名)")
    p.add_argument("--wsl", metavar="DISTRO", help="WindowsホストのWSL2で実行 (例: Ubuntu)")
    p.add_argument("--exchange-dir", help="--wsl時のWindows側受け渡しパス (例: C:/Users/x/work/AI/kbuild)")
    p.add_argument("--note", help="メモ (省略時は recipe/host を自動記録)")
    p.add_argument("--push", action="store_true", help="登録後そのままdatasetへpush")
    p.set_defaults(fn=cmd_build)

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
