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


def _online_backup(dest):
    if not SOURCE_DB.exists():
        return "no-source"
    src = sqlite3.connect("file:" + str(SOURCE_DB).replace("\\", "/") + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def run_daily():
    """每日首次触发：当天全量快照（已存在则跳过）+ 会话级快照 + 异地周备份检查。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / ("db-%s.sqlite" % time.strftime("%Y%m%d"))
    if dest.exists():
        result = "exists"
    else:
        result = _online_backup(dest)
        if result == "no-source":
            return result
    _cleanup(BACKUP_DIR, "db-*.sqlite", KEEP_DAILY)
    _intraday()
    _offsite()
    return result


def _intraday():
    """每个会话结束都拍一份当天快照，保最近 KEEP_INTRADAY 份。"""
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    dest = INTRADAY_DIR / ("snap-%s.sqlite" % time.strftime("%Y%m%d-%H%M"))
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

