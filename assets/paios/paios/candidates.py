# -*- coding: utf-8 -*-
"""候选沉淀标记：识别「值得提炼经验」的会话（BLUEPRINT 3.3）。

启发式（不求全，求召回后人工筛）：
- 同文件返工 ≥4 次（反复改同一处 = 踩坑修复现场）
- 大规模生产会话（≥50 消息 且 ≥10 次文件操作）
- 高强度改动（±300 行 且 ≥8 次文件操作）
"""
import json
import re
import sqlite3

from . import INDEX_DB

_ERR_RE = re.compile(r"(Traceback|Error|Exception|FAILED|assert|失败|报错|错误|无法|找不到|not found|Cannot read|undefined)", re.I)


def _ensure_columns(con):
    for col in ("worth_extracting INTEGER DEFAULT 0",
                "worth_reason TEXT DEFAULT ''",
                "extracted INTEGER DEFAULT 0",
                "archived INTEGER DEFAULT 0",
                "trashed INTEGER DEFAULT 0"):
        try:
            con.execute("ALTER TABLE sessions ADD COLUMN %s" % col)
        except Exception:
            pass


def mark():
    """扫描未评估会话，命中启发式的打标记。返回本次新标记数。"""
    con = sqlite3.connect(str(INDEX_DB))
    _ensure_columns(con)
    rows = con.execute(
        "SELECT session_id, n_messages, n_file_ops, additions, deletions, files_json "
        "FROM sessions WHERE worth_extracting=0 AND extracted=0 AND trashed=0").fetchall()
    marked = 0
    for sid, nmsg, nfops, add, dele, fj in rows:
        reasons = []
        try:
            files = json.loads(fj or "{}")
        except Exception:
            files = {}
        if any(v >= 4 for v in files.values()):
            reasons.append("同文件返工>=4次")
        if (nmsg or 0) >= 50 and (nfops or 0) >= 10:
            reasons.append("大规模生产会话")
        if ((add or 0) + (dele or 0)) >= 300 and (nfops or 0) >= 8:
            reasons.append("高强度改动")
        if reasons:
            con.execute("UPDATE sessions SET worth_extracting=1, worth_reason=? "
                        "WHERE session_id=?", ("；".join(reasons), sid))
            marked += 1
    con.commit()
    con.close()
    return marked


def clear(session_id):
    """原子提炼完成后清除候选标记。"""
    con = sqlite3.connect(str(INDEX_DB))
    _ensure_columns(con)
    con.execute("UPDATE sessions SET extracted=1 WHERE session_id=?", (session_id,))
    con.commit()
    con.close()


def pain_detail(session_id, max_errors=12):
    """痛点透视：目标文件 + 从会话原文捞报错行（点开时按需执行）。"""
    from . import indexer
    con = sqlite3.connect(str(INDEX_DB))
    _ensure_columns(con)
    row = con.execute("SELECT summary, files_json FROM sessions WHERE session_id=?",
                      (session_id,)).fetchone()
    con.close()
    summary, fj = (row[0], row[1]) if row else ("", "{}")
    try:
        files = json.loads(fj or "{}")
    except Exception:
        files = {}
    top_files = sorted(files.items(), key=lambda x: -x[1])[:10]

    lines, seen = [], set()
    src = indexer._open_source()
    try:
        q = src.execute(
            "SELECT p.data FROM part p JOIN message m ON m.id=p.message_id "
            "WHERE p.session_id=? AND (json_extract(p.data,'$.type')='tool' "
            "OR (json_extract(p.data,'$.type')='text' "
            "AND json_extract(m.data,'$.role')='user'))", (session_id,))
        for (data,) in q:
            try:
                d = json.loads(data)
            except Exception:
                continue
            if d.get("type") == "tool":
                out = ((d.get("state") or {}).get("output") or "")
                if isinstance(out, dict):
                    out = out.get("value") or ""
                blobs = [str(out)]
            else:
                t = (d.get("text") or "").strip()
                blobs = [t] if t and "system-reminder" not in t[:40] else []
            for blob in blobs:
                for ln in blob.splitlines():
                    s = ln.strip()
                    if s and _ERR_RE.search(s) and s.lower() not in seen:
                        seen.add(s.lower())
                        lines.append(s[:200])
                if len(lines) >= max_errors:
                    break
            if len(lines) >= max_errors:
                break
    finally:
        src.close()
    return {"summary": summary or "", "top_files": top_files, "error_lines": lines}


def list_candidates(limit=50):
    """待提炼候选队列（按时间序）。"""
    con = sqlite3.connect(str(INDEX_DB))
    _ensure_columns(con)
    rows = con.execute(
        "SELECT session_id, title, project_key, worth_reason, t_updated "
        "FROM sessions WHERE worth_extracting=1 AND extracted=0 AND trashed=0 "
        "ORDER BY t_updated DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return rows


def list_ranked(limit=50):
    """候选队列按「痛点分」降序（筛查排序用）。

    分数合成（可调权重，求序不求数值的绝对意义）：
      同文件返工次数 ×12（反复改同一处 = 最痛）
    + 文件操作数 ×2
    + 改动行数（封顶3000）÷40
    + 鏖战时长（封顶24h）×6（打了一整天 = 大坑）
    + 消息数（封顶1000）÷20
    """
    con = sqlite3.connect(str(INDEX_DB))
    _ensure_columns(con)
    rows = con.execute(
        "SELECT session_id, title, project_key, worth_reason, t_updated, t_created,"
        " n_messages, n_file_ops, additions, deletions, files_json, summary "
        "FROM sessions WHERE worth_extracting=1 AND extracted=0 AND trashed=0").fetchall()
    con.close()
    out = []
    for sid, title, proj, reason, tu, tc, nmsg, nfops, add, dele, fj, summary in rows:
        try:
            files = json.loads(fj or "{}")
        except Exception:
            files = {}
        rework = max(files.values()) if files else 0
        hours = max(0.0, ((tu or 0) - (tc or 0)) / 3600000.0)
        delta = (add or 0) + (dele or 0)
        score = (rework * 12 + (nfops or 0) * 2 + min(delta, 3000) / 40.0
                 + min(hours, 24) * 6 + min(nmsg or 0, 1000) / 20.0)
        top_files = sorted(files.items(), key=lambda x: -x[1])[:3]
        out.append({
            "session_id": sid, "title": title, "project_key": proj,
            "reason": reason, "t_updated": tu, "t_created": tc,
            "score": round(score), "rework": rework,
            "n_messages": nmsg or 0, "n_file_ops": nfops or 0,
            "delta": delta, "hours": round(hours, 1),
            "summary": (summary or "")[:90],
            "top_files": [[k[:46], v] for k, v in top_files],
        })
    out.sort(key=lambda x: -x["score"])
    return out[:limit]
