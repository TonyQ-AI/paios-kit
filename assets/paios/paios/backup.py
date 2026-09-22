# -*- coding: utf-8 -*-
"""ZCode 主库双层滚动备份 + 异地周备份（sqlite3 在线 backup API，禁止文件复制——WAL 不一致快照）。

- 日级全量：data/backup/db-YYYYMMDD.sqlite，每日首会话备份一份，保 3 天
- 会话级快照：data/backup/intraday/snap-YYYYMMDD-HHMM.sqlite，
  每个会话结束都拍一份，保最近 2 份——把「当天内主库损坏」的风险窗口
  从最长 24h 压到约 1 个会话间隔。
- 异地周备份：目标目录取自机器级本地配置（<安装根>/data/local-config.json 的
  offsite_dir，如 "D:\\paios-backup"），每周后的首次触发拷贝本周最新日级备份，
  保 3 份——防整盘灾难。**分发包默认没有该配置 → 此功能整体关闭**（不会往
  别人的盘里写）；拷贝的是已通过 backup API 落盘并验证过的快照，不对主库二次加压。
"""
import os
import shutil
import sqlite3
import time

from . import SOURCE_DB, BACKUP_DIR

# 日级 3 份（用户裁定：三日回溯足够——更早的数据坏了说明三天没人碰，
# 有价值内容早已沉淀进原子库/git，可再生）+ 会话级 2 份，本地合计 ≈1.9GB 封顶。
KEEP_DAILY = 3
# 实测：backup API 快照是全量文件（~710MB/份），非增量页。
KEEP_INTRADAY = 2
INTRADAY_DIR = BACKUP_DIR / "intraday"

KEEP_OFFSITE = 3
OFFSITE_MAX_AGE_DAYS = 7  # 距上次异地备份超过 7 天且当日无周备 → 补拷


def _offsite_dir():
    """异地备份目标目录：机器级本地配置的 offsite_dir；未配置返回空串（功能关闭）。"""
    from . import local_conf
    return local_conf.get("offsite_dir")


def _valid_sqlite(path):
    """O(1) 完整性校验：无热日志 + 页数≥1 且页数×页宽与文件大小自洽 + 可读。

    不扫全库（大库扫不动）。识别两类残废备份：写到一半被中断的截断文件
    （header 声明的页数 > 实际文件）、带 hot journal 的脏文件。注意必须
    要求 page_count≥1：空库同样满足 页数×页宽==文件大小（0==0），会被误判有效。
    """
    try:
        if os.path.exists(str(path) + "-journal"):
            return False
        if os.path.getsize(path) < 1024:
            return False
        con = sqlite3.connect("file:" + str(path).replace("\\", "/") + "?mode=ro", uri=True)
        try:
            ps = con.execute("PRAGMA page_size").fetchone()[0]
            pc = con.execute("PRAGMA page_count").fetchone()[0]
        finally:
            con.close()
        return ps > 0 and pc >= 1 and abs(ps * pc - os.path.getsize(path)) < 4096
    except Exception:
        return False


def _clean_stale(d):
    """清掉 .tmp / -journal / -wal / -shm：都不是能独立成立的备份内容。

    （-wal/-shm 是打开备份文件时按源库的 WAL 标记派生出来的边车，
    备份本体在写入时已转成 DELETE 日志模式，见 _online_backup。）
    """
    n = 0
    for pat in ("*.tmp", "*-journal", "*-wal", "*-shm"):
        for p in d.glob(pat):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def _online_backup(dest):
    """在线备份，**原子落盘**：先写带 pid 的 .tmp，校验通过再 os.replace。

    中断/并发只会留下 .tmp（下次自动清），永不产生半截 .sqlite 冒充有效备份。
    写完后把副本转成 DELETE 日志模式（源库是 WAL，备份会继承 WAL 标记，
    导致每次打开都派生 -wal/-shm 边车文件）。
    """
    if not SOURCE_DB.exists():
        return "no-source"
    tmp = dest.with_name(dest.name + ".%d.tmp" % os.getpid())
    src = sqlite3.connect("file:" + str(SOURCE_DB).replace("\\", "/") + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
        try:
            dst.execute("PRAGMA journal_mode=DELETE")   # 副本自包含，无 WAL 边车
        except sqlite3.Error:
            pass
    finally:
        dst.close()
        src.close()
    if not _valid_sqlite(tmp):
        try:
            tmp.unlink()
        except OSError:
            pass
        return "invalid"
    os.replace(str(tmp), str(dest))
    return "ok"


def run_daily():
    """每日首次触发：当天全量快照 + 会话级快照 + 异地周备份检查。

    僵尸自愈：若当天文件存在但校验不过（截断/带脏日志），删掉重做——
    否则 `exists` 短路会让那一天的备份永久停在坏文件上。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _clean_stale(BACKUP_DIR)
    dest = BACKUP_DIR / ("db-%s.sqlite" % time.strftime("%Y%m%d"))
    if dest.exists() and _valid_sqlite(dest):
        result = "exists"
    else:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        result = _online_backup(dest)
        if result == "no-source":
            return result
    _cleanup(BACKUP_DIR, "db-*.sqlite", KEEP_DAILY)
    _intraday()
    _offsite()
    return result


def _intraday():
    """每个会话结束都拍一份当天快照，保最近 KEEP_INTRADAY 份。

    命名到秒 + 写入带 pid 的 .tmp：同分钟/同秒的并发 hook 不再互相撞车
    （此前分钟级命名曾在并发时写出截断文件与孤儿 journal）。
    """
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    _clean_stale(INTRADAY_DIR)
    dest = INTRADAY_DIR / ("snap-%s.sqlite" % time.strftime("%Y%m%d-%H%M%S"))
    _online_backup(dest)
    _cleanup(INTRADAY_DIR, "snap-*.sqlite", KEEP_INTRADAY)


def _offsite():
    """异地周备份：最新日级备份拷到配置的异地目录，超 7 天未备则补拷，保 3 份。

    异地目录来自机器级本地配置；未配置（分发包默认）→ 本函数整体不做事。
    """
    import os
    offsite = _offsite_dir()
    if not offsite:
        return
    dailies = sorted(BACKUP_DIR.glob("db-*.sqlite"))
    if not dailies:
        return
    latest = dailies[-1]
    try:
        os.makedirs(offsite, exist_ok=True)
    except OSError:
        return  # 目标盘不可用（未挂载等），静默跳过，下次触发再试
    dest = os.path.join(offsite,
                        "db-weekly-" + latest.stem.replace("db-", "") + ".sqlite")
    existing = sorted(f for f in os.listdir(offsite)
                      if f.startswith("db-weekly-"))
    if existing:
        last = existing[-1]
        # 本周已备（同一份源文件）→ 跳过
        if last == "db-weekly-" + latest.name.replace("db-", ""):
            return
        # 距上次异地备份不足 7 天 → 跳过
        try:
            age = time.time() - os.path.getmtime(os.path.join(offsite, last))
            if age < OFFSITE_MAX_AGE_DAYS * 86400:
                return
        except OSError:
            pass
    shutil.copy2(latest, dest)
    _cleanup_offsite(offsite)
    print("config-layer sync:", _config_layer_sync())


def _config_layer_sync():
    """搭车兜底：把配置层仓库（~/.zcode）的未提交改动提交并推送。

    搭在异地周备份这一趟上（每周一次），专抓「改了规则但那个会话忘了提交/推送」。
    只对白名单仓库生效（其 .gitignore 是白名单写法，不会误收运行态数据/私钥）；
    任何失败都只返回状态字符串，绝不影响备份流程本身。
    """
    import os
    import pathlib
    import subprocess
    import tempfile

    cfg = pathlib.Path(os.path.expanduser("~")) / ".zcode"
    if not (cfg / ".git").exists():
        return "no-repo"

    def _git(*args):
        # CREATE_NO_WINDOW：本函数在会话结束钩子里跑，绝不能弹控制台窗口
        return subprocess.run(["git", "-C", str(cfg)] + list(args),
                              capture_output=True, encoding="utf-8", errors="replace",
                              creationflags=0x08000000, timeout=180)

    try:
        dirty = [l for l in _git("status", "--porcelain").stdout.splitlines() if l.strip()]
        if dirty:
            _git("add", "-A")
            msg = ("chore(snapshot): 配置层周快照自动提交（%d 项）\n\n"
                   "由 paios/backup.py 的异地周备份搭车执行；非人工整理，追溯请看 diff。\n\n"
                   "Tool: paios-snapshot\n" % len(dirty))
            mp = os.path.join(tempfile.gettempdir(), "zcode-config-sync-msg.txt")
            with open(mp, "w", encoding="utf-8", newline="\n") as f:
                f.write(msg)
            rc = _git("commit", "-q", "-F", mp).returncode
            try:
                os.remove(mp)
            except OSError:
                pass
            if rc != 0:
                return "commit-failed"
        ahead = _git("log", "--oneline", "origin/master..HEAD").stdout.strip()
        if not ahead:
            return "up-to-date"
        rc = _git("push", "origin", "master").returncode
        return "pushed" if rc == 0 else "push-failed"
    except Exception:
        return "error"


def _cleanup_offsite(offsite):
    import os
    files = sorted(f for f in os.listdir(offsite)
                   if f.startswith("db-weekly-"))
    for f in files[:-KEEP_OFFSITE]:
        try:
            os.remove(os.path.join(offsite, f))
        except OSError:
            pass


def _cleanup(d, pattern, keep):
    files = sorted(d.glob(pattern))
    for p in files[:-keep]:
        try:
            p.unlink()
        except OSError:
            pass

