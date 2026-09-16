# -*- coding: utf-8 -*-
"""paios-kit 一键安装器。

把记忆基座程序、技能、静默执行器装入本机，并合并注册进 ZCode 配置：
  - PAIOS 程序      -> <root>（默认 %USERPROFILE%\\paios）
  - 12 个技能       -> <zcode>/skills/
  - SpecKit-CN 副本 -> <zcode>/speckit-cn/
  - silent.py       -> <zcode>/scripts/
  - MCP + Stop hook -> <zcode>/cli/config.json（合并写入，绝不整文件覆盖）
  - 使用规则段      -> <zcode>/AGENTS.md（管理标记包裹，幂等替换）

幂等：重跑即升级；用户数据（data/、knowledge/atoms）永不被覆盖。

用法示例：
  python install.py
  python install.py --root D:\\tools\\paios --port 8743
  python install.py --workspace-roots "D:\\works,E:\\projects"
  python install.py --print-plan
"""
import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
ASSETS = KIT / "assets"

BEGIN_MARK = "<!-- paios-kit:begin (managed by paios-kit, do not edit inside) -->"
END_MARK = "<!-- paios-kit:end -->"

SNIPPET_FILES = ["agents-memory.md.tmpl", "agents-windows.md.tmpl",
                 "agents-newproject.md.tmpl"]
DEFAULT_PORT = 8643


def log(msg):
    print(msg, flush=True)


def render(text, ctx):
    for k, v in ctx.items():
        text = text.replace("{{%s}}" % k, v)
    return text


def find_free_port(start):
    port = start
    while port < start + 50:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("no free port in [%d, %d)" % (start, start + 50))


def install_paios_core(root, ctx, plan=False):
    src = ASSETS / "paios"
    log("[core] program -> %s" % root)
    if plan:
        return
    shutil.copytree(src, root, dirs_exist_ok=True)


def install_knowledge_seed(root, plan=False):
    dst = root / "knowledge"
    if dst.exists():
        log("[knowledge] existing knowledge/ kept (user data protected)")
        return
    log("[knowledge] seed empty knowledge base (INDEX + format example)")
    if plan:
        return
    shutil.copytree(ASSETS / "knowledge", dst)


def init_data(root, workspace_roots, plan=False):
    data = root / "data"
    log("[data] init %s" % data)
    if plan:
        return
    (data / "models").mkdir(parents=True, exist_ok=True)
    (root / "backup").mkdir(parents=True, exist_ok=True)
    wr = data / "workspace-roots.json"
    if not wr.exists():
        wr.write_text(json.dumps(workspace_roots, ensure_ascii=False, indent=1),
                      encoding="utf-8", newline="\n")


def check_python_and_deps(root, skip_deps, plan=False):
    log("[deps] python %s" % sys.version.split()[0])
    if sys.version_info < (3, 10):
        log("  ! WARNING: Python >= 3.10 required (dev env: 3.11)")
    if plan or skip_deps:
        return
    try:
        import fastembed  # noqa: F401
        log("[deps] fastembed already installed")
    except ImportError:
        req = str(KIT / "requirements.txt")
        log("[deps] pip install -r requirements.txt ...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req],
                           check=False)
        if r.returncode != 0:
            log("[deps] default index failed -> retry with Tsinghua mirror ...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", req,
                            "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                           check=False)


def install_skills(zcode, ctx, plan=False):
    src_root = ASSETS / "skills"
    dst_root = zcode / "skills"
    names = sorted(p.name for p in src_root.iterdir() if p.is_dir())
    log("[skills] %d skills -> %s" % (len(names), dst_root))
    if plan:
        for n in names:
            log("  - " + n)
        return
    dst_root.mkdir(parents=True, exist_ok=True)
    for n in names:
        dst = dst_root / n
        if dst.exists():
            shutil.rmtree(dst)
        for p in (src_root / n).rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(src_root / n)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if p.suffix in (".md", ".txt", ".json"):
                out.write_text(render(p.read_text(encoding="utf-8"), ctx),
                               encoding="utf-8", newline="\n")
            else:
                shutil.copy2(p, out)
        log("  + " + n)


def install_speckit(zcode, plan=False):
    src, dst = ASSETS / "speckit-cn", zcode / "speckit-cn"
    log("[speckit-cn] master copy -> %s" % dst)
    if plan:
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def install_silent(zcode, plan=False):
    src, dst = ASSETS / "scripts" / "silent.py", zcode / "scripts" / "silent.py"
    log("[silent.py] -> %s" % dst)
    if plan:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        bak = dst.with_suffix(".py.bak-" + time.strftime("%Y%m%d"))
        shutil.copy2(dst, bak)
        log("  (existing backed up to %s)" % bak.name)
    shutil.copy2(src, dst)


def merge_config(zcode, root, register_hook=True, plan=False):
    cfg_path = zcode / "cli" / "config.json"
    server = {"type": "stdio", "command": "python",
              "args": [str(root / "mcp_entry.py")],
              "enabled": True, "timeoutMs": 15000}
    hook = {"type": "process", "command": "python",
            "args": [str(root / "run_hook.py")], "timeoutMs": 10000}
    log("[config] merge -> %s" % cfg_path)
    if plan:
        log("  mcp.servers.paios = stdio %s" % server["args"][0])
        if register_hook:
            log("  hooks.Stop += run_hook.py")
        return
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        shutil.copy2(cfg_path, cfg_path.with_suffix(".json.bak-paioskit"))
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        cfg = {}
    # --- mcp.servers.paios（upsert，不动其他 server）---
    mcp = cfg.setdefault("mcp", {})
    servers = mcp.setdefault("servers", {})
    overwritten = "paios" in servers
    servers["paios"] = server
    # --- hooks.Stop 里的 paios 条目（upsert，不动用户已有 hook）---
    hook_note = "skipped (--no-hook)"
    if register_hook:
        hooks = cfg.setdefault("hooks", {})
        if "enabled" not in hooks:
            hooks["enabled"] = True
        events = hooks.setdefault("events", {})
        stop_list = events.setdefault("Stop", [])
        if not stop_list:
            stop_list.append({"hooks": []})
        group = stop_list[0]
        entries = group.setdefault("hooks", [])
        replaced = False
        for i, e in enumerate(entries):
            if "run_hook.py" in json.dumps(e.get("args", [])):
                entries[i] = hook
                replaced = True
                break
        if not replaced:
            entries.append(hook)
        hook_note = "%s run_hook.py" % ("updated" if replaced else "added")
        if not hooks.get("enabled"):
            log("  ! WARNING: hooks.enabled=false in your config -> Stop hook will NOT run")
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")
    log("  mcp.servers.paios %s" % ("updated" if overwritten else "added"))
    log("  hooks.Stop %s" % hook_note)


def merge_agents_md(zcode, ctx, include_windows, plan=False):
    ag = zcode / "AGENTS.md"
    log("[AGENTS.md] managed section -> %s" % ag)
    snippets = []
    for f in SNIPPET_FILES:
        if f == "agents-windows.md.tmpl" and not include_windows:
            continue
        snippets.append(render((ASSETS / "snippets" / f).read_text(
            encoding="utf-8"), ctx))
    block = BEGIN_MARK + "\n\n" + "\n\n---\n\n".join(snippets) + "\n\n" + END_MARK
    if plan:
        log("  %d snippet(s): %s" % (len(snippets), ", ".join(SNIPPET_FILES)))
        return
    old = ag.read_text(encoding="utf-8") if ag.exists() else ""
    if BEGIN_MARK in old and END_MARK in old:
        pre = old.split(BEGIN_MARK)[0].rstrip("\n")
        post = old.split(END_MARK)[-1].lstrip("\n")
        new = (pre + "\n\n" + block + "\n" + post).strip("\n") + "\n"
        action = "replaced"
    elif old.strip():
        new = old.rstrip("\n") + "\n\n" + block + "\n"
        action = "appended"
    else:
        new = block + "\n"
        action = "created"
    ag.write_text(new, encoding="utf-8", newline="\n")
    log("  %s (%d snippet section(s))" % (action, len(snippets)))


def gen_launcher(root, ctx, plan=False):
    dst = root / "launch_ui.vbs"
    log("[launcher] %s" % dst)
    if plan:
        return
    tpl = (KIT / "templates" / "launch_ui.vbs.tmpl").read_text(encoding="ascii")
    dst.write_text(render(tpl, ctx), encoding="ascii", newline="\r\n")


def verify_import(root, plan=False):
    if plan:
        return
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s'); import paios; "
         "import paios.mcp, paios.indexer, paios.atoms; print('import-ok')"
         % root],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0 and "import-ok" in (r.stdout or ""):
        log("[verify] paios package imports OK")
    else:
        log("[verify] FAILED:\n%s%s" % (r.stdout, r.stderr))


def warm_model(root, skip, plan=False):
    """预下载语义模型（约 90MB，走 hf-mirror），失败不阻塞安装。"""
    if plan or skip:
        return
    log("[model] warming bge-small-zh-v1.5 (first run downloads ~90MB)...")
    code = ("import sys; sys.path.insert(0, r'{root}'); "
            "from paios import vectors; v = vectors.embed_texts(['warmup']); "
            "print('model-ready dim=', len(v[0]))").format(root=root)
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
        if "model-ready" in (r.stdout or ""):
            log("[model] " + r.stdout.strip().splitlines()[-1])
        else:
            log("[model] warmup skipped/failed (fine — downloads on first"
                " semantic search):\n%s" % (r.stderr or r.stdout)[-400:])
    except Exception as e:
        log("[model] warmup skipped (%s)" % e)


def main():
    ap = argparse.ArgumentParser(description="paios-kit installer")
    ap.add_argument("--root", default=str(Path.home() / "paios"))
    ap.add_argument("--zcode-dir", default=str(Path.home() / ".zcode"))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--workspace-roots", default="",
                    help='comma separated, e.g. "D:\\works,E:\\projects"')
    ap.add_argument("--no-hook", action="store_true",
                    help="do not register the Stop hook")
    ap.add_argument("--no-windows-snippet", action="store_true",
                    help="skip the Windows pitfalls section in AGENTS.md")
    ap.add_argument("--skip-deps", action="store_true")
    ap.add_argument("--no-warm", action="store_true",
                    help="skip model warm-up download")
    ap.add_argument("--print-plan", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    zcode = Path(args.zcode_dir).resolve()
    port = find_free_port(args.port)
    if port != args.port:
        log("[port] %d busy -> using %d" % (args.port, port))
    pyw = Path(sys.executable).parent / "pythonw.exe"
    ctx = {
        "PAIOS_ROOT": str(root),
        "ZCODE_DIR": str(zcode),
        "UI_PORT": str(port),
        "UI_URL": "http://127.0.0.1:%d" % port,
        "PYW": str(pyw) if pyw.exists() else "pythonw",
    }
    workspace_roots = [w.strip() for w in args.workspace_roots.split(",") if w.strip()]

    log("=== paios-kit install ===")
    log("root     : %s" % root)
    log("zcode dir: %s" % zcode)
    log("ui       : %s" % ctx["UI_URL"])
    log("pythonw  : %s" % ctx["PYW"])
    log("")
    plan = args.print_plan

    check_python_and_deps(root, args.skip_deps, plan)
    install_paios_core(root, ctx, plan)
    install_knowledge_seed(root, plan)
    init_data(root, workspace_roots, plan)
    install_skills(zcode, ctx, plan)
    install_speckit(zcode, plan)
    install_silent(zcode, plan)
    merge_config(zcode, root, register_hook=not args.no_hook, plan=plan)
    if args.no_hook:
        log("[hook] Stop hook registration skipped (--no-hook)")
    merge_agents_md(zcode, ctx, not args.no_windows_snippet, plan)
    gen_launcher(root, ctx, plan)
    verify_import(root, plan)
    warm_model(root, args.no_warm or args.skip_deps, plan)

    log("")
    if plan:
        log("=== plan only, nothing written ===")
    else:
        log("=== done ===")
        log("1) double-click %s to open the memory-base UI" % (root / "launch_ui.vbs"))
        log("2) fully restart ZCode; new sessions can use paios_* tools and"
            " $new-project / $speckit-cn-* / $confirm-vision / $knowledge-atom skills")
        log("3) first semantic dedupe run downloads the model (~90MB) from"
            " hf-mirror.com automatically")


if __name__ == "__main__":
    main()
