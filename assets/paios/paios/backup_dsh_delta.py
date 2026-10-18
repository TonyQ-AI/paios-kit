# -*- coding: utf-8 -*-
"""DSH 会话容器的「追加增量」备份链。

数据事实：`~/.dsh/sessions/**/session.v4.jsonl.zstd` 是**只追加**的跨帧 zstd 帧流。
所以不需要块哈希、不需要逻辑导出——只要记住每个容器上次备份到第几个字节，
下一份 delta 只复制 `[offset:]` 的新字节。写入量 = 这段时间真正新产生的字节，
与 `~/.dsh` 总量彻底解耦。

链结构（默认 `<PAIOS根>/backup/dsh`）：

    dsh-YYYYMMDD.zip            # base：每日全量（复用 backup.run_dsh_daily 的快照），保 3
    chain/chain.json            # base 指针 + 全部 delta + 各容器水位（size/mtime）
    chain/delta-YYYYMMDD-HHMMSS/
        manifest.json           # {ts, base, entries: {rel: {mode, offset_before, offset_after, bytes, sha256, part}}, removed}
        parts/<rel>.part        # append：新增段；full：整文件
    chain/.lock                 # 跨进程锁（计划任务与 ZCode Stop hook 可能同时触发）
    chain/restore/              # 演练临时目录（用完即删）

容器状态判定：

    水位缺失（新容器）        → full（整文件进 delta）
    size 变小（截断/重写）    → full，水位重置
    size 相同但 mtime 变了    → full（保守：可能原地重写）
    size 相同且 mtime 未变    → skip
    size 变大（正常追加）     → append [prev_size:size]
    容器消失（会话删除）      → 只记 removed，不传播删除（归档不镜像）

铁律：

1. **链绝不能断**：delta 只保留「当前 base 之后」的；超过 MAX_DELTAS 时强制重打 base，
   绝不在中间删 delta。恢复 = base + 按序全部 delta。
2. 每份 delta 原子落盘（.tmp → 长度校验 → os.replace），带整段 sha256；
   恢复前逐份校验，坏一份就响亮失败（不静默跳过）。
3. 只读 `~/.dsh`；不写 DSH 目录；迁移期不动现有日备/intraday。
4. 恢复产物永远落在指定目录（`--to`），**绝不覆盖正在使用的 `~/.dsh`**。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import BACKUP_DIR
from .backup import (DSH_BACKUP_DIR, DSH_MAX_BYTES, DSH_SRC_DIR,
                     _cleanup_log, _dsh_offsite_dir, _valid_dsh_zip,
                     run_dsh_daily)

CHAIN_DIR = DSH_BACKUP_DIR / "chain"
CHAIN_FILE = CHAIN_DIR / "chain.json"
LOCK_FILE = CHAIN_DIR / ".lock"

MAX_DELTAS = 288              # ≈2 天（10 分钟一份）；超过就强制重打 base，绝不断链
DRILL_EVERY_S = 7 * 86400     # 每周演练一次
MAX_CHAIN_BYTES = 4 * 1024 ** 3
CHUNK = 4 * 1024 * 1024


def _now_ms():
    return int(time.time() * 1000)


def _log(msg):
    try:
        CHAIN_DIR.mkdir(parents=True, exist_ok=True)
        with open(str(CHAIN_DIR / "chain.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------- 链状态

def load_chain(root=None):
    root = Path(root) if root else CHAIN_DIR
    try:
        return json.loads((root / "chain.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_chain(c, root=None):
    root = Path(root) if root else CHAIN_DIR
    _atomic_write(root / "chain.json",
                  json.dumps(c, ensure_ascii=False, indent=1).encode("utf-8"))


class _Lock:
    """跨进程文件锁（Windows msvcrt 非阻塞）。拿不到锁说明另一进程在跑，本轮跳过。"""

    def __init__(self, path=None):
        self.path = Path(path) if path else LOCK_FILE
        self.f = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(str(self.path), "a+b")
        self.f.seek(0)
        self.f.write(b"L")
        self.f.flush()
        self.f.seek(0)
        try:
            import msvcrt
            msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.f.close()
            self.f = None
            raise BlockingIOError("another DSH backup is running")
        return self

    def __exit__(self, *exc):
        if self.f is not None:
            try:
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            self.f.close()
            self.f = None


# ---------------------------------------------------------------- 容器扫描 / base

def scan_containers(src_root=None):
    """{相对 ~/.dsh 的路径: {size, mtime_ms}}，只收会话容器。"""
    src = Path(src_root) if src_root else DSH_SRC_DIR
    out = {}
    base = src / "sessions"
    if base.is_dir():
        for dp, _dn, fns in os.walk(str(base)):
            for f in fns:
                if not (f.startswith("session.v") and f.endswith(".jsonl.zstd")):
                    continue
                p = Path(dp) / f
                try:
                    st = p.stat()
                except OSError:
                    continue
                rel = p.relative_to(src).as_posix()
                out[rel] = {"size": st.st_size,
                            "mtime_ms": int(st.st_mtime * 1000)}
    return out


def base_offsets(zip_path):
    """从 base zip 里读各容器的**快照时大小**（水位必须来自 base，不能来自当前扫描）。"""
    out = {}
    with zipfile.ZipFile(str(zip_path)) as zf:
        for info in zf.infolist():
            n = info.filename
            if n.startswith("sessions/") and n.endswith(".jsonl.zstd"):
                out[n] = {"size": info.file_size, "mtime_ms": 0}
    return out


def ensure_base(force=False, root=None):
    """确保今天的 base 存在且链指向它。返回 (chain, created)。

    base 复用 `backup.run_dsh_daily()` 生成的 `dsh-YYYYMMDD.zip`（今天已存在则直接复用）。
    新 base 建立后清空 delta、水位取自 zip 内各容器的 file_size。
    """
    root = Path(root) if root else CHAIN_DIR
    c = load_chain(root)
    name = "dsh-%s.zip" % time.strftime("%Y%m%d")
    zp = DSH_BACKUP_DIR / name
    if not force and c.get("base") == name and zp.exists() and _valid_dsh_zip(zp):
        return c, False
    if not zp.exists() or not _valid_dsh_zip(zp):
        run_dsh_daily()
    if not zp.exists() or not _valid_dsh_zip(zp):
        raise RuntimeError("base 不可用：%s" % zp)
    c = {"schema_version": 1, "base": name, "deltas": [],
         "offsets": base_offsets(zp), "last_base_ts": _now_ms(),
         "last_drill": c.get("last_drill") or {}}
    save_chain(c, root)
    # 新 base 已经覆盖旧 delta：清掉链目录里的旧 delta（否则链会重复/错位）
    for d in sorted(root.glob("delta-*")):
        shutil.rmtree(str(d), ignore_errors=True)
    _log("base reset: %s (%d containers)" % (name, len(c["offsets"])))
    return c, True


# ---------------------------------------------------------------- delta

def write_delta(root=None):
    """写一份增量。无变化返回 no-change（不产生空目录）。"""
    root = Path(root) if root else CHAIN_DIR
    c, _ = ensure_base(root=root)
    current = scan_containers()
    entries = {}
    new_offsets = {}
    ts = time.strftime("%Y%m%d-%H%M%S")
    delta_dir = root / ("delta-" + ts)
    if delta_dir.exists():
        delta_dir = root / ("delta-%s-%d" % (ts, os.getpid()))
    parts_dir = delta_dir / "parts"

    for rel, st in sorted(current.items()):
        prev = c["offsets"].get(rel)
        if prev is None:
            mode, start = "full", 0
        elif st["size"] < int(prev.get("size", 0)):
            mode, start = "full", 0            # 截断/重写
        elif st["size"] == int(prev.get("size", 0)):
            if st["mtime_ms"] == int(prev.get("mtime_ms", 0)):
                new_offsets[rel] = st             # 未变
                continue
            mode, start = "full", 0            # 同长但被写过：保守整份
        else:
            mode, start = "append", int(prev["size"])

        n = st["size"] - start
        src_file = DSH_SRC_DIR / rel
        part_rel = rel + ".part"
        part = parts_dir / part_rel
        part.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256()
        tmp = part.with_name(part.name + ".%d.tmp" % os.getpid())
        try:
            with open(str(src_file), "rb") as fi, open(str(tmp), "wb") as fo:
                fi.seek(start)
                remain = n
                while remain > 0:
                    chunk = fi.read(min(CHUNK, remain))
                    if not chunk:
                        break
                    fo.write(chunk)
                    h.update(chunk)
                    remain -= len(chunk)
            got = tmp.stat().st_size
            if got != n:
                raise RuntimeError("part 长度不符 %s: %d != %d" % (rel, got, n))
            os.replace(str(tmp), str(part))
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        entries[rel] = {"mode": mode, "offset_before": start,
                        "offset_after": st["size"], "bytes": n,
                        "sha256": h.hexdigest(), "part": part_rel}
        new_offsets[rel] = st

    removed = sorted(set(c["offsets"]) - set(current))
    if not entries and not removed:
        return {"ok": True, "action": "no-change",
                "deltas": len(c.get("deltas") or [])}

    manifest = {"schema_version": 1, "ts": _now_ms(), "base": c["base"],
                "entries": entries, "removed": removed}
    _atomic_write(delta_dir / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    c["deltas"] = list(c.get("deltas") or []) + [delta_dir.name]
    c["offsets"] = new_offsets
    c["last_delta_ts"] = _now_ms()
    save_chain(c, root)
    total = sum(e["bytes"] for e in entries.values())
    _log("delta %s: %d files (%d append, %d full), %d bytes, removed=%d"
         % (delta_dir.name, len(entries),
            sum(1 for e in entries.values() if e["mode"] == "append"),
            sum(1 for e in entries.values() if e["mode"] == "full"),
            total, len(removed)))

    # 链太长 → 强制换 base（绝不在中间删 delta 断链）
    if len(c["deltas"]) > MAX_DELTAS:
        _log("delta chain > %d -> force new base" % MAX_DELTAS)
        ensure_base(force=True, root=root)
    _enforce_capacity(root)
    return {"ok": True, "action": "delta", "name": delta_dir.name,
            "entries": len(entries), "bytes": total, "removed": len(removed)}


def _enforce_capacity(root=None):
    """链总容量闸门：只删**最旧的 base**（保底 1 份），绝不动当前链的 delta。

    删旧 base 只是缩短「历史回溯点」，不影响当前链的可恢复性（恢复只用最新 base）。
    注意不要删当前 chain 指向的 base。
    """
    root = Path(root) if root else CHAIN_DIR
    c = load_chain(root)
    cur_base = c.get("base")
    bases = [p for p in sorted(DSH_BACKUP_DIR.glob("dsh-*.zip"))
             if p.name != cur_base]
    total = _tree_size(DSH_BACKUP_DIR) + _tree_size(root)
    dropped = 0
    while total > MAX_CHAIN_BYTES and bases:
        p = bases.pop(0)
        try:
            sz = p.stat().st_size
            p.unlink()
            total -= sz
            dropped += 1
        except OSError:
            break
    if dropped:
        _log("capacity: dropped %d old base(s), total=%.1fMB"
             % (dropped, total / 1048576))
        try:
            _cleanup_log("dsh chain capacity: dropped_bases=%d total=%.1fMB cap=%.1fGB"
                         % (dropped, total / 1048576,
                            MAX_CHAIN_BYTES / 1024 ** 3))
        except Exception:
            pass


def _tree_size(p):
    total = 0
    p = Path(p)
    if not p.exists():
        return 0
    for dp, _dn, fns in os.walk(str(p)):
        for f in fns:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- 恢复

def _verify_part(path, sha, size):
    if not path.exists() or path.stat().st_size != int(size):
        return False
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest() == sha


def _zstd_check(path):
    try:
        import zstandard as zstd
    except ImportError:
        return
    with open(str(path), "rb") as f:
        with zstd.ZstdDecompressor().stream_reader(f, read_across_frames=True) as r:
            while r.read(CHUNK):
                pass


def restore(chain_root, dest, log=print):
    """把链还原到 dest（不碰主库）。返回摘要 dict；任何不符都抛 RuntimeError。"""
    root = Path(chain_root) if chain_root else CHAIN_DIR
    c = load_chain(root)
    if not c.get("base"):
        raise RuntimeError("没有 base（chain 为空）")
    base_zip = DSH_BACKUP_DIR / c["base"]
    if not base_zip.exists():
        raise RuntimeError("base 缺失：%s" % base_zip)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(str(base_zip)) as zf:
        names = zf.namelist()
        zf.extractall(str(dest))
    log("base 解压：%s（%d 个文件）" % (c["base"], len(names)))

    deltas = list(c.get("deltas") or [])
    for name in deltas:
        ddir = root / name
        mpath = ddir / "manifest.json"
        if not mpath.exists():
            raise RuntimeError("delta 缺失：%s" % name)
        mf = json.loads(mpath.read_text(encoding="utf-8"))
        for rel, e in (mf.get("entries") or {}).items():
            part = ddir / "parts" / e["part"]
            if not _verify_part(part, e["sha256"], e["bytes"]):
                raise RuntimeError("delta 损坏（%s / %s）：sha256 或长度不符"
                                   % (name, rel))
            target = dest / rel
            if e["mode"] == "full":
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(part), str(target))
            else:
                if not target.exists():
                    raise RuntimeError("append 前置失败（%s）：目标不存在" % rel)
                cur = target.stat().st_size
                if cur != int(e["offset_before"]):
                    raise RuntimeError("append 前置失败（%s）：当前 %d，期望 %d"
                                       % (rel, cur, e["offset_before"]))
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(str(target), "ab") as fo, open(str(part), "rb") as fi:
                    shutil.copyfileobj(fi, fo, CHUNK)
    log("delta 重放：%d 份" % len(deltas))

    bad = []
    offsets = c.get("offsets") or {}
    for rel, off in offsets.items():
        target = dest / rel
        if not target.exists():
            bad.append("%s(缺失)" % rel)
            continue
        sz = target.stat().st_size
        if sz != int(off.get("size", -1)):
            bad.append("%s(大小 %d != %d)" % (rel, sz, off.get("size")))
            continue
        try:
            _zstd_check(target)
        except Exception as e:
            bad.append("%s(zstd: %r)" % (rel, e))
    if bad:
        raise RuntimeError("还原校验失败：" + "；".join(bad[:5]))
    log("校验通过：%d 个容器" % len(offsets))
    return {"ok": True, "containers": len(offsets), "deltas": len(deltas)}


# ---------------------------------------------------------------- 演练 / 异地

def drill(root=None):
    """每周演练：把整条链还原到临时目录并逐容器校验。失败让调用方返回非零。"""
    root = Path(root) if root else CHAIN_DIR
    dest = root / "restore" / ("drill-" + time.strftime("%Y%m%d-%H%M%S"))
    script = Path(__file__).resolve().parents[1] / "scripts" / "restore_dsh.py"
    ok, detail = False, ""
    try:
        p = subprocess.run(
            [sys.executable, "-X", "utf8", str(script), "--chain", "--yes",
             "--to", str(dest), "--chain-root", str(root)],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=900, creationflags=0x08000000)
        ok = p.returncode == 0
        lines = (p.stdout or p.stderr or "").strip().splitlines()
        detail = lines[-1] if lines else ""
    except Exception as e:
        ok, detail = False, "drill error: %r" % e
    c = load_chain(root)
    c["last_drill"] = {"ts": _now_ms(), "ok": ok, "detail": detail[:200]}
    save_chain(c, root)
    _log("drill %s: %s" % ("OK" if ok else "FAILED", detail))
    shutil.rmtree(str(dest), ignore_errors=True)
    return {"ok": ok, "detail": detail}


def _offsite_chain(root=None):
    """异地同步：当前链的 base + 全部 delta + chain.json（链必须完整，否则异地不能恢复）。"""
    root = Path(root) if root else CHAIN_DIR
    offsite = _dsh_offsite_dir()
    if not offsite:
        return "off"
    o = Path(offsite) / "chain"
    o.mkdir(parents=True, exist_ok=True)
    c = load_chain(root)
    if not c.get("base"):
        return "no-base"
    base_src = DSH_BACKUP_DIR / c["base"]
    base_dst = o / c["base"]
    if not (base_dst.exists()
            and base_dst.stat().st_size == base_src.stat().st_size):
        tmp = base_dst.with_name(base_dst.name + ".tmp")
        shutil.copy2(str(base_src), str(tmp))
        os.replace(str(tmp), str(base_dst))
    keep = list(c.get("deltas") or [])
    for name in keep:
        sdir = root / name
        ddir = o / name
        if not sdir.is_dir():
            continue
        if ddir.is_dir() and _tree_size(ddir) == _tree_size(sdir):
            continue
        tmpd = o / (name + ".tmp")
        shutil.rmtree(str(tmpd), ignore_errors=True)
        shutil.copytree(str(sdir), str(tmpd))
        shutil.rmtree(str(ddir), ignore_errors=True)
        os.replace(str(tmpd), str(ddir))
    # 清掉异地不在当前链上的旧 delta / 旧 base
    for p in o.glob("delta-*"):
        if p.name not in keep:
            shutil.rmtree(str(p), ignore_errors=True)
    for p in o.glob("dsh-*.zip"):
        if p.name != c["base"]:
            try:
                p.unlink()
            except OSError:
                pass
    try:
        _atomic_write(o / "chain.json", (root / "chain.json").read_bytes())
    except OSError:
        pass
    return "ok"


# ---------------------------------------------------------------- 调度入口

def run(root=None):
    """计划任务入口：每日 base + 每 10 分钟 delta + 每周演练 + 异地同步。

    任何异常都不抛；返回 {"ok": bool, ...}，调用方据此决定计划任务退出码。
    """
    root = Path(root) if root else CHAIN_DIR
    try:
        with _Lock(root / ".lock"):
            r = write_delta(root=root)
            c = load_chain(root)
            drill_due = (_now_ms()
                         - int((c.get("last_drill") or {}).get("ts") or 0)
                         ) > DRILL_EVERY_S * 1000
            if drill_due:
                d = drill(root=root)
                r["drill"] = d
                if not d.get("ok"):
                    r["ok"] = False
            if r.get("ok"):
                try:
                    r["offsite"] = _offsite_chain(root=root)
                except Exception as e:
                    _log("offsite failed: %r" % (e,))
                    r["offsite"] = "error"
            return r
    except BlockingIOError:
        return {"ok": True, "action": "locked"}
    except Exception as e:
        _log("run ERROR: %r" % (e,))
        return {"ok": False, "error": repr(e)}
