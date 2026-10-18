# -*- coding: utf-8 -*-
"""DSH（DeepSeek Harness）会话源：只读解析 `~/.dsh/sessions` 的会话容器。

容器格式（2026-10-18 实测，DSH 桌面版）：
    ~/.dsh/sessions/<工作区 slug>/<session-id>/session.v4.jsonl.zstd
    - **跨帧 zstd** 压缩的 JSONL（单帧读会只拿到首行，必须 stream_reader(read_across_frames=True)）
    - 首帧  {"type":"session","version":4,"id":...,"createdAt":...,"cwd":...,
             "parentSession":...,"isSeeded":false,"origin":"subagent","delegationDepth":1,
             "agentPreset":"standard"}
    - 其余帧是事件流：user/message、assistant/message、tool/call、tool/result、
      session/title、turn/start、step/*、subagent/descriptor …

为什么在 PAIOS 侧解析（与「解析责任归数据拥有方」原则的让步）：
- DSH 没有可编程会话读取入口：`dsh` CLI 只有 boot/plugin；GUI 的 /export 是手动动作。
  要做到「自动入索引」只能读它落盘的事实。
- 因此把漂移风险关在三个闸门里：只认 version==4；坏文件只跳过不猜；本源整体可禁用。

闸门：
1. 只认 `header.version == 4`。未知版本 → stderr 警告 + 跳过该文件（绝不猜字段）。
2. 解压/解析失败的单个文件只计数跳过（会话可能正在写入，帧未 flush）。
3. zstd 解码器是**可选依赖**（优先 zstandard，其次 backports.zstd）。缺失时
   `available()` 返回 False，DSH 源整体禁用，ZCode 主源行为完全不变。
4. 只读：绝不写 `~/.dsh` 下任何东西。

测试钩子：环境变量 `PAIOS_DSH_SESSIONS_DIR` 覆盖会话根目录。
"""
import io
import json
import os
import sys
from pathlib import Path

CONTAINER_NAME = "session.v4.jsonl.zstd"
CONTAINER_GLOB = "session.v*.jsonl.zstd"
KNOWN_VERSION = 4

# 提取文件操作路径的工具参数键（与 ZCode 侧口径一致）
_PATH_KEYS = ("file_path", "path", "notebook_path")

# user/message 里这些注入块不算「首条用户消息」
_INJECT_MARKERS = ("<system-reminder>", "system-reminder", "<command-name>",
                   "<local-command-stdout>", "Current runtime context",
                   "Team message")


# ---------------------------------------------------------------- 可用性

def sessions_root():
    """DSH 会话根目录（可被环境变量覆盖，便于测试与多 profile）。"""
    env = os.environ.get("PAIOS_DSH_SESSIONS_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".dsh" / "sessions"


def _decompressor():
    """返回 zstd 解压器；不可用返回 None（不抛异常）。"""
    try:
        import zstandard as zstd  # 可选依赖
        return zstd.ZstdDecompressor()
    except Exception:
        pass
    try:
        import backports.zstd as bzstd  # Python 3.14 compression.zstd 的 backport
        return bzstd.ZstdDecompressor()
    except Exception:
        return None


def available():
    """(bool, 原因/根目录)。DSH 源不可用时上层静默跳过。"""
    root = sessions_root()
    if not root.is_dir():
        return False, "未找到 DSH 会话目录 %s" % root
    if _decompressor() is None:
        return False, ("缺少 zstd 解码器（pip install zstandard）；"
                       "DSH 源已禁用，ZCode 主源不受影响")
    return True, str(root)


def _warn(msg):
    print("[paios.dsh] %s" % msg, file=sys.stderr)


# ---------------------------------------------------------------- 解析

def _iter_lines(path, dec):
    """逐行产出解压后的 JSONL 文本；跨帧读取。"""
    with open(path, "rb") as f:
        with dec.stream_reader(f, read_across_frames=True) as reader:
            text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
            for line in text:
                line = line.strip()
                if line:
                    yield line


def _json_or_none(line):
    try:
        return json.loads(line)
    except Exception:
        return None


def _user_text(obj):
    """从 user/message 事件取可见文本（跳过注入块与附件占位）。"""
    data = obj.get("data") or {}
    content = data.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        t = (block.get("text") or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def _looks_injected(text):
    head = (text or "")[:80]
    return any(m in head for m in _INJECT_MARKERS)


def _tool_paths(obj):
    """从 tool/call 事件抽文件路径。arguments 是 JSON 字符串。"""
    data = obj.get("data") or {}
    args = data.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return []
    if not isinstance(args, dict):
        return []
    out = []
    for k in _PATH_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def read_container(path):
    """解析一个会话容器，返回规范记录 dict；不可解析返回 {"error": ...}。

    字段对齐 indexer 的 sessions 表口径：
      n_messages = user/message + assistant/message 条数
      n_tools    = tool/call 条数
      n_file_ops = 工具调用里出现过的**唯一**文件路径数
    """
    dec = _decompressor()
    if dec is None:
        return {"error": "no-zstd-decoder"}
    header = None
    n_messages = 0
    n_tools = 0
    paths = []
    first_text = ""
    title = ""
    title_source = ""
    last_time = 0
    bad_lines = 0
    try:
        for line in _iter_lines(path, dec):
            # 跳过体积大而索引不需要的事件（tool/result 常占容器 80% 以上字节）。
            # 只看行首 48 字节判断 type，避免为丢弃的行做完整 JSON 解析。
            head = line[:48]
            if ('"tool/result"' in head or '"step/' in head
                    or '"session-log-' in head or '"job/' in head
                    or '"team/' in head or '"compaction/' in head
                    or '"request/' in head or '"delivery-accepted"' in head):
                continue
            obj = _json_or_none(line)
            if obj is None:
                bad_lines += 1
                continue
            typ = obj.get("type")
            if header is None:
                if typ != "session":
                    return {"error": "no-session-header", "first_type": typ}
                header = obj
                continue
            t = obj.get("time")
            if isinstance(t, int) and t > last_time:
                last_time = t
            if typ == "user/message":
                n_messages += 1
                if not first_text:
                    txt = _user_text(obj)
                    if txt and not _looks_injected(txt):
                        first_text = txt
            elif typ == "assistant/message":
                n_messages += 1
            elif typ == "tool/call":
                n_tools += 1
                paths.extend(_tool_paths(obj))
            elif typ == "session/title":
                data = obj.get("data") or {}
                t2 = (data.get("title") or "").strip()
                if t2:
                    title = t2
                    src = data.get("source") or {}
                    title_source = (src.get("kind") if isinstance(src, dict) else "") or ""
    except Exception as e:
        # 会话可能正在写入（帧未 flush）——本轮跳过，下轮 mtime 变了再来
        return {"error": "unreadable", "detail": str(e)[:200]}

    if header is None:
        return {"error": "empty-container"}
    ver = header.get("version")
    if ver != KNOWN_VERSION:
        return {"error": "unknown-version", "version": ver}

    sid = header.get("id") or path.parent.name
    # DSH 的 fallback 标题取「首条用户消息前 26 字」，subagent 会话里首条常是
    # 注入的 system-reminder / runtime context——这类标题直接丢弃，改用完整首条
    # 用户文本（同样被注入污染的则留空，由 indexer 落 "(无标题)"）。
    if title and _looks_injected(title):
        title, title_source = "", ""
    if first_text and (not title or title_source == "fallback"):
        flat = " ".join(first_text.split())
        if len(flat) > len(title):
            title = flat[:100]
            title_source = "fallback"
    try:
        mtime_ms = int(os.path.getmtime(path) * 1000)
    except OSError:
        mtime_ms = 0
    t_created = header.get("createdAt") or 0
    t_updated = max(mtime_ms, last_time or 0)
    uniq = []
    seen = set()
    for p in paths:
        k = p.replace("/", "\\").rstrip("\\").lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    rec = {
        "session_id": str(sid),
        "source": "dsh",
        "container": str(path),
        "version": ver,
        "title": title,
        "title_source": title_source,
        "directory": header.get("cwd") or "",
        "t_created": int(t_created or 0),
        "t_updated": int(t_updated or 0),
        "n_messages": n_messages,
        "n_tools": n_tools,
        "n_file_ops": len(uniq),
        "paths": uniq,
        "first_message": first_text[:500],
        "delegation_depth": int(header.get("delegationDepth") or 0),
        "parent_session_id": header.get("parentSession") or "",
        "agent_preset": header.get("agentPreset") or "",
        "is_seeded": 1 if header.get("isSeeded") else 0,
        "origin": header.get("origin") or "",
    }
    if bad_lines:
        rec["bad_lines"] = bad_lines
    return rec


# ---------------------------------------------------------------- 扫描

def scan(watermark_ms=0, force_paths=None):
    """增量扫描：返回 (records, stats)。

    records 只含 mtime > watermark 的容器（watermark 为本源独立水位线）；
    force_paths 里的容器（上次解析失败的）无视水位线重试，避免「失败一次就永久跳过」。
    统计里给上层打日志/自检用，不参与入库。
    """
    force_paths = force_paths or {}
    ok, why = available()
    stats = {"available": ok, "reason": why, "files": 0, "changed": 0,
             "parsed": 0, "failed": 0, "skipped": 0, "unknown_version": 0,
             "max_mtime_ms": int(watermark_ms or 0), "failures": {}}
    if not ok:
        return [], stats
    root = sessions_root()
    try:
        files = sorted(root.rglob(CONTAINER_GLOB))
    except OSError as e:
        stats["reason"] = "扫描失败: %s" % e
        return [], stats
    stats["files"] = len(files)
    records = []
    for p in files:
        try:
            mtime_ms = int(os.path.getmtime(p) * 1000)
        except OSError:
            stats["skipped"] += 1
            continue
        stats["max_mtime_ms"] = max(stats["max_mtime_ms"], mtime_ms)
        forced = str(p) in force_paths
        if mtime_ms <= int(watermark_ms or 0) and not forced:
            stats["skipped"] += 1
            continue
        stats["changed"] += 1
        rec = read_container(p)
        if "error" in rec:
            stats["failed"] += 1
            stats["failures"][str(p)] = {"mtime_ms": mtime_ms,
                                         "error": rec["error"],
                                         "version": rec.get("version")}
            if rec["error"] == "unknown-version":
                stats["unknown_version"] += 1
                _warn("跳过未知容器版本 %r：%s（只认 v%d）" %
                      (rec.get("version"), p, KNOWN_VERSION))
            elif rec["error"] in ("unreadable", "no-session-header", "empty-container"):
                # 会话正在写入或容器损坏：本轮跳过，下轮 mtime 变了会重试
                if rec["error"] == "unreadable":
                    _warn("容器暂不可读（可能正在写入）：%s" % p)
            else:
                _warn("容器无法解析（%s）：%s" % (rec["error"], p))
            continue
        stats["parsed"] += 1
        records.append(rec)
    return records, stats


# ---------------------------------------------------------------- 原文读取（导出/痛点）

def find_container(session_id):
    """按会话 id 找容器路径（索引里 container_path 缺失/失效时的回退）。"""
    if not session_id:
        return None
    root = sessions_root()
    try:
        for p in root.rglob(CONTAINER_GLOB):
            if p.parent.name == session_id:
                return str(p)
    except OSError:
        return None
    try:
        for p in root.rglob(CONTAINER_GLOB):
            rec = read_container(p)
            if rec.get("session_id") == session_id:
                return str(p)
    except OSError:
        return None
    return None


def iter_events(container_path):
    """逐条产出容器里的原始事件（含 tool/result 等大事件，供导出/痛点使用）。

    与 scan() 的区别：scan 为索引跳过体积大的事件，这里要完整原文。
    """
    dec = _decompressor()
    if dec is None:
        return
    for line in _iter_lines(container_path, dec):
        obj = _json_or_none(line)
        if isinstance(obj, dict):
            yield obj


def _fmt_ms(ms):
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _brief_args(args):
    """工具参数摘要（与 indexer._brief_input 同口径，只取最有信息量的键）。"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return args[:200].replace("\n", " ")
    if not isinstance(args, dict):
        return str(args)[:200]
    keys = ["command", "description", "file_path", "path", "pattern",
            "url", "query", "prompt", "skill", "title", "task_id"]
    frags = [f"{k}={str(args[k])[:160]}" for k in keys if args.get(k)]
    return ("; ".join(frags) or json.dumps(args, ensure_ascii=False))[:240]


def _strip_injected(text):
    """去掉注入的 system-reminder / command 上下文块。"""
    import re
    t = text or ""
    t = re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S)
    t = re.sub(r"<command-[a-z]+>.*?</command-[a-z]+>", "", t, flags=re.S)
    if _looks_injected(t):
        return ""
    return t.strip()


def render_markdown(container_path, meta=None):
    """把会话容器渲染成 Markdown（与 ZCode 的 export_session_md 同版式）。"""
    meta = meta or {}
    header = None
    events = []
    for obj in iter_events(container_path):
        if header is None and obj.get("type") == "session":
            header = obj
            continue
        if header is None:
            continue
        events.append(obj)
    if header is None:
        return None
    title = meta.get("title") or ""
    if not title:
        for e in events:
            if e.get("type") == "session/title":
                title = (e.get("data") or {}).get("title") or title
    if not title:
        for e in events:
            if e.get("type") == "user/message":
                title = _user_text(e)[:80]
                if title:
                    break
    sid = header.get("id") or ""
    depth = int(header.get("delegationDepth") or 0)
    md = []
    md.append(f"# 会话历史记录 · {title or '(无标题)'}")
    md.append("")
    md.append("| 项 | 值 |")
    md.append("|---|---|")
    md.append(f"| 会话 ID | `{sid}` |")
    md.append("| 来源 | DSH（DeepSeek Harness，`.dsh` 容器只读直读） |")
    md.append(f"| 标题 | {title or '-'} |")
    md.append(f"| 时间 | {_fmt_ms(header.get('createdAt'))} → {_fmt_ms(meta.get('t_updated'))} |")
    md.append(f"| 项目目录 | {header.get('cwd') or '无'} |")
    md.append(f"| 子代理会话 | {'是（depth=%d，父 %s）' % (depth, header.get('parentSession')) if depth else '否'} |")
    md.append(f"| 预设/模型 | {header.get('agentPreset') or '-'} |")
    md.append(f"| 容器 | `{container_path}` |")
    md.append("")
    md.append("---")
    md.append("")
    for e in events:
        typ = e.get("type")
        t = _fmt_ms(e.get("time"))
        if typ == "user/message":
            txt = _strip_injected(_user_text(e))
            if not txt:
                continue
            md.append(f"## [{e.get('seq')}] 🙋 用户 · {t}")
            md.append("")
            md.append(txt)
            md.append("")
            md.append("---")
            md.append("")
        elif typ == "assistant/message":
            msg = (e.get("data") or {}).get("message") or {}
            body = []
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        body.append(txt)
                elif bt == "reasoning":
                    rt = (block.get("text") or "").strip()
                    if rt:
                        show = rt[:5000] + ("\n…（截断）" if len(rt) > 5000 else "")
                        body.append("<details><summary>💭 思考过程</summary>\n\n"
                                    + show.replace("```", "``\\`") + "\n\n</details>")
                # tool-call 块跳过：紧接着的 tool/call 事件会渲染（避免重复）
            if not body:
                continue
            md.append(f"## [{e.get('seq')}] 🤖 AI · {t}")
            md.append("")
            md.append("\n\n".join(body))
            md.append("")
            md.append("---")
            md.append("")
        elif typ == "tool/call":
            data = e.get("data") or {}
            md.append(f"- **🔧 {data.get('name') or '?'}**：`{_brief_args(data.get('arguments'))}`")
        elif typ == "tool/result":
            data = e.get("data") or {}
            msg = data.get("message") or {}
            out = []
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    out.append(block.get("text") or "")
            text = "\n".join(x for x in out if x).strip()
            if not text:
                continue
            iso = " ⚠️ 出错" if data.get("isError") or msg.get("isError") else ""
            show = text[:2000] + ("\n…（截断，共 %d 字符）" % len(text) if len(text) > 2000 else "")
            md.append(f"  <details><summary>输出{iso}</summary>\n\n  ```\n  "
                      + show.replace("\n", "\n  ") + "\n  ```\n\n  </details>")
    return "\n".join(md)


def error_lines(container_path, pattern, max_errors=12):
    """从容器原文里捞报错行（候选页「痛点透视」用，正则由调用方给）。"""
    lines, seen = [], set()
    for e in iter_events(container_path):
        typ = e.get("type")
        if typ == "tool/result":
            msg = (e.get("data") or {}).get("message") or {}
            blobs = [(b.get("text") or "") for b in (msg.get("content") or [])
                     if isinstance(b, dict) and b.get("type") == "text"]
        elif typ == "user/message":
            t = _strip_injected(_user_text(e))
            blobs = [t] if t else []
        else:
            continue
        for blob in blobs:
            for ln in blob.splitlines():
                s = ln.strip()
                if s and pattern.search(s) and s.lower() not in seen:
                    seen.add(s.lower())
                    lines.append(s[:200])
            if len(lines) >= max_errors:
                return lines
    return lines
