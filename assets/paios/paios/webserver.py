# -*- coding: utf-8 -*-
"""PAIOS 本地网页服务：静态 GUI + JSON API（纯标准库，仅绑定 127.0.0.1，端口 8643）。

路由：
  GET  /api/atoms?q=&status=   原子列表（meta+一句话结论）
  GET  /api/atom?id=           原子全文
  GET  /api/candidates         候选沉淀队列
  POST /api/atom_status        {id, status} 转正/废弃/恢复
  POST /api/candidate_done     {session_id} 标记该候选已提炼
"""
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import STATIC_DIR, DEFAULT_PORT, ensure_data_dir
from . import atoms, candidates, indexer

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    # ------------------------------------------------------------ 响应工具
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        path = (STATIC_DIR / rel).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        ext = path.suffix.lower()
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------ 路由
    def do_GET(self):
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/" or u.path == "/index.html":
                return self._static("index.html")
            if not u.path.startswith("/api/"):
                return self._static(u.path.lstrip("/")) if ".." not in u.path \
                    else self._json({"error": "bad path"}, 400)

            if u.path == "/api/sessions":
                rows = indexer.query_sessions(
                    q=qs.get("q", ""), project=qs.get("project", ""),
                    limit=min(int(qs.get("limit", 200)), 1000),
                    order=qs.get("order", "updated"),
                    state=qs.get("state", "active"))
                counts = atoms.count_by_project()
                for r in rows:
                    r["proj_atoms"] = counts.get(r["project_key"], 0)
                return self._json(rows)

            if u.path == "/api/projects":
                ps = indexer.query_projects()
                counts = atoms.count_by_project()
                for p in ps:
                    p["atoms"] = counts.get(p["project_key"], 0)
                return self._json(ps)

            if u.path == "/api/stats":
                st = indexer.stats()
                st["push_fail"] = atoms.read_push_fail_note()
                return self._json(st)

            if u.path == "/api/open_folder":
                p = qs.get("path", "")
                opened = _open_in_explorer(p)
                return self._json({"ok": bool(opened)})

            if u.path == "/api/export":
                sid = qs.get("session_id", "")
                if not sid:
                    return self._json({"error": "需要 session_id"}, 400)
                md = indexer.export_session_md(sid)
                if md is None:
                    return self._json({"error": "会话不存在"}, 404)
                body = md.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="session_{sid[:16]}.md"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if u.path == "/api/export_all":
                result = indexer.export_all_project_sessions()
                if result[0] is None:
                    return self._json({"error": "没有有项目的会话可导出"}, 404)
                zip_data, fname = result
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(zip_data)))
                self.end_headers()
                self.wfile.write(zip_data)
                return

            if u.path == "/api/atoms":
                q = qs.get("q", "")
                status = qs.get("status", "")
                out = []
                for meta, body, path in atoms.list_atoms():
                    if status and meta.get("status") != status:
                        continue
                    if q:
                        tags = meta.get("tags")
                        hay = " ".join([meta.get("title", ""),
                                        " ".join(tags) if isinstance(tags, list) else str(tags or ""),
                                        body]).lower()
                        if not all(t.lower() in hay for t in q.split()):
                            continue
                    import re as _re
                    m = _re.search(r"【一句话结论】\s*(.+)", body)
                    out.append({
                        "id": meta.get("id"), "title": meta.get("title"),
                        "type": meta.get("type"), "status": meta.get("status"),
                        "tags": meta.get("tags") if isinstance(meta.get("tags"), list) else [],
                        "project": meta.get("project", ""), "date": meta.get("date"),
                        "source": meta.get("source"), "source_ref": meta.get("source_ref"),
                        "one_liner": m.group(1).strip() if m else "",
                        "rule": meta.get("rule", ""),
                        "family": meta.get("family") if isinstance(meta.get("family"), list) else [],
                        "family_note": meta.get("family_note", ""),
                        "merged": meta.get("merged") if isinstance(meta.get("merged"), list) else [],
                        "file": path.name,
                    })
                return self._json(out)

            if u.path == "/api/atom":
                aid = qs.get("id", "")
                for meta, body, path in atoms.list_atoms():
                    if meta.get("id") == aid:
                        return self._json({
                            "meta": {k: v for k, v in meta.items()},
                            "body": body, "file": path.name,
                        })
                return self._json({"error": "原子不存在"}, 404)

            if u.path == "/api/candidates":
                if qs.get("sort", "pain") == "time":
                    rows = candidates.list_candidates(int(qs.get("limit", 50)))
                    return self._json([
                        {"session_id": r[0], "title": r[1], "project_key": r[2],
                         "reason": r[3], "t_updated": r[4]} for r in rows])
                return self._json(candidates.list_ranked(int(qs.get("limit", 50))))

            if u.path == "/api/atom_similar":
                aid = qs.get("id", "")
                sims = atoms.similar_atoms(aid, top=5)
                if sims is None:
                    return self._json({"error": "原子不存在"}, 404)
                return self._json([
                    {"id": m.get("id"), "title": m.get("title"),
                     "status": m.get("status"), "score": s, "channel": ch}
                    for s, m, b, ch in sims])

            if u.path == "/api/atom_clusters":
                return self._json(atoms.family_clusters())

            if u.path == "/api/cluster_condense":
                theme = qs.get("theme", "")
                c = next((x for x in atoms.family_clusters() if x["theme"] == theme), None)
                if not c:
                    return self._json({"error": "同族分组不存在或已变化"}, 404)
                return self._json({"theme": theme, "ids": [m["id"] for m in c["members"]],
                                   "draft": atoms.condense_cluster(theme, c["members"])})

            if u.path == "/api/candidate_pain":
                sid = qs.get("session_id", "")
                if not sid:
                    return self._json({"error": "需要 session_id"}, 400)
                return self._json(candidates.pain_detail(sid))

            return self._json({"error": "unknown api"}, 404)
        except Exception as e:  # 任何异常都不弄垮服务线程
            try:
                return self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            payload = {}

        try:
            if u.path == "/api/sync":
                st = indexer.sync(verbose=False)
                st.update(indexer.stats())
                st["candidates_marked"] = candidates.mark()
                return self._json(st)

            if u.path == "/api/reassign":
                sid = payload.get("session_id", "")
                key = (payload.get("project_key") or "").strip()
                if not sid or not key:
                    return self._json({"error": "需要 session_id 和 project_key"}, 400)
                indexer.reassign(sid, key, payload.get("project_path"))
                return self._json({"ok": True})

            if u.path == "/api/note":
                sid = payload.get("session_id", "")
                if not sid:
                    return self._json({"error": "需要 session_id"}, 400)
                indexer.update_note(sid, payload.get("notes", ""))
                return self._json({"ok": True})

            if u.path == "/api/sessions_batch":
                ids = list(payload.get("ids") or [])
                op = str(payload.get("op") or "")
                if not ids:
                    return self._json({"ok": False, "result": "未选择会话"}, 400)
                if op == "delete":
                    return self._json(indexer.purge_sessions(ids))
                return self._json(indexer.batch_state(ids, op))

            if u.path == "/api/project_status":
                key = (payload.get("project_key") or "").strip()
                if not key:
                    return self._json({"error": "需要 project_key"}, 400)
                indexer.set_project_status(key, (payload.get("status") or "").strip())
                return self._json({"ok": True})

            if u.path == "/api/atom_status":
                ok, result = atoms.set_status(str(payload.get("id") or ""),
                                              str(payload.get("status") or ""))
                return self._json({"ok": ok, "result": result})

            if u.path == "/api/atom_rule":
                aid = str(payload.get("id") or "")
                if payload.get("promote"):
                    ok, result = atoms.promote_to_rule(aid)
                else:
                    ok, result = atoms.demote_from_rule(aid)
                return self._json({"ok": ok, "result": result})

            if u.path == "/api/cluster_rule":
                theme = str(payload.get("theme") or "").strip()
                ids = [str(x) for x in (payload.get("ids") or [])]
                condensed = str(payload.get("condensed") or "").strip()
                if not theme or not ids:
                    return self._json({"ok": False, "result": "需要 theme 和 ids"}, 400)
                ok, result = atoms.promote_cluster_rule(theme, ids, condensed=condensed or None)
                return self._json({"ok": ok, "result": result})

            if u.path == "/api/atom_link":
                ok, result = atoms.link_family(str(payload.get("id") or ""),
                                               str(payload.get("other") or ""),
                                               str(payload.get("note") or ""))
                return self._json({"ok": ok, "result": result})

            if u.path == "/api/atom_merge":
                ok, result = atoms.merge_atoms(
                    str(payload.get("primary") or ""),
                    list(payload.get("absorbed") or []),
                    merged_body=payload.get("body"),
                    merged_title=payload.get("title"),
                    merged_tags=payload.get("tags"))
                return self._json({"ok": ok, "result": result})

            if u.path == "/api/atoms_batch":
                ids = list(payload.get("ids") or [])
                op = str(payload.get("op") or "")
                if not ids:
                    return self._json({"ok": False, "result": "未选择原子"})
                if op == "delete":
                    ok, result = atoms.delete_atoms(ids)
                    return self._json({"ok": ok, "result": result})
                results = []
                for aid in ids:
                    _ok, _r = atoms.set_status(aid, op)
                    results.append("%s:%s" % (aid, "ok" if _ok else _r))
                return self._json({"ok": True, "result": results})

            if u.path == "/api/atom_link_batch":
                aid = str(payload.get("id") or "")
                others = list(payload.get("others") or [])
                note = str(payload.get("note") or "")
                results = []
                for other in others:
                    ok, r = atoms.link_family(aid, other, note if other == others[0] else "")
                    results.append("%s-%s:%s" % (aid, other, "ok" if ok else r))
                return self._json({"ok": True, "result": results})

            if u.path == "/api/atom_merge_batch":
                ops = list(payload.get("ops") or [])
                results = []
                for op in ops:
                    ok, r = atoms.merge_atoms(str(op.get("primary") or ""),
                                              list(op.get("absorbed") or []))
                    results.append({"primary": op.get("primary"),
                                    "ok": ok, "result": r})
                return self._json({"ok": True, "result": results})

            if u.path == "/api/atom_split":
                ok, result = atoms.split_atom(str(payload.get("id") or ""),
                                              list(payload.get("parts") or []))
                return self._json({"ok": ok, "result": result})

            if u.path == "/api/candidate_done":
                sid = payload.get("session_id", "")
                if not sid:
                    return self._json({"error": "需要 session_id"}, 400)
                candidates.clear(sid)
                return self._json({"ok": True})

            return self._json({"error": "unknown api"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def _open_in_explorer(path):
    """在资源管理器中打开项目文件夹（仅允许本机已存在的目录）。"""
    from pathlib import Path
    if not path:
        return False
    p = Path(path.replace("/", "\\"))
    while not p.exists() and p.parent != p:  # 目录可能已被移动，向上找存在的祖先
        p = p.parent
    if p.is_dir() and p.parent != p:  # 兜底兜到盘符根目录就别开了（定位不准）
        os.startfile(str(p))  # noqa 仅 Windows
        return True
    return False


def serve(port=DEFAULT_PORT, open_browser=True):
    """启动 UI 服务。

    向量模型**不预热**（2026-09-22 起）：按需在首次「找相似」时加载。
    冷启动实测 ~0.1s（OpenBLAS 单线程化后常驻 ~163MB），而预热会让
    「开着不用」也长期付这份内存（实测 632MB 私有提交的主因，见交接 #33）。
    """
    ensure_data_dir()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"PAIOS UI: {url}  (Ctrl+C 退出)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    serve()
