# -*- coding: utf-8 -*-
"""ZCode 主库「增量备份」：周 base（压缩全量）+ 20 分钟 delta（逻辑增量）+ 可验证恢复。

设计来源：docs/handoffs/2026-10-18-zcode-backup-redesign-handoff.md（DSH 侧给方案）。
本模块是**新增链路**，不改动 backup.py 里既有的日备 / intraday / DSH 快照——
迁移期内两套并行，演练通过后可把 `local_conf.zcode_incremental` 置真以停掉
「每会话全量拷 1.2GB」的旧 intraday。

与设计的两处实测修正（2026-10-18，本机实测）：
1. **增量同时覆盖被更新的行**：`message`/`part` 都有 `time_updated` 列，原地编辑
   会让纯 rowid 增量漏更新。实测全表扫 `MAX(time_updated)` 仅 0.23s（605MB 表），
   因此每份增量用 `rowid > wm_rowid OR time_updated > wm_ts` 一次查出「新增+改动」。
2. **表清单补齐 5 张设计漏掉的表**：model_usage / tool_usage / turn_usage /
   session_input / local_setting（以及 sqlite_sequence、dwf_*、workflow_*），
   否则恢复后用量统计与工作区设置会缺。

红线（沿用设计 §9）：只读主库（`mode=ro`）；不 VACUUM / 不 checkpoint；
不碰物理页；不删旧备份；不改 DSH 相关代码。
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import SOURCE_DB, BACKUP_DIR

# ---------------------------------------------------------------- 布局与常量
ZDIR = BACKUP_DIR / "zcode"
BASE_DIR = ZDIR / "base"
DELTA_DIR = ZDIR / "delta"
RESTORE_DIR = ZDIR / "restore"
MANIFEST = ZDIR / "manifest.json"
LOG_FILE = ZDIR / "last-run.log"

KEEP_BASE = 3
KEEP_DELTA = 504                # 7 天 × 72 份/天（20 分钟一份），与 BASE_MAX_AGE_S 对齐；
                                # 超上限时重置链（打新 base），绝不删中间 delta 断链
BASE_MAX_AGE_S = 7 * 86400      # 距上次 base 超过 7 天 → 重新打 base
MAX_TOTAL_BYTES = 8 * 1024 ** 3  # P1-2（DSH 复查）：新链总容量上限（base+delta）
DRILL_EVERY_S = 7 * 86400       # 每周演练一次

# (表, 模式, 时间表达式)  mode: rowid+ts | ts | full
DELTA_SPEC = [
    # ts 模式：按时间水位只取「新增 + 改动」的行；rowid+ts：大表双水位（新行 + 原地编辑）
    ("session",           "ts",       "time_updated"),
    ("session_entry",     "ts",       "time_updated"),
    ("input_history",     "ts",       "time_created"),
    ("session_input",     "ts",       "time_updated"),
    # P0-2（DSH 复查）：补两张会随时间写入的表——现在是空表，一旦 ZCode 开始写
    # （会话目标/预算、任务树关联）备份会永久缺这块，且演练发现不了。
    ("session_target",    "ts",       "time_updated"),
    ("session_task_link", "ts",       "time_updated"),
    # P2（DSH 复查）：小表从「每份全量」改为时间增量，去掉每份 900+ 行的重复写。
    # 四张表都有可用时间列（schema_migration 用 time_applied）。
    ("todo",              "ts",       "time_updated"),
    ("local_setting",     "ts",       "time_updated"),
    ("permission",        "ts",       "time_updated"),
    ("schema_migration",  "ts",       "time_applied"),
    ("sqlite_sequence",   "full",     ""),        # 无时间列，但 0 行且极小
    ("model_usage",       "rowid+ts", "COALESCE(completed_at, started_at)"),
    ("tool_usage",        "rowid+ts", "COALESCE(completed_at, started_at)"),
    ("turn_usage",        "ts",       "COALESCE(completed_at, started_at)"),
    ("message",           "rowid+ts", "time_updated"),
    ("part",              "rowid+ts", "time_updated"),
    ("dwf_actor",         "full",     ""),
    ("dwf_event",         "full",     ""),
    ("dwf_node",          "full",     ""),
    ("dwf_run",           "full",     ""),
    ("workflow_activity", "full",     ""),
    ("workflow_definition", "full",   ""),
    ("workflow_event",    "full",     ""),
    ("workflow_run",      "full",     ""),
]


# ---------------------------------------------------------------- 压缩（zstd 优先，stdlib gzip 兜底）
def _zstd():
    try:
        import zstandard  # noqa: F401
        return zstandard
    except ImportError:
        return None


def compress_file(src, dst):
    """流式压缩 src → dst（原子：dst.tmp → replace）。返回压缩后字节数。"""
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + ".%d.tmp" % os.getpid())
    zstd = _zstd()
    try:
        if zstd is not None:
            cctx = zstd.ZstdCompressor(level=3)
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                cctx.copy_stream(fi, fo)
        else:
            import gzip
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                with gzip.GzipFile(fileobj=fo, mode="wb", compresslevel=6) as gz:
                    shutil.copyfileobj(fi, gz, 1024 * 1024)
        os.replace(str(tmp), str(dst))
        return dst.stat().st_size
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def decompress_file(src, dst):
    """流式解压 src → dst。支持 zstd（按魔数识别）与 gzip。返回解压后字节数。"""
    src, dst = Path(src), Path(dst)
    with open(src, "rb") as f:
        magic = f.read(4)
    zstd = _zstd()
    if magic[:4] == b"\x28\xb5\x2f\xfd":
        if zstd is None:
            raise RuntimeError("该备份是 zstd 压缩，但本机缺少 zstandard 依赖")
        dctx = zstd.ZstdDecompressor()
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            dctx.copy_stream(fi, fo)
    else:
        import gzip
        with gzip.open(src, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo, 1024 * 1024)
    return dst.stat().st_size


# ---------------------------------------------------------------- 只读连接与元数据
def _con():
    return sqlite3.connect("file:" + str(SOURCE_DB).replace("\\", "/") + "?mode=ro", uri=True)


def _source_stat():
    """主库轻量状态指纹（size + mtime_ns）：多天不用 ZCode 时它完全不变。"""
    try:
        st = SOURCE_DB.stat()
        return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return {}


def _table_exists(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (t,)).fetchone() is not None


def _columns(con, t):
    return [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]


def schema_fingerprint(con):
    """schema 指纹：所有表/索引 DDL + schema_migration 行。版本不一致时恢复要响亮失败。"""
    h = hashlib.sha256()
    for name, sql in con.execute(
            "SELECT name, COALESCE(sql,'') FROM sqlite_master "
            "WHERE type IN ('table','index') ORDER BY name"):
        h.update(("%s|%s\n" % (name, sql)).encode("utf-8", "replace"))
    try:
        for row in con.execute("SELECT * FROM schema_migration ORDER BY id"):
            h.update(repr(row).encode("utf-8", "replace"))
    except sqlite3.Error:
        pass
    return h.hexdigest()


def _watermarks(con, spec):
    """各表当前水位：{table: {"rowid": n, "ts": m, "count": c}}。"""
    out = {}
    for t, mode, expr in spec:
        if not _table_exists(con, t):
            continue
        entry = {"count": con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]}
        if mode in ("rowid+ts", "ts"):
            rid = con.execute('SELECT MAX(rowid) FROM "%s"' % t).fetchone()[0]
            ts = con.execute('SELECT MAX(%s) FROM "%s"' % (expr, t)).fetchone()[0]
            entry["rowid"] = rid or 0
            entry["ts"] = ts or 0
        out[t] = entry
    return out


# ---------------------------------------------------------------- manifest
def load_manifest():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 1, "base": None, "deltas": [], "watermark": {},
                "counts": {}, "last_base_ts": 0, "last_delta_ts": 0, "last_drill": {}}


def save_manifest(m):
    ZDIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_name(MANIFEST.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8",
                   newline="\n")
    os.replace(str(tmp), str(MANIFEST))


def _log(msg):
    """追加一行运行日志（不抛异常）。"""
    try:
        ZDIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        # 日志自身封顶 512KB
        if LOG_FILE.stat().st_size > 512 * 1024:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG_FILE.write_text("\n".join(lines[-2000:]) + "\n",
                                encoding="utf-8", newline="\n")
    except OSError:
        pass


# ---------------------------------------------------------------- base（周全量）
def make_base(reason=""):
    """打一份周全量 base：在线 backup → 校验 → zstd 压缩 → 原子落盘 → 更新 manifest。

    之后再删掉被新 base 覆盖的旧 delta 与超期 base（先确保新 base 有效）。
    """
    ZDIR.mkdir(parents=True, exist_ok=True)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE_DB.exists():
        return {"ok": False, "error": "no-source"}
    from .backup import _online_backup, _valid_sqlite

    name = "db-%s.sqlite" % time.strftime("%Y%m%d-%H%M%S")
    raw = BASE_DIR / name
    res = _online_backup(raw)
    if res != "ok" or not _valid_sqlite(raw):
        try:
            raw.unlink()
        except OSError:
            pass
        _log("base FAILED: %s (%s)" % (res, reason))
        return {"ok": False, "error": res}
    zst = BASE_DIR / (name + ".zst")
    size = compress_file(raw, zst)
    try:
        raw.unlink()          # 只留压缩件，省一半以上磁盘
    except OSError:
        pass

    con = _con()
    try:
        wm = _watermarks(con, DELTA_SPEC)
        fp = schema_fingerprint(con)
    finally:
        con.close()

    m = load_manifest()
    old_deltas = list(m.get("deltas") or [])
    m.update({
        "schema_version": 1,
        "schema_fingerprint": fp,
        "base": "base/" + zst.name,
        "deltas": [],
        "watermark": wm,
        "counts": {k: v.get("count", 0) for k, v in wm.items()},
        "last_base_ts": int(time.time() * 1000),
        "last_delta_ts": int(time.time() * 1000),
        "last_source_stat": _source_stat(),
    })
    save_manifest(m)

    # 新 base 有效后才裁剪：超期 base + 被 base 覆盖的旧 delta
    _trim_bases()
    _trim_deltas(KEEP_DELTA)
    _enforce_capacity()
    for d in old_deltas:
        p = DELTA_DIR / os.path.basename(d)
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    _log("base OK: %s (%.1f MB, reason=%s)" % (zst.name, size / 1048576, reason or "-"))
    return {"ok": True, "base": zst.name, "bytes": size, "reason": reason}


def _trim_bases():
    files = sorted(BASE_DIR.glob("db-*.sqlite.zst"))
    for p in files[:-KEEP_BASE]:
        try:
            p.unlink()
        except OSError:
            pass


def _trim_deltas(keep=KEEP_DELTA):
    files = sorted(DELTA_DIR.glob("delta-*.jsonl.zst"))
    for p in files[:-keep]:
        try:
            p.unlink()
        except OSError:
            pass


def _total_bytes():
    total = 0
    for d in (BASE_DIR, DELTA_DIR):
        if d.is_dir():
            total += sum(p.stat().st_size for p in d.glob("*") if p.is_file())
    return total


def _enforce_capacity(max_bytes=None):
    """P1-2（DSH 复查）：总容量闸门。

    顺序：**先删最旧的 base**（一份 ~200MB，删一个顶两千份 delta），最新 base
    永远保留；仍超才从最旧删 delta，并在 manifest 里记 `capacity_note`
    （恢复脚本据此把"行数少于 manifest"降级为告警而不是判链损坏）。
    """
    from .backup import _cleanup_log

    cap = int(max_bytes if max_bytes is not None else MAX_TOTAL_BYTES)
    dropped_bases = dropped_deltas = 0
    total = _total_bytes()
    if total <= cap:
        return {"ok": True, "total": total, "dropped_bases": 0, "dropped_deltas": 0}

    bases = sorted(BASE_DIR.glob("db-*.sqlite.zst"))
    while total > cap and len(bases) > 1:      # 永远保留最新 1 份 base
        p = bases.pop(0)
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
            dropped_bases += 1
        except OSError:
            break

    if total > cap:
        # 链绝不能断：容量只剩「当前 base + 当前 delta 链」仍然超限时，
        # 宁可暂时超限 + 告警，也绝不删链中间的 delta（删了恢复必失败）。
        _log("capacity: current chain %.1fMB still over cap %.1fGB after dropping "
             "old bases; refusing to drop deltas (would break the chain)"
             % (total / 1048576, cap / 1024 ** 3))

    if dropped_bases or dropped_deltas:
        try:
            _cleanup_log("zcode-chain capacity trim: bases=%d deltas=%d total=%.1fMB cap=%.1fGB"
                         % (dropped_bases, dropped_deltas, total / 1048576, cap / 1024 ** 3))
        except Exception:
            pass
    return {"ok": True, "total": total, "dropped_bases": dropped_bases,
            "dropped_deltas": dropped_deltas}


# ---------------------------------------------------------------- delta（增量）
def export_delta(label=""):
    """导出「新行 + 被改行」为 zstd JSONL。返回统计。

    每行：``{"t": 表, "rid": rowid, "row": {列: 值}}``
    - rowid+ts 表：``rowid > wm_rowid OR COALESCE(expr,0) > wm_ts``（一次全扫，实测 0.3s/605MB）
    - ts 表：``COALESCE(expr,0) > wm_ts``
    - full 表：每次全量（都是小表/空表）
    """
    m = load_manifest()
    if not m.get("base"):
        return {"ok": False, "error": "no-base"}
    cur = _source_stat()
    if cur and m.get("last_source_stat") == cur:
        # 主库自上次备份以来毫无变化（多天不用 ZCode）→ 不产生空 delta 文件、
        # 不推进/裁剪任何东西：链与 base 原样保留。否则每天 72 份「0 行」垃圾会
        # 把旧 delta 挤出保留窗口（2026-10-23 断链事故的同类风险）。
        return {"ok": True, "action": "no-change",
                "deltas": len(m.get("deltas") or [])}
    DELTA_DIR.mkdir(parents=True, exist_ok=True)
    wm = m.get("watermark") or {}

    name = "delta-%s.jsonl.zst" % time.strftime("%Y%m%d-%H%M%S")
    dest = DELTA_DIR / name
    tmp = dest.with_name(dest.name + ".%d.tmp" % os.getpid())
    zstd = _zstd()
    rows_written = {}
    max_wm = {}
    new_wm = dict(wm)

    con = _con()
    try:
        fp = schema_fingerprint(con)
        if m.get("schema_fingerprint") and fp != m["schema_fingerprint"]:
            con.close()
            _log("delta FAILED: schema drift, forcing base")
            r = make_base(reason="schema-drift")
            return {"ok": True, "forced_base": True, "detail": r}

        raw_f = open(tmp, "wb")
        if zstd is not None:
            fo = zstd.ZstdCompressor(level=3).stream_writer(raw_f, closefd=False)
            closer = fo.close
        else:
            import gzip
            fo = gzip.GzipFile(fileobj=raw_f, mode="wb", compresslevel=6)
            closer = fo.close
        try:
            for t, mode, expr in DELTA_SPEC:
                if not _table_exists(con, t):
                    continue
                cols = _columns(con, t)
                sel = ",".join('"%s"' % c for c in cols)
                entry = wm.get(t) or {}
                where, args = [], []
                if mode in ("rowid+ts", "ts") and entry.get("ts"):
                    if mode == "rowid+ts":
                        where.append("(rowid > ? OR COALESCE(%s,0) > ?)" % expr)
                        args += [entry.get("rowid", 0), entry["ts"]]
                    else:
                        where.append("COALESCE(%s,0) > ?" % expr)
                        args += [entry["ts"]]
                sql = 'SELECT rowid, %s FROM "%s"' % (sel, t)
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY rowid"
                n = 0
                for row in con.execute(sql, args):
                    fo.write(json.dumps(
                        {"t": t, "rid": row[0], "row": dict(zip(cols, row[1:]))},
                        ensure_ascii=False, default=str).encode("utf-8") + b"\n")
                    n += 1
                rows_written[t] = n
        finally:
            try:
                closer()
            finally:
                raw_f.close()          # gzip 的 fileobj 不随 GzipFile 关闭，必须显式关

        # 新水位与计数
        for t, mode, expr in DELTA_SPEC:
            if not _table_exists(con, t):
                continue
            e = {"count": con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]}
            if mode in ("rowid+ts", "ts"):
                e["rowid"] = con.execute('SELECT MAX(rowid) FROM "%s"' % t).fetchone()[0] or 0
                e["ts"] = con.execute('SELECT MAX(%s) FROM "%s"' % (expr, t)).fetchone()[0] or 0
            new_wm[t] = e
        fp2 = schema_fingerprint(con)
    finally:
        con.close()

    # 校验：能解压 + 逐行 JSON 合法（自愈要求的"写侧必须验"）
    tmp_size = tmp.stat().st_size
    try:
        import io
        with open(tmp, "rb") as f:
            magic = f.read(4)
        if magic[:4] == b"\x28\xb5\x2f\xfd":
            raw = zstd.ZstdDecompressor().stream_reader(open(tmp, "rb"))
        else:
            import gzip
            raw = gzip.open(tmp, "rb")
        try:
            nlines = 0
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                if line.strip():
                    json.loads(line)
                    nlines += 1
        finally:
            try:
                raw.close()
            except Exception:
                pass
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        _log("delta FAILED: verify error %s" % e)
        return {"ok": False, "error": "verify:%s" % e}

    os.replace(str(tmp), str(dest))
    deltas = list(m.get("deltas") or []) + ["delta/" + name]
    lines_map = dict(m.get("delta_lines") or {})
    lines_map[name] = nlines
    m.update({
        "deltas": deltas,
        "delta_lines": lines_map,
        "watermark": new_wm,
        "counts": {k: v.get("count", 0) for k, v in new_wm.items()},
        "last_delta_ts": int(time.time() * 1000),
        "schema_fingerprint": fp2,
        "last_source_stat": cur,
    })
    save_manifest(m)
    _enforce_capacity()
    # 链绝不能断：delta 只保留「当前 base 之后」的全部。超过 KEEP_DELTA（7 天，与
    # BASE_MAX_AGE_S 对齐）时必须重打 base 重置链，不能删最旧 delta——删了 base 与
    # 剩余 delta 之间就缺一段，恢复必然失败。
    # （2026-10-23 实际事故：旧值 48 份≈16h，而 base 周级；10-18 的 base + 10-22 起的
    #   delta，restore 退出码 7，缺 43 条消息 / 185 个 part。）
    if len(sorted(DELTA_DIR.glob("delta-*.jsonl.zst"))) > KEEP_DELTA:
        _log("delta count > %d -> reset chain with a fresh base" % KEEP_DELTA)
        make_base(reason="delta-limit")
    else:
        kept = sorted(DELTA_DIR.glob("delta-*.jsonl.zst"))
        m["deltas"] = ["delta/" + p.name for p in kept if p.name in
                       {os.path.basename(d) for d in m["deltas"]}]
        save_manifest(m)
    total = sum(rows_written.values())
    _log("delta OK: %s (%.2f MB, %d 行, %s)" %
         (name, tmp_size / 1048576, total,
          ",".join("%s=%d" % (k, v) for k, v in rows_written.items() if v)))
    return {"ok": True, "delta": name, "bytes": tmp_size, "rows": rows_written,
            "lines": nlines}


# ---------------------------------------------------------------- 演练（可验证恢复）
def drill(keep_temp=False):
    """每周演练：还原 base + 全部 delta 到临时目录，integrity_check + 行数比对。

    任何失败都返回 ok=False（供调度侧告警）；临时目录演练后删除。
    """
    import subprocess
    script = Path(__file__).resolve().parents[1] / "scripts" / "restore_zcode.py"
    RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    dest = RESTORE_DIR / ("drill-%s" % time.strftime("%Y%m%d-%H%M%S"))
    try:
        p = subprocess.run([sys.executable, str(script), "--to", str(dest), "--yes"],
                           capture_output=True, encoding="utf-8", errors="replace",
                           timeout=600, creationflags=0x08000000)
        ok = (p.returncode == 0)
        detail = (p.stdout or p.stderr or "").strip().splitlines()
        detail = detail[-1] if detail else ""
    except Exception as e:
        ok, detail = False, "drill error: %s" % e
    m = load_manifest()
    m["last_drill"] = {"ts": int(time.time() * 1000), "ok": ok, "detail": detail[:200]}
    save_manifest(m)
    _log("drill %s: %s" % ("OK" if ok else "FAILED", detail))
    if not keep_temp:
        try:
            shutil.rmtree(dest, ignore_errors=True)
        except Exception:
            pass
    return {"ok": ok, "detail": detail}


# ---------------------------------------------------------------- 异地同步

def _offsite_chain():
    """异地同步增量链：当前 base + 全部 delta + manifest（链完整才能恢复）。

    异地目录 = `local_conf.offsite_dir` + `\\chain`（缺配置 → 整体跳过）。
    只同步「当前链」的文件，异地上的旧 base / 旧 delta 会被清掉（避免异地链错位）。
    """
    from . import local_conf
    offsite = local_conf.get("offsite_dir")
    if not offsite:
        return "off"
    o = Path(offsite) / "chain"
    (o / "base").mkdir(parents=True, exist_ok=True)
    (o / "delta").mkdir(parents=True, exist_ok=True)
    m = load_manifest()
    if not m.get("base"):
        return "no-base"
    base_src = ZDIR / m["base"]
    if not base_src.exists():
        return "no-base-file"
    base_dst = o / m["base"]
    if not (base_dst.exists()
            and base_dst.stat().st_size == base_src.stat().st_size):
        tmp = base_dst.with_name(base_dst.name + ".tmp")
        shutil.copy2(str(base_src), str(tmp))
        os.replace(str(tmp), str(base_dst))
    keep = {os.path.basename(d) for d in (m.get("deltas") or [])}
    copied = 0
    for name in keep:
        src = DELTA_DIR / name
        dst = o / "delta" / name
        if not src.exists():
            continue
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copy2(str(src), str(tmp))
        os.replace(str(tmp), str(dst))
        copied += 1
    for p in (o / "delta").glob("delta-*.jsonl.zst"):
        if p.name not in keep:
            try:
                p.unlink()
            except OSError:
                pass
    for p in (o / "base").glob("db-*.sqlite.zst"):
        if p.name != os.path.basename(m["base"]):
            try:
                p.unlink()
            except OSError:
                pass
    # manifest 最后写：恢复端只在 manifest 指向的文件都在时才动手
    tmpm = o / "manifest.json.tmp"
    tmpm.write_bytes(MANIFEST.read_bytes())
    os.replace(str(tmpm), str(o / "manifest.json"))
    return "copied:%d" % copied


# ---------------------------------------------------------------- 主入口（调度用）
def run(now=None, force_base=False):
    """调度入口：按需打 base / 导出 delta / 每周演练。任何异常都不抛（返回状态）。"""
    try:
        m = load_manifest()
        ts = int((now or time.time()) * 1000)
        cur = _source_stat()
        # base 只在「主库确实变了」且已过期时才重打：多天不用 ZCode 时主库没变，
        # 旧 base 与新 base 等价——重打只会白写 200MB，还会把旧 base 轮转出去。
        src_changed = bool(cur) and m.get("last_source_stat") != cur
        need_base = (force_base or not m.get("base")
                     or (src_changed
                         and (ts - int(m.get("last_base_ts") or 0)) > BASE_MAX_AGE_S * 1000))
        act = "base" if need_base else "delta"
        result = make_base(reason="scheduled" if not m.get("base") else "age") \
            if need_base else export_delta()
        drill_due = (ts - int((m.get("last_drill") or {}).get("ts") or 0)) > DRILL_EVERY_S * 1000
        if drill_due:
            d = drill()
            result["drill"] = d
            # P0-1（DSH 复查）：演练失败 = 恢复能力不可证明，整体必须失败
            # （计划任务退出码 1 + 日志 drill FAILED），不能只看 base/delta 是否成功。
            if not d.get("ok", False):
                result["ok"] = False
                _log("run FAILED: drill failed -> %s" % (d.get("detail") or ""))
        # 异地同步（链完整才同步；失败不影响本地链）
        if result.get("ok", False):
            try:
                result["offsite"] = _offsite_chain()
            except Exception as e:
                _log("offsite failed: %r" % (e,))
                result["offsite"] = "error"
        return {"ok": result.get("ok", False), "action": act, "result": result}
    except Exception as e:
        _log("run ERROR: %r" % (e,))
        return {"ok": False, "error": repr(e)}


# ---------------------------------------------------------------- 链信息（恢复脚本用）
def list_chain():
    m = load_manifest()
    base = m.get("base")
    out = {
        "base": base,
        "base_exists": bool(base) and (ZDIR / base).is_file(),
        "deltas": [],
        "schema_fingerprint": m.get("schema_fingerprint"),
        "counts": m.get("counts") or {},
        "last_delta_ts": m.get("last_delta_ts"),
        "last_drill": m.get("last_drill") or {},
        "delta_lines": m.get("delta_lines") or {},
        "capacity_note": m.get("capacity_note") or {},
    }
    for d in m.get("deltas") or []:
        p = ZDIR / d
        out["deltas"].append({"path": d, "exists": p.is_file(),
                              "bytes": p.stat().st_size if p.is_file() else 0,
                              "lines": (m.get("delta_lines") or {}).get(
                                  os.path.basename(d))})
    return out
