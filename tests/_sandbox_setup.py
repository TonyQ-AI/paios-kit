# -*- coding: utf-8 -*-
"""搭建 paios-kit 沙盒测试环境（假 .zcode，含用户已有配置）。"""
import json
from pathlib import Path

sb = Path(__file__).resolve().parent.parent / ".sandbox"
z = sb / "home" / ".zcode"
(z / "cli").mkdir(parents=True, exist_ok=True)
(z / "scripts").mkdir(parents=True, exist_ok=True)

cfg = {
    "mcp": {"servers": {"github": {
        "type": "http", "url": "https://example.invalid/mcp",
        "headers": {"Authorization": "dummy"}}}},
    "hooks": {"enabled": True, "events": {
        "PreToolUse": [{"matcher": "Write|Edit", "hooks": [
            {"type": "process", "command": "python",
             "args": ["C:\\x\\spec_gate.py"], "timeoutMs": 5000}]}],
        "Stop": [{"hooks": [
            {"type": "process", "command": "python",
             "args": ["C:\\x\\action_guard.py"], "timeoutMs": 10000}]}]}}}

(z / "cli" / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
(z / "AGENTS.md").write_text("# My personal rules\n- be concise\n", encoding="utf-8")
print("sandbox ready at", z)
