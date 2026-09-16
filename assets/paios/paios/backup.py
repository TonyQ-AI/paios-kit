# -*- coding: utf-8 -*-
"""ZCode 主库每日滚动备份（sqlite3 在线 backup API，保 7 份）。

注意：必须用 backup API 而非文件复制——ZCode 运行中主库可能处于
WAL 模式，文件级复制会得到数据页与日志不一致的快照（BLUEPRINT 12.3）。
"""
import sqlite3
import time

from . import SOURCE_DB, BACKUP_DIR

KEEP = 7


def run_daily():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / ("db-%s.sqlite" % time.strftime("%Y%m%d"))
    if dest.exists():
        return "exists"
    if not SOURCE_DB.exists():
        return "no-source"
    src = sqlite3.connect("file:" + str(SOURCE_DB).replace("\\", "/") + "?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    backups = sorted(BACKUP_DIR.glob("db-*.sqlite"))
    for p in backups[:-KEEP]:
        try:
            p.unlink()
        except OSError:
            pass
    return "ok"
