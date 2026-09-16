# -*- coding: utf-8 -*-
"""PAIOS MCP stdio server：记忆基座查询 + 知识层读写（BLUEPRINT 12.2）。

全部工具 paios_ 前缀，纯标准库实现。
协议：stdin/stdout 上逐行 JSON-RPC（MCP 标准帧），纯标准库。
"""
import json
import sys

from . import atoms, candidates, indexer

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "paios", "version": "1.0.0"}

TOOLS = [
    {
        "name": "paios_search_sessions",
        "description": (
            "搜索用户的 ZCode 对话历史索引（记忆基座·归档层）。"
            "按关键词匹配标题/摘要/项目名/涉及文件路径，返回会话列表"
            "（含归属项目、时间、改动概况）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，如：录屏图标"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "paios_recent_sessions",
        "description": "列出最近活跃的 ZCode 会话及其归属项目。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 8},
                "project": {"type": "string", "description": "只看某个项目（可选）"},
            },
        },
    },
    {
        "name": "paios_get_session",
        "description": "获取某个会话的详细信息：摘要、涉及文件清单、消息/工具数、起止时间。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "paios_list_projects",
        "description": "列出索引中全部项目及各自会话数、最近活跃时间。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "paios_search_atoms",
        "description": (
            "检索经验原子库（知识层）。任务开工前应按任务关键词调用本工具，"
            "命中即读对应原子（避旧坑、守约束）；未命中不要注入任何内容。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "2~3 个关键词，空格分隔"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "paios_save_atom",
        "description": (
            "沉淀一条经验原子（7 字段，先过写入门禁：适用边界必填且具体、"
            "AI错误没有就写「无」、同标题查重、词面相似软告警）。"
            "写入前必须先用 paios_search_atoms 检索关键词并阅读候选原子，"
            "判断语义是否重复：重复则勿入库（可考虑更新既有原子）；"
            "确属不同角度/结论才提交，若被相似告警拦截且确认非重复，带 force=true。"
            "默认存为 draft 草稿，转正是人工动作（Web UI 或 paios_promote_atom）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "一句话标题"},
                "type": {"type": "string",
                         "enum": ["踩坑", "方案", "约束", "决策", "工作流"]},
                "body": {"type": "string",
                         "description": "7 字段正文：任务背景/人的判断/判断理由/AI错误/修正方案/最终结果/适用边界，开头可加【一句话结论】"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "project": {"type": "string", "description": "所属项目（可选）"},
                "source": {"type": "string",
                           "description": "zcode-session | 豆包 | 其他，默认 zcode-session"},
                "source_ref": {"type": "string",
                               "description": "溯源：sess_xxx 或 REQ 单号等"},
                "status": {"type": "string", "enum": ["draft", "active"],
                           "description": "默认 draft；active 仅限用户明确要求"},
                "force": {"type": "boolean",
                          "description": "被相似告警拦截且确认非重复时置 true 越过软门禁"},
                "reviewed": {"type": "array", "items": {"type": "string"},
                             "description": "查重评审证据：已通过 paios_search_atoms 检索并阅读、判断非重复的原子 ID 列表"},
                "session_id": {"type": "string",
                               "description": "来源会话（可选，存后自动清该会话的候选标记）"},
            },
            "required": ["title", "type", "body"],
        },
    },
    {
        "name": "paios_promote_atom",
        "description": "原子状态流转：draft→active（转正）、→deprecated（废弃）、deprecated→active（恢复）。仅按用户明确指示调用。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "原子 ID，如 A-0002"},
                "status": {"type": "string", "enum": ["draft", "active", "deprecated"]},
            },
            "required": ["id", "status"],
        },
    },
    {
        "name": "paios_get_session_md",
        "description": "导出某会话完整原文为 Markdown（清洗注入块、渲染工具调用）。用于提炼经验原子时读取原始素材。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "paios_list_candidates",
        "description": "列出待提炼的候选会话（报错修复密集、大规模生产等特征）。提炼完成后用 paios_save_atom 的 session_id 参数自动清除标记。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    {
        "name": "paios_review_drafts",
        "description": (
            "按用户授权规则批量审核草稿原子（红线：转正授权 + 抽查 + 可回滚）。"
            "AI 先定归属：独立问题+明确边界+跨项目可复用→promote（转正）；"
            "同质重复→并入既有 active 原子；纯项目私有/无价值→deprecate（废弃）。"
            "调用时传 decisions=[{id, action: promote|deprecate}]。"
            "转正后返回清单供用户抽查，任何 active 原子可一键回滚。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "action": {"type": "string", "enum": ["promote", "deprecate"]},
                        },
                        "required": ["id", "action"],
                    },
                },
            },
            "required": ["decisions"],
        },
    },
]


def _fmt_session_row(s, with_files=False):
    from datetime import datetime
    t = datetime.fromtimestamp(s["t_updated"] / 1000).strftime("%Y-%m-%d %H:%M") \
        if s.get("t_updated") else "?"
    line = (f"- [{t}] {s['title']} ｜ 项目: {s['project_key']}"
            f"({'低置信' if s['confidence'] == 'low' else s['confidence']})"
            f" ｜ 会话ID: {s['session_id']}")
    if s.get("n_file_ops"):
        line += f" ｜ {s['n_file_ops']} 次文件操作 +{s['additions']}/-{s['deletions']}"
    if with_files and s.get("files"):
        top = list(s["files"].items())[:5]
        line += "\n  主要文件: " + ", ".join(f"{k}×{v}" for k, v in top)
    return line


def _fmt_atom(meta, body=None):
    import re as _re
    tags = meta.get("tags")
    tags = ",".join(tags) if isinstance(tags, list) else (tags or "")
    line = (f"- {meta.get('id')} [{meta.get('status')}] {meta.get('title')}"
            f"（{meta.get('type')}｜标签:{tags}｜项目:{meta.get('project') or '-'}）")
    if body:
        m = _re.search(r"【一句话结论】\s*(.+)", body)
        if m:
            line += f"\n  结论: {m.group(1).strip()}"
        line += f"\n  溯源: {meta.get('source_ref') or '-'}"
    return line


def call_tool(name, args):
    args = args or {}
    if name == "paios_search_sessions":
        q = str(args.get("query") or "")
        indexer.sync()  # 确保新鲜
        rows = indexer.query_sessions(q=q, limit=int(args.get("limit", 10)))
        if not rows:
            return f"没有匹配 “{q}” 的会话。可以试试其他关键词。"
        lines = [f"找到 {len(rows)} 条与 “{q}” 相关的会话："] + \
                [_fmt_session_row(r, with_files=True) for r in rows]
        return "\n".join(lines)

    if name == "paios_recent_sessions":
        indexer.sync()
        rows = indexer.query_sessions(project=args.get("project", ""),
                                      limit=int(args.get("limit", 8)))
        if not rows:
            return "索引为空。"
        return "\n".join([f"最近 {len(rows)} 个会话："] +
                         [_fmt_session_row(r) for r in rows])

    if name == "paios_get_session":
        sid = args.get("session_id", "")
        rows = indexer.query_sessions(limit=5000)
        hit = next((r for r in rows if r["session_id"] == sid), None)
        if not hit:
            cand = [r for r in rows if r["session_id"].find(sid) >= 0 and sid]
            hit = cand[0] if len(cand) == 1 else None
        if not hit:
            return f"找不到会话 {sid}。请先用 paios_search_sessions 找到确切 ID。"
        files = "\n".join(f"  - {k} ×{v}" for k, v in
                          sorted(hit["files"].items(), key=lambda x: -x[1]))
        import datetime as _dt
        tc = _dt.datetime.fromtimestamp(hit["t_created"] / 1000).strftime("%Y-%m-%d %H:%M")
        tu = _dt.datetime.fromtimestamp(hit["t_updated"] / 1000).strftime("%Y-%m-%d %H:%M")
        return (f"标题: {hit['title']}\n项目: {hit['project_key']}"
                f" ({hit['project_path'] or '路径未知'})\n摘要: {hit['summary']}\n"
                f"时间: {tc} → {tu}\n规模: {hit['n_messages']} 消息 / "
                f"{hit['n_tools']} 工具调用 / {hit['n_file_ops']} 文件操作 "
                f"+{hit['additions']}/-{hit['deletions']}\n"
                f"会话ID: {hit['session_id']}"
                + (f"\n涉及文件:\n{files}" if files else ""))

    if name == "paios_list_projects":
        ps = indexer.query_projects()
        return "索引中的项目：\n" + "\n".join(
            f"- {p['project_key']}: {p['sessions']} 个会话，"
            f"最近 {__import__('datetime').datetime.fromtimestamp(p['last_active']/1000).strftime('%m-%d %H:%M')}"
            for p in ps)

    if name == "paios_search_atoms":
        hits = atoms.search_atoms(str(args.get("query") or ""),
                                  int(args.get("limit", 5)))
        if not hits:
            return "原子库无命中（未命中不注入，直接干活即可）。"
        # 检索层 = skim 层：标题+结论+边界即决策依据（边界防误用，必须露面），
        # 溯源/背景/操作细节留在精读层（省 token）
        lines = [f"命中 {len(hits)} 条（按相关性排序；结论+边界已够行动就以此为准，需要操作细节才读全文，最多精读 1~2 条）："]
        for m, b in hits:
            line = _fmt_atom(m, b)
            line = "\n".join(l for l in line.split("\n") if not l.startswith("  溯源:"))
            bnd = atoms.extract_fields(b).get("适用边界", "").strip()
            if bnd:
                line += "\n  边界: " + bnd[:80] + ("…" if len(bnd) > 80 else "")
            lines.append(line)
        return "\n".join(lines)

    if name == "paios_save_atom":
        ok, result = atoms.save_atom(
            title=str(args.get("title") or ""),
            atom_type=str(args.get("type") or ""),
            body=str(args.get("body") or ""),
            tags=args.get("tags") or [],
            project=str(args.get("project") or ""),
            source=str(args.get("source") or "zcode-session"),
            source_ref=str(args.get("source_ref") or ""),
            status=str(args.get("status") or "draft"),
            force=bool(args.get("force")),
            reviewed=args.get("reviewed") or [],
        )
        if not ok:
            return "写入门禁拒绝：\n" + "\n".join("- " + r for r in result)
        if args.get("session_id"):
            candidates.clear(str(args["session_id"]))
        return (f"已保存 {result}（status={args.get('status') or 'draft'}）。"
                + ("草稿不参与检索，转正请在 Web UI 操作或调 paios_promote_atom。"
                   if (args.get("status") or "draft") == "draft" else ""))

    if name == "paios_promote_atom":
        ok, result = atoms.set_status(str(args.get("id") or ""),
                                      str(args.get("status") or ""))
        return ("已更新 %s → %s" % (result, args.get("status"))
                if ok else "失败：%s" % result)

    if name == "paios_get_session_md":
        md = indexer.export_session_md(str(args.get("session_id") or ""))
        return md if md else "会话不存在或无内容。"

    if name == "paios_list_candidates":
        rows = candidates.list_candidates(int(args.get("limit", 20)))
        if not rows:
            return "候选队列为空。"
        import datetime as _dt
        lines = [f"待提炼候选 {len(rows)} 条："]
        for sid, title, proj, reason, tu in rows:
            t = _dt.datetime.fromtimestamp(tu / 1000).strftime("%m-%d %H:%M") if tu else "?"
            lines.append(f"- [{t}] {title} ｜ 项目: {proj} ｜ 特征: {reason} ｜ {sid}")
        return "\n".join(lines)

    if name == "paios_review_drafts":
        decisions = list(args.get("decisions") or [])
        if not decisions:
            return "未传入 decisions。"
        promoted, deprecated, skipped = [], [], []
        for d in decisions:
            aid = str(d.get("id") or "")
            action = str(d.get("action") or "")
            if not aid or action not in ("promote", "deprecate"):
                skipped.append(aid + ":" + (action or "无action"))
                continue
            ok, r = atoms.set_status(aid, "active" if action == "promote" else "deprecated")
            if not ok:
                skipped.append(aid + ":" + str(r))
            elif action == "promote":
                promoted.append(aid)
            else:
                deprecated.append(aid)
        lines = []
        if promoted:
            lines.append("已转正 %d 条（供抽查，可用 deprecated→active 回滚）：" % len(promoted))
            lines += ["  - " + a for a in promoted]
        if deprecated:
            lines.append("已废弃 %d 条（无跨项目价值）：" % len(deprecated))
            lines += ["  - " + a for a in deprecated]
        if skipped:
            lines.append("跳过/失败 %d 条：" % len(skipped))
            lines += ["  - " + s for s in skipped]
        return "\n".join(lines) if lines else "无操作。"

    raise KeyError(name)


# ------------------------------------------------------------ JSON-RPC 骨架

def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        pv = msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION)
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": pv,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            text = call_tool(p.get("name"), p.get("arguments"))
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"查询失败: {e}"}],
                "isError": True}}
    if mid is not None:  # 未知请求方法；通知(notifications/*)一律不回
        return {"jsonrpc": "2.0", "id": mid, "error":
                {"code": -32601, "message": f"unknown method: {method}"}}
    return None


def main():
    # 关键：stdout 只能输出协议帧，业务日志全走 stderr
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        try:
            resp = handle(msg)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
