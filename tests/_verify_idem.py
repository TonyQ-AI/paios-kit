# -*- coding: utf-8 -*-
"""paios-kit 幂等重装 + 卸载回滚验证。"""
import json
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SB = KIT / ".sandbox"
ROOT = SB / "paios"
Z = SB / "home" / ".zcode"

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail and not cond else ""))
    if not cond:
        fails.append(name)


def run(cmd):
    r = subprocess.run([sys.executable] + cmd, cwd=str(KIT),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-800:])
        print(r.stderr[-800:])
    return r


# --- 1. 幂等：第三次安装（覆盖升级）---
run(["install.py", "--root", str(ROOT), "--zcode-dir", str(Z), "--skip-deps"])
ag = (Z / "AGENTS.md").read_text(encoding="utf-8")
check("idempotent: managed block still once",
      ag.count("paios-kit:begin") == 1 and ag.count("paios-kit:end") == 1)
cfg = json.loads((Z / "cli" / "config.json").read_text(encoding="utf-8"))
stop_hooks = (((cfg.get("hooks") or {}).get("events") or {}).get("Stop") or [{}])[0].get("hooks", [])
check("idempotent: single paios hook entry",
      sum(1 for e in stop_hooks if "run_hook.py" in json.dumps(e.get("args", []))) == 1)
check("idempotent: single paios server",
      json.dumps(cfg["mcp"]["servers"]).count("mcp_entry.py") == 1)

# --- 2. 卸载 ---
run(["uninstall.py", "--zcode-dir", str(Z)])
cfg2 = json.loads((Z / "cli" / "config.json").read_text(encoding="utf-8"))
check("uninstall: paios server gone", "paios" not in cfg2["mcp"]["servers"])
check("uninstall: github server kept", "github" in cfg2["mcp"]["servers"])
stop2 = (((cfg2.get("hooks") or {}).get("events") or {}).get("Stop") or [])
check("uninstall: run_hook gone", "run_hook.py" not in json.dumps(stop2))
check("uninstall: user action_guard kept", "action_guard.py" in json.dumps(stop2))
check("uninstall: skills removed",
      not (Z / "skills" / "new-project").exists())
check("uninstall: speckit-cn removed", not (Z / "speckit-cn").exists())
ag2 = (Z / "AGENTS.md").read_text(encoding="utf-8")
check("uninstall: managed section removed",
      "paios-kit:begin" not in ag2 and "# My personal rules" in ag2)
# 多轮安装会产生 silent.py.bak-*（视为用户原文件）→ 卸载=还原备份；单轮干净场景=直接删除
silent = Z / "scripts" / "silent.py"
baks = sorted((Z / "scripts").glob("silent.py.bak-*")) if silent.exists() else []
check("uninstall: silent.py removed-or-restored-to-user-version",
      not silent.exists() or (baks and silent.read_bytes() == baks[-1].read_bytes()))

# --- 3. 卸载后重装一遍（模拟对方反悔）---
run(["install.py", "--root", str(ROOT), "--zcode-dir", str(Z), "--skip-deps"])
sys.path.insert(0, str(ROOT))
from paios import mcp  # noqa: E402
r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
check("reinstall works", r and len(r["result"]["tools"]) == 10)

print()
print("RESULT:", "ALL PASS" if not fails else "FAILED: %s" % fails)
sys.exit(1 if fails else 0)
