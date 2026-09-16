# -*- coding: utf-8 -*-
"""增量索引器：从 ZCode 主库(db.sqlite)抽取会话元数据，写入自己的索引库。

设计要点：
- 主库始终以 read-only URI 打开，避免干扰正在运行的 ZCode。
- 增量水位线存于索引库 meta 表；只处理 time_updated 超过水位的会话。
- 归属判定三级降级：目录直推 > 文件操作投票 > 标题关键词兜底(低置信)。
- 人工修正过的归属(confidence='manual')不会被自动同步覆盖。
"""
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime

from . import INDEX_DB, ensure_data_dir, source_db_uri, load_workspace_roots
from .classify import (classify_session, collect_roots, list_project_names,
                       norm, scan_project_nodes)
from .enrich import read_project_desc, assess_project, classify_type, coarse_type

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id   TEXT PRIMARY KEY,
  title        TEXT,
  title_source TEXT,
  directory    TEXT,
  project_key  TEXT,
  project_path TEXT,
  confidence   TEXT DEFAULT 'low',
  summary      TEXT,
  first_message TEXT,
  project_desc TEXT DEFAULT '',
  project_maturity TEXT DEFAULT '',
  project_type TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  n_messages   INTEGER DEFAULT 0,
  n_tools      INTEGER DEFAULT 0,
  n_file_ops   INTEGER DEFAULT 0,
  additions    INTEGER DEFAULT 0,
  deletions    INTEGER DEFAULT 0,
  files_json   TEXT DEFAULT '{}',
  worth_extracting INTEGER DEFAULT 0,
  worth_reason TEXT DEFAULT '',
  extracted    INTEGER DEFAULT 0,
  archived     INTEGER DEFAULT 0,
  trashed      INTEGER DEFAULT 0,
  t_created    INTEGER,
  t_updated    INTEGER
);
CREATE TABLE IF NOT EXISTS deleted_sessions(
  session_id TEXT PRIMARY KEY,
  t_deleted  INTEGER
);
CREATE TABLE IF NOT EXISTS project_status(
  project_key TEXT PRIMARY KEY,
  status     TEXT,
  t_set      INTEGER
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_key);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(t_updated);
"""


def _open_index():
    ensure_data_dir()
    con = sqlite3.connect(str(INDEX_DB))
    con.executescript(SCHEMA)
    # 兼容旧库：增量加列
    for col in ("project_desc TEXT DEFAULT ''", "project_maturity TEXT DEFAULT ''",
                "project_type TEXT DEFAULT ''", "notes TEXT DEFAULT ''",
                "worth_extracting INTEGER DEFAULT 0", "worth_reason TEXT DEFAULT ''",
                "extracted INTEGER DEFAULT 0",
                "archived INTEGER DEFAULT 0", "trashed INTEGER DEFAULT 0"):
        try:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {col}")
        except Exception:
            pass
    con.commit()
    return con


def _open_source():
    return sqlite3.connect(source_db_uri(), uri=True)


def _get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ---------------------------------------------------------------- 提取逻辑

def _fetch_sessions(src, watermark):
    """拉取需(重新)处理的顶层会话基础信息。"""
    rows = src.execute("""
        SELECT id, directory, title, title_source,
               COALESCE(summary_additions,0), COALESCE(summary_deletions,0),
               time_created, time_updated, task_type
        FROM session
        WHERE time_updated > ? AND parent_id IS NULL
        ORDER BY time_updated ASC
    """, (watermark,)).fetchall()
    keys = ["id", "directory", "title", "title_source",
            "additions", "deletions", "t_created", "t_updated", "task_type"]
    return [dict(zip(keys, r)) for r in rows]


def _extract_file_ops(src, watermark):
    """提取工具调用目标路径（含子代理），归并到父会话。

    返回 {parent_sid: [path,...]} 和 {parent_sid: tool_count}。
    """
    ops = {}          # {parent_sid: [path]}
    tool_cnt = Counter()
    # 先查所有子会话的 parent 映射（只取本次水位线以上的）
    child_parent = {}
    for cid, pid in src.execute("""
        SELECT id, parent_id FROM session
        WHERE time_updated > ? AND parent_id IS NOT NULL
    """, (watermark,)):
        child_parent[cid] = pid

    q = """
        SELECT p.session_id, p.data
        FROM part p JOIN session s ON s.id = p.session_id
        WHERE s.time_updated > ?
          AND json_extract(p.data,'$.type')='tool'
    """
    for sid, data in src.execute(q, (watermark,)):
        try:
            d = json.loads(data)
        except Exception:
            continue
        # 把子代理的工具计数归到父会话
        owner = child_parent.get(sid, sid)
        tool_cnt[owner] += 1
        inp = (d.get("state") or {}).get("input") or {}
        fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
        if fp:
            ops.setdefault(owner, []).append(fp)
    return ops, dict(tool_cnt)


def _extract_first_user_text(src, watermark):
    """提取父会话及子会话的首条用户文本，都归到父会话名下。"""
    first = {}
    child_parent = {}
    for cid, pid in src.execute("""
        SELECT id, parent_id FROM session
        WHERE time_updated > ? AND parent_id IS NOT NULL
    """, (watermark,)):
        child_parent[cid] = pid

    q = """
        SELECT p.session_id, p.data
        FROM part p
        JOIN message m ON m.id = p.message_id
        JOIN session s ON s.id = p.session_id
        WHERE s.time_updated > ?
          AND json_extract(m.data,'$.role')='user'
          AND json_extract(p.data,'$.type')='text'
        ORDER BY p.time_created ASC
    """
    for sid, data in src.execute(q, (watermark,)):
        owner = child_parent.get(sid, sid)
        if owner in first:
            continue
        try:
            text = (json.loads(data).get("text") or "").strip()
        except Exception:
            text = ""
        if text and "system-reminder" not in text[:40]:
            first[owner] = text[:500]
    return first


def _extract_msg_count(src, watermark):
    """统计父会话及其所有子代理的消息总数。"""
    cnt = Counter()
    child_parent = {}
    for cid, pid in src.execute("""
        SELECT id, parent_id FROM session
        WHERE time_updated > ? AND parent_id IS NOT NULL
    """, (watermark,)):
        child_parent[cid] = pid

    q = """
        SELECT m.session_id, COUNT(*) FROM message m
        JOIN session s ON s.id = m.session_id
        WHERE s.time_updated > ?
        GROUP BY m.session_id
    """
    for sid, n in src.execute(q, (watermark,)):
        owner = child_parent.get(sid, sid)
        cnt[owner] += n
    return dict(cnt)


def _extract_child_titles(src, watermark):
    """提取子会话标题，归并到父会话名下。

    返回 {parent_sid: [child_title, ...]}，子标题用于搜索兜底匹配。
    """
    child_titles = {}
    q = """
        SELECT parent_id, title FROM session
        WHERE time_updated > ? AND parent_id IS NOT NULL
          AND title IS NOT NULL AND title <> ''
    """
    for pid, title in src.execute(q, (watermark,)):
        child_titles.setdefault(pid, []).append(title)
    return child_titles


# ---------------------------------------------------------------- 派生字段

def make_summary(info, first_msg):
    ts = info.get("title_source") or ""
    title = info.get("title") or ""
    if len(title) <= 80 and title:
        return title
    src = (first_msg or title or "").replace("\r", " ").replace("\n", " ").strip()
    return src[:120] + ("…" if len(src) > 120 else "")


def make_files_json(paths):
    """按出现次数聚合文件路径，保留 Top30，键为末三级目录+文件名（保留原始大小写）。

    比较用小写，显示用原始串。
    """
    c = Counter()
    display = {}  # 用小写作 key 的首个出现对应的原始路径
    for p in paths:
        lower = p.lower()
        parts = [x for x in lower.split("\\") if x]
        short_lower = "\\".join(parts[-3:]) if len(parts) >= 4 else lower.lstrip("\\")
        c[short_lower] += 1
        if short_lower not in display:
            # 尝试保留原始路径末三级（大小写）
            orig_parts = [x for x in p.replace("/", "\\").split("\\") if x]
            if len(orig_parts) >= 4:
                display[short_lower] = "\\".join(orig_parts[-3:])
            else:
                display[short_lower] = p.replace("/", "\\").lstrip("\\")
    return {display[k]: v for k, v in c.most_common(30)}


# ---------------------------------------------------------------- 同步

def rebuild_all():
    """丢弃索引，全量重建。"""
    con = _open_index()
    con.execute("DELETE FROM sessions")
    _set_meta(con, "watermark_ms", 0)
    con.commit()
    con.close()


def sync(verbose=False):
    icon = _open_index()
    src = _open_source()
    watermark = int(_get_meta(icon, "watermark_ms") or 0)

    sessions = _fetch_sessions(src, watermark)
    new_n = updated_n = 0
    max_ts = watermark

    if sessions:
        directories = [r[0] for r in src.execute(
            "SELECT DISTINCT directory FROM session WHERE directory IS NOT NULL")]
        roots = collect_roots(directories)
        registered = load_workspace_roots()
        nodes = scan_project_nodes(roots + registered)
        folder_names = list_project_names(roots + registered)

        file_ops, tool_counts = _extract_file_ops(src, watermark)
        msg_counts = _extract_msg_count(src, watermark)
        first_texts = _extract_first_user_text(src, watermark)
        child_titles_map = _extract_child_titles(src, watermark)
        existing = {r[0] for r in icon.execute("SELECT session_id FROM sessions")}
        # 已彻底删除的会话永久跳过（防同步回灌）
        deleted_ids = {r[0] for r in icon.execute("SELECT session_id FROM deleted_sessions")}

        for info in sessions:
            sid = info["id"]
            if sid in deleted_ids:
                continue
            fm = first_texts.get(sid, "")
            # 把子会话标题追加到 first_message（用于搜索兜底匹配）
            child_titles = child_titles_map.get(sid, [])
            if child_titles:
                extra = " | ".join(child_titles[:8])
                fm = (fm + " | " + extra) if fm else extra
            ops = file_ops.get(sid, [])
            max_ts = max(max_ts, info["t_updated"])

            prev = icon.execute("SELECT project_key, project_path, confidence, "
                                "project_desc, project_maturity, archived, trashed "
                                "FROM sessions WHERE session_id=?",
                                (sid,)).fetchone()
            prev_arch = prev[5] if prev else 0
            prev_trash = prev[6] if prev else 0
            if prev and prev[2] == "manual":
                cls = {"project_key": prev[0], "project_path": prev[1],
                       "confidence": "manual"}
            else:
                cls = classify_session(info, ops, roots, folder_names,
                                       nodes=nodes, registered_roots=registered)

            params_full = dict(
                title=(info.get("title") or fm or "(无标题)")[:200],
                summary=make_summary(info, fm),
                n_messages=msg_counts.get(sid, 0),
                n_tools=tool_counts.get(sid, 0),
                n_file_ops=len(ops),
                additions=info["additions"],
                deletions=info["deletions"],
                files=json.dumps(make_files_json(ops), ensure_ascii=False),
            )

            # 项目描述：优先从已有记录取，否则读 README
            pp = cls.get("project_path", "")
            pk = cls.get("project_key", "")
            # 路径修正：如果 project_path 不含 project_key（指向了 projects 根），自动拼接
            if pp and pk and pk != "未归类":
                from pathlib import Path as _P
                pp_lower = pp.replace("/", "\\").rstrip("\\").lower()
                if not pp_lower.endswith(pk.lower()):
                    candidate = _P(pp) / pk
                    if candidate.is_dir():
                        pp = str(candidate)
            # project_path 为空时，尝试从已知的 projects 根目录拼出实际路径
            if not pp and pk and pk != "未归类":
                from pathlib import Path as _P
                for root_dir in roots:
                    candidate = _P(root_dir) / pk
                    if candidate.is_dir():
                        pp = str(candidate)
                        break
            if prev and prev[3]:  # prev[3] = project_desc
                pdesc = prev[3]
                pmaturity = prev[4] or ""
            else:
                pdesc = read_project_desc(pp, pk) if pp else ""
                pmaturity = ""
            # 项目完成度：每次同步重新评估（目录结构可能变化）
            if pp and (not pmaturity or not prev):
                mat = assess_project(pp)
                pmaturity = f"{mat['icon']} {mat['label']}({mat['score']}分) {' '.join(mat['details'])}"
            # 项目类型
            ptype = classify_type(pp) if pp else ""

            if prev:
                icon.execute("""UPDATE sessions SET title=?, summary=?, n_messages=?,
                                n_tools=?, n_file_ops=?, additions=?, deletions=?,
                                files_json=?, first_message=?, t_updated=?,
                                project_key=?, project_path=?, confidence=?,
                                project_desc=?, project_maturity=?, project_type=?,
                                archived=?, trashed=?
                                WHERE session_id=?""",
                             (params_full["title"], params_full["summary"],
                              params_full["n_messages"], params_full["n_tools"],
                              params_full["n_file_ops"], params_full["additions"],
                              params_full["deletions"], params_full["files"],
                              fm[:1000], info["t_updated"], cls["project_key"],
                              pp, cls["confidence"], pdesc, pmaturity, ptype,
                              prev_arch, prev_trash, sid))
                updated_n += 1
            else:
                icon.execute("""INSERT INTO sessions(session_id,title,title_source,
                    directory,project_key,project_path,confidence,summary,
                    first_message,project_desc,project_maturity,project_type,
                    n_messages,n_tools,n_file_ops,additions,deletions,
                    files_json,t_created,t_updated)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (sid, params_full["title"], info.get("title_source"),
                              info.get("directory"), cls["project_key"],
                              pp, cls["confidence"],
                              params_full["summary"], fm[:1000], pdesc, pmaturity, ptype,
                              params_full["n_messages"], params_full["n_tools"],
                              params_full["n_file_ops"], params_full["additions"],
                              params_full["deletions"], params_full["files"],
                              info["t_created"], info["t_updated"]))
                new_n += 1

    _set_meta(icon, "watermark_ms", max_ts)
    icon.commit()
    total = icon.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    src.close()
    icon.close()
    stats = {"new": new_n, "updated": updated_n, "total": total}
    if verbose:
        print(json.dumps(stats, ensure_ascii=False))
    return stats


# ---------------------------------------------------------------- 查询 API

_COLS = ["session_id", "title", "project_key", "project_path", "confidence",
         "summary", "project_desc", "project_maturity", "project_type",
         "n_messages", "n_tools", "n_file_ops", "additions", "deletions",
         "files_json", "first_message", "notes", "t_created", "t_updated",
         "directory"]


def query_sessions(q="", project="", limit=200, order="updated", state="active",
                   _fallback=True):
    icon = _open_index()
    sql = f"SELECT {','.join(_COLS)} FROM sessions WHERE 1=1"
    args = []
    # 视图状态：active=当前 / archived=已归档 / trash=垃圾箱
    if state == "archived":
        sql += " AND archived=1 AND trashed=0"
    elif state == "trash":
        sql += " AND trashed=1"
    else:
        sql += " AND archived=0 AND trashed=0"
    # 多关键词：按空白拆词，每个词命中任一字段即可，词与词之间取交集
    tokens = (q or "").strip().split()
    for tok in tokens:
        like = f"%{tok}%"
        sql += (" AND (title LIKE ? OR summary LIKE ? OR project_key LIKE ?"
                " OR files_json LIKE ? OR first_message LIKE ? OR notes LIKE ?)")
        args += [like] * 6
    if project:
        sql += " AND project_key = ?"
        args.append(project)
    sql += " ORDER BY " + {"created": "t_created DESC",
                           "updated_asc": "t_updated ASC",
                           "created_asc": "t_created ASC"
                           }.get(order, "t_updated DESC")
    sql += " LIMIT ?"
    args.append(int(limit))
    rows = [dict(zip(_COLS, r)) for r in icon.execute(sql, args)]
    # 多词 AND 全部无结果时，自动降级为只用第一个词（OR 任一字段）
    if not rows and tokens and len(tokens) > 1 and _fallback:
        icon.close()
        return query_sessions(q=tokens[0], project=project, limit=limit,
                              order=order, state=state, _fallback=False)
    overrides = _status_overrides(icon)
    icon.close()
    out = []
    for r in rows:
        fj = r.pop("files_json")
        try:
            r["files"] = json.loads(fj)
        except Exception:
            r["files"] = {}
        r["folder_exists"] = bool(r.get("project_path")) and os.path.isdir(r["project_path"])
        r["app_type"] = coarse_type(r.get("project_type") or "")
        r["proj_status"] = overrides.get(r["project_key"]) \
            or _derive_status(r.get("project_maturity") or "")
        r["proj_status_src"] = "manual" if r["project_key"] in overrides else "auto"
        out.append(r)
    return out


def query_projects():
    icon = _open_index()
    rows = icon.execute("""
        SELECT project_key, COUNT(*), MAX(t_updated), MIN(project_path)
        FROM sessions WHERE trashed=0
        GROUP BY project_key ORDER BY MAX(t_updated) DESC
    """).fetchall()
    overrides = _status_overrides(icon)
    mats = {}
    for k, m in icon.execute("SELECT project_key, project_maturity FROM sessions "
                             "WHERE trashed=0 AND project_maturity<>'' "
                             "ORDER BY t_updated DESC"):
        mats.setdefault(k, m)
    icon.close()
    out = []
    for a, b, c, d in rows:
        # 应用类型按项目目录结构现判（目录结构可能已变化，缓存值不可信）
        app = coarse_type(classify_type(d)) if d else "未归类"
        status = overrides.get(a) or _derive_status(mats.get(a, ""))
        out.append({"project_key": a, "sessions": b, "last_active": c,
                    "path": d, "app_type": app, "status": status,
                    "status_src": "manual" if a in overrides else "auto"})
    return out


def stats():
    icon = _open_index()
    total = icon.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    unclassified = icon.execute(
        "SELECT COUNT(*) FROM sessions WHERE confidence='low'").fetchone()[0]
    archived = icon.execute(
        "SELECT COUNT(*) FROM sessions WHERE archived=1 AND trashed=0").fetchone()[0]
    trashed = icon.execute(
        "SELECT COUNT(*) FROM sessions WHERE trashed=1").fetchone()[0]
    wm = int(_get_meta(icon, "watermark_ms") or 0)
    icon.close()
    return {"total": total, "unclassified": unclassified,
            "archived": archived, "trashed": trashed, "last_sync_ms": wm}


def reassign(session_id, project_key, project_path=None):
    """人工修正归属（confidence 置 manual，后续自动同步不覆盖）。"""
    icon = _open_index()
    pp = project_path or ""
    if not pp:
        row = icon.execute("SELECT project_path FROM sessions WHERE project_key=? "
                           "AND project_path<>'' LIMIT 1", (project_key,)).fetchone()
        pp = row[0] if row else ""
    pdesc = read_project_desc(pp, project_key) if pp else ""
    mat = assess_project(pp) if pp else {"icon": "❓", "label": "未归类", "score": 0, "details": []}
    pmaturity = f"{mat['icon']} {mat['label']}({mat['score']}分) {' '.join(mat['details'])}"
    ptype = classify_type(pp) if pp else ""
    icon.execute("UPDATE sessions SET project_key=?, project_path=?, confidence='manual',"
                 " project_desc=?, project_maturity=?, project_type=? WHERE session_id=?",
                 (project_key, pp, pdesc, pmaturity, ptype, session_id))
    icon.commit()
    icon.close()


def update_note(session_id, notes):
    """更新会话备注。"""
    icon = _open_index()
    icon.execute("UPDATE sessions SET notes=? WHERE session_id=?", (notes, session_id))
    icon.commit()
    icon.close()


# ---------------------------------------------------------------- 项目状态

_STATUS_AUTO = (("🟢", "可运行"), ("🟡", "开发中"), ("🔴", "草稿"))


def _derive_status(maturity):
    """从自动完成度文本推导状态（🟢可运行/🟡开发中/🔴草稿），无信号返回空。"""
    for icon_char, label in _STATUS_AUTO:
        if icon_char in (maturity or ""):
            return label
    return ""


def _status_overrides(icon):
    return dict(icon.execute("SELECT project_key, status FROM project_status"))


def set_project_status(project_key, status):
    """手动指定项目状态（覆盖自动评分，同步不覆盖）；status 为空=清除手改回自动。"""
    icon = _open_index()
    if status:
        icon.execute("INSERT OR REPLACE INTO project_status(project_key, status, t_set) "
                     "VALUES(?,?,?)",
                     (project_key, status, int(datetime.now().timestamp() * 1000)))
    else:
        icon.execute("DELETE FROM project_status WHERE project_key=?", (project_key,))
    icon.commit()
    icon.close()


def batch_state(ids, op):
    """会话批量状态：archive 归档 / unarchive 取消归档 / trash 移入垃圾箱 / restore 恢复。

    垃圾箱与归档互斥：移入垃圾箱同时取消归档，恢复时两个标记都清零。
    """
    if not ids:
        return {"ok": False, "affected": 0, "result": "未选择会话"}
    sql = {"archive": "UPDATE sessions SET archived=1 WHERE session_id=?",
           "unarchive": "UPDATE sessions SET archived=0 WHERE session_id=?",
           "trash": "UPDATE sessions SET trashed=1, archived=0 WHERE session_id=?",
           "restore": "UPDATE sessions SET trashed=0, archived=0 WHERE session_id=?"
           }.get(op)
    if not sql:
        return {"ok": False, "affected": 0, "result": f"未知操作 {op}"}
    icon = _open_index()
    n = sum(icon.execute(sql, (sid,)).rowcount for sid in ids)
    icon.commit()
    icon.close()
    return {"ok": True, "affected": n}


def purge_sessions(ids):
    """彻底删除：仅垃圾箱内会话可删。删除索引行并记入 deleted_sessions，
    之后增量同步不再回灌。只动 PAIOS 索引，ZCode 主库原文不动。"""
    if not ids:
        return {"ok": False, "deleted": [], "errors": ["未选择会话"]}
    icon = _open_index()
    deleted, errors = [], []
    t = int(datetime.now().timestamp() * 1000)
    for sid in ids:
        row = icon.execute("SELECT trashed FROM sessions WHERE session_id=?",
                           (sid,)).fetchone()
        if not row:
            errors.append("%s:不存在" % sid[:12])
            continue
        if not row[0]:
            errors.append("%s:不在垃圾箱" % sid[:12])
            continue
        icon.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
        icon.execute("INSERT OR REPLACE INTO deleted_sessions(session_id, t_deleted) "
                     "VALUES(?,?)", (sid, t))
        deleted.append(sid)
    icon.commit()
    icon.close()
    return {"ok": True, "deleted": deleted, "errors": errors}


# ---------------------------------------------------------------- 会话导出

def _clean_user_text(t):
    """去掉注入的 system-reminder / command 上下文块，只留用户真实输入。"""
    t = re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S)
    t = re.sub(r"<command-[a-z]+>.*?</command-[a-z]+>", "", t, flags=re.S)
    return t.strip()


def _brief_input(inp):
    if not isinstance(inp, dict):
        return str(inp)[:160]
    keys = ["command", "description", "file_path", "path", "pattern",
            "url", "query", "prompt", "skill", "title"]
    frags = []
    for k in keys:
        if k in inp and inp[k]:
            frags.append(f'{k}={str(inp[k])[:160]}')
    if not frags:
        frags.append(json.dumps(inp, ensure_ascii=False)[:160])
    return "; ".join(frags)


def _render_tool(p):
    tool = p.get("tool", "?")
    state = p.get("state", {})
    inp = state.get("input", {})
    out = state.get("output", "")
    if isinstance(out, dict):
        out = out.get("value") or json.dumps(out, ensure_ascii=False)
    out = str(out or "")
    status = state.get("status", "")
    lines = [f'- **🔧 {tool}**（{status}）：`{_brief_input(inp)}`']
    if out:
        show = out[:2000] + ("\n…（截断，共 %d 字符）" % len(out) if len(out) > 2000 else "")
        lines.append("  <details><summary>输出</summary>\n\n  ```\n  "
                      + show.replace("\n", "\n  ") + "\n  ```\n\n  </details>")
    return "\n".join(lines)


def _esc_fence(t):
    return t.replace("```", "``\\`")


def export_session_md(session_id):
    """导出指定会话的完整对话为 Markdown 字符串。"""
    src = _open_source()
    # 会话元信息
    row = src.execute("""
        SELECT id, directory, title, time_created, time_updated
        FROM session WHERE id=?
    """, (session_id,)).fetchone()
    if not row:
        src.close()
        return None
    sid, directory, title, t_create, t_update = row

    def fmt_ts(ms):
        return (datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
                if ms else "")

    md = []
    md.append(f"# 会话历史记录 · {title}")
    md.append("")
    md.append("| 项 | 值 |")
    md.append("|---|---|")
    md.append(f"| 会话 ID | `{sid}` |")
    md.append(f"| 标题 | {title} |")
    md.append(f"| 时间 | {fmt_ts(t_create)} → {fmt_ts(t_update)} |")
    md.append(f"| 项目目录 | {directory or '无'} |")
    md.append("")
    md.append("---")
    md.append("")

    # 消息列表
    messages = src.execute("""
        SELECT id, sequence, data FROM message
        WHERE session_id=? ORDER BY sequence
    """, (sid,)).fetchall()

    for mid, seq, mdata in messages:
        m = json.loads(mdata)
        role = m.get("role", "?")
        t = fmt_ts((m.get("time") or {}).get("created"))
        parts_rows = src.execute("""
            SELECT data FROM part
            WHERE message_id=? AND session_id=? ORDER BY sequence
        """, (mid, sid)).fetchall()
        parts = [json.loads(r[0]) for r in parts_rows]

        body = []
        for p in parts:
            pt = p.get("type")
            if pt == "text":
                txt = p.get("text", "")
                if role == "user":
                    txt = _clean_user_text(txt)
                if txt:
                    body.append(txt)
            elif pt == "tool":
                body.append(_render_tool(p))
            elif pt == "reasoning":
                rt = p.get("text", "").strip()
                if rt:
                    show = rt[:5000] + ("\n…（截断）" if len(rt) > 5000 else "")
                    body.append("<details><summary>💭 思考过程</summary>\n\n"
                                + _esc_fence(show) + "\n\n</details>")
            elif pt == "file":
                body.append(f"📎 文件：`{p.get('filename') or p.get('url', '')}`")

        if not body:
            continue
        who = "🙋 用户" if role == "user" else "🤖 AI"
        md.append(f"## [{seq}] {who} · {t}")
        md.append("")
        md.append("\n\n".join(body))
        md.append("")
        md.append("---")
        md.append("")

    src.close()
    return "\n".join(md)


def export_all_project_sessions():
    """导出所有有项目的会话为 ZIP，按项目分文件夹。

    返回 (zip_bytes, filename) 或 (None, None)。
    """
    import io
    import zipfile
    from datetime import datetime

    icon = _open_index()
    rows = icon.execute("""
        SELECT session_id, project_key, title, t_created
        FROM sessions WHERE project_key <> '未归类' AND project_key <> ''
        AND trashed=0
        ORDER BY project_key, t_created
    """).fetchall()
    icon.close()

    if not rows:
        return None, None

    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for sid, project_key, title, t_created in rows:
            md = export_session_md(sid)
            if not md:
                continue
            # 文件名：日期-时间-标题.md
            dt = datetime.fromtimestamp(t_created / 1000).strftime("%Y%m%d-%H%M%S")
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', (title or "无标题"))[:60]
            fname = f"{dt}-{safe_title}.md"
            fpath = f"{project_key}/{fname}"
            zf.writestr(fpath, md.encode("utf-8"))
            count += 1

    if count == 0:
        return None, None

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return buf.getvalue(), f"zsess-export-{ts}.zip"
