# -*- coding: utf-8 -*-
"""paios-kit 沙盒安装验收。"""
import json
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


# 1. root 结构
check("core: mcp_entry.py", (ROOT / "mcp_entry.py").is_file())
check("core: webui.py + run_hook.py", (ROOT / "webui.py").is_file() and (ROOT / "run_hook.py").is_file())
check("crop: workflow.py excluded", not (ROOT / "paios" / "workflow.py").exists())
check("core: paios pkg + static", (ROOT / "paios" / "webserver.py").is_file())
check("knowledge seed", (ROOT / "knowledge" / "INDEX.md").is_file()
      and (ROOT / "knowledge" / "atoms").is_dir())
check("workspace-roots.json empty list",
      json.loads((ROOT / "data" / "workspace-roots.json").read_text(encoding="utf-8")) == [])
check("launcher rendered", "{{" not in (ROOT / "launch_ui.vbs").read_text(encoding="ascii"))

# 2. skills
ASSETS = KIT / "assets" / "skills"
skill_names = sorted(p.name for p in ASSETS.iterdir() if p.is_dir())
installed = sorted(p.name for p in (Z / "skills").iterdir() if p.is_dir())
check("12 skills installed", skill_names == installed and len(installed) == 12,
      str(installed))
leftover = [str(p) for p in (Z / "skills").rglob("*.md")
            if "{{" in p.read_text(encoding="utf-8")]
check("no unrendered placeholders in skills", not leftover, str(leftover))
init_skill = (Z / "skills" / "speckit-cn-init" / "SKILL.md").read_text(encoding="utf-8")
check("skill path rendered to sandbox root", "sandbox" in init_skill.lower()
      and "{{PAIOS_ROOT}}" not in init_skill and "{{ZCODE_DIR}}" not in init_skill)

# 3. speckit-cn + silent.py
check("speckit-cn copied", (Z / "speckit-cn" / "claude" / ".specify").is_dir())
check("silent.py installed", (Z / "scripts" / "silent.py").is_file())

# 4. config.json 合并
cfg = json.loads((Z / "cli" / "config.json").read_text(encoding="utf-8"))
srv = cfg.get("mcp", {}).get("servers", {})
check("user server kept", "github" in srv)
check("paios server added", "paios" in srv
      and srv["paios"]["args"] == [str(ROOT / "mcp_entry.py")])
stop_hooks = (((cfg.get("hooks") or {}).get("events") or {}).get("Stop") or [{}])[0].get("hooks", [])
args_joined = json.dumps(stop_hooks)
check("user hook kept (action_guard)", "action_guard.py" in args_joined)
check("paios hook added (run_hook)", "run_hook.py" in args_joined)
check("user PreToolUse untouched", "spec_gate.py" in json.dumps(cfg["hooks"]["events"].get("PreToolUse", [])))

# 5. AGENTS.md
ag = (Z / "AGENTS.md").read_text(encoding="utf-8")
check("agents: personal content kept", "# My personal rules" in ag and "be concise" in ag)
check("agents: managed block once", ag.count("paios-kit:begin") == 1 and ag.count("paios-kit:end") == 1)
check("agents: three sections", all(s in ag for s in
      ["记忆基座", "Windows shell 避坑手册", "新项目启动规则"]))
check("agents: no leftover placeholders", "{{" not in ag)

# 6. MCP 冒烟：无源库场景（沙盒没有 db.sqlite）
sys.path.insert(0, str(ROOT))
from paios import mcp  # noqa: E402
r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
check("mcp tools/list ok", r and len(r["result"]["tools"]) == 10)
r = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "paios_search_sessions",
                           "arguments": {"query": "test"}}})
txt = r["result"]["content"][0]["text"]
check("mcp search without source db degrades gracefully",
      not r["result"]["isError"] or "失败" in txt, txt[:120])
r = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "paios_search_atoms",
                           "arguments": {"query": "格式"}}})
txt = r["result"]["content"][0]["text"]
check("mcp search_atoms on empty lib", "无命中" in txt, txt[:120])

print()
print("RESULT:", "ALL PASS" if not fails else "FAILED: %s" % fails)
sys.exit(1 if fails else 0)
