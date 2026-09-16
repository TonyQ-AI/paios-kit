# -*- coding: utf-8 -*-
"""paios-kit 卸载器：反向摘除所有注册项；安装根目录保留（含你的数据）。

移除：
  - mcp.servers.paios 与 hooks.Stop 里的 run_hook.py 条目（config.json 先备份）
  - <zcode>/skills/ 下 paios-kit 装入的技能
  - <zcode>/speckit-cn/
  - <zcode>/scripts/silent.py（有安装时备份则还原）
  - <zcode>/AGENTS.md 的 paios-kit 管理段

不删：<root> 安装根目录（会话索引与经验原子在里面），确认不要了再手动删。
"""
import argparse
import json
import shutil
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent
ASSETS = KIT / "assets"
BEGIN_MARK = "<!-- paios-kit:begin (managed by paios-kit, do not edit inside) -->"
END_MARK = "<!-- paios-kit:end -->"


def log(msg):
    print(msg, flush=True)


def clean_config(zcode):
    cfg_path = zcode / "cli" / "config.json"
    if not cfg_path.exists():
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    changed = []
    servers = (cfg.get("mcp") or {}).get("servers") or {}
    if "paios" in servers:
        del servers["paios"]
        changed.append("mcp.servers.paios removed")
    events = (cfg.get("hooks") or {}).get("events") or {}
    for ev, groups in events.items():
        if not isinstance(groups, list):
            continue
        for gi, group in enumerate(groups):
            entries = group.get("hooks") or []
            kept = [e for e in entries
                    if "run_hook.py" not in json.dumps(e.get("args", []))]
            if len(kept) != len(entries):
                group["hooks"] = kept
                changed.append("hooks.%s run_hook.py removed" % ev)
        events[ev] = [g for g in groups if g.get("hooks")]
    if changed:
        shutil.copy2(cfg_path, cfg_path.with_suffix(
            ".json.bak-paioskit-uninstall-" + time.strftime("%Y%m%d")))
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8", newline="\n")
    for c in changed:
        log("[config] " + c)


def clean_skills(zcode):
    src_root = ASSETS / "skills"
    dst_root = zcode / "skills"
    if not dst_root.exists():
        return
    for n in sorted(p.name for p in src_root.iterdir() if p.is_dir()):
        dst = dst_root / n
        if dst.exists():
            shutil.rmtree(dst)
            log("[skills] removed " + n)


def clean_speckit(zcode):
    dst = zcode / "speckit-cn"
    if dst.exists():
        shutil.rmtree(dst)
        log("[speckit-cn] removed " + str(dst))


def clean_silent(zcode):
    dst = zcode / "scripts" / "silent.py"
    if not dst.exists():
        return
    baks = sorted(dst.parent.glob("silent.py.bak-*"))
    if baks:
        shutil.copy2(baks[-1], dst)
        log("[silent.py] restored from %s" % baks[-1].name)
    else:
        dst.unlink()
        log("[silent.py] removed")


def clean_agents_md(zcode):
    ag = zcode / "AGENTS.md"
    if not ag.exists():
        return
    old = ag.read_text(encoding="utf-8")
    if BEGIN_MARK in old and END_MARK in old:
        pre = old.split(BEGIN_MARK)[0].rstrip("\n")
        post = old.split(END_MARK)[-1].lstrip("\n")
        new = (pre + "\n" + post).strip("\n")
        ag.write_text((new + "\n") if new else "", encoding="utf-8", newline="\n")
        log("[AGENTS.md] managed section removed")


def main():
    ap = argparse.ArgumentParser(description="paios-kit uninstaller")
    ap.add_argument("--zcode-dir", default=str(Path.home() / ".zcode"))
    args = ap.parse_args()
    zcode = Path(args.zcode_dir).resolve()
    log("=== paios-kit uninstall (zcode dir: %s) ===" % zcode)
    clean_config(zcode)
    clean_skills(zcode)
    clean_speckit(zcode)
    clean_silent(zcode)
    clean_agents_md(zcode)
    log("=== done. install root kept (your data) — delete it manually if unwanted ===")


if __name__ == "__main__":
    main()
