# -*- coding: utf-8 -*-
"""ZCode 主库双层滚动备份 + 异地周备份（sqlite3 在线 backup API，禁止文件复制——WAL 不一致快照）。

- 日级全量：data/backup/db-YYYYMMDD.sqlite，每日首会话备份一份，保 3 天
- 会话级快照：data/backup/intraday/snap-YYYYMMDD-HHMM.sqlite，
  每个会话结束都拍一份，保最近 2 份——把「当天内主库损坏」的风险窗口
  从最长 24h 压到约 1 个会话间隔。
- 增量链路（新）：paios/zbackup.py —— 周 base（zstd）+ 20 分钟 delta + 每周演练，
  由计划任务 PAIOS-ZCode-Incremental 驱动；本文件的日备/intraday 是它的迁移期兜底。
- 异地周备份：目标目录取自机器级本地配置（<安装根>/data/local-config.json 的
  offsite_dir，如 "D:\\paios-backup"），每周后的首次触发拷贝本周最新日级备份，
  保 3 份——防整盘灾难。**分发包默认没有该配置 → 此功能整体关闭**（不会往
  别人的盘里写）；拷贝的是已通过 backup API 落盘并验证过的快照，不对主库二次加压。
"""
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

from . import SOURCE_DB, BACKUP_DIR

# 日级 3 份（用户裁定：三日回溯足够——更早的数据坏了说明三天没人碰，
# 有价值内容早已沉淀进原子库/git，可再生）+ 会话级 2 份，本地合计 ≈1.9GB 封顶。
KEEP_DAILY = 3
# 实测：backup API 快照是全量文件（~710MB/份），非增量页。
KEEP_INTRADAY = 2
INTRADAY_DIR = BACKUP_DIR / "intraday"

KEEP_OFFSITE = 3
OFFSITE_MAX_AGE_DAYS = 7  # 距上次异地备份超过 7 天且当日无周备 → 补拷

# DSH（DeepSeek Harness）数据快照（2026-10-18）：DSH 重装后，会话容器是**唯一
# 不可再生**的东西；storages（工作区注册）与 profiles（插件配置，含 PAIOS MCP
# 注册块）一起带走，免得重装后还要手工重配。容器本身已是 zstd，快照用
# ZIP_STORED 不再二次压缩（约 70MB/份）。分发包用户没装 DSH → 整体跳过。
DSH_SRC_DIR = Path.home() / ".dsh"
DSH_BACKUP_DIR = BACKUP_DIR / "dsh"
KEEP_DSH = 3   # 用户裁定：7 份没必要，3 份（≈3 天历史）足够，配合 intraday 2 份保底
DSH_SUBDIRS = ("sessions", "storages", "profiles")
DSH_SKIP_NAMES = {"node_modules", "cache", "__pycache__"}

# DSH 会话级快照（与 ZCode 的 intraday 同口径）：DSH 没有「会话结束」钩子，
# 最细粒度就是计划任务的 10 分钟轮询——检测到容器 mtime 变化才拍，空闲不写。
DSH_INTRADAY_DIR = DSH_BACKUP_DIR / "intraday"
KEEP_DSH_INTRADAY = 2

# 「按份数保留」只防无限堆文件，不防单份随数据增长把总量顶上去（7 份 × 1GB = 7GB）。
# 再加一条按总容量封顶：本地与异地各 4GB，超了从最旧删，但永远至少留 1 份日备。
DSH_MAX_BYTES = 4 * 1024 ** 3


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
    try:
        run_dsh_daily()
    except Exception:
        pass  # DSH 快照失败绝不连带 ZCode 主库备份
    return result


def _intraday():
    """每个会话结束都拍一份当天快照，保最近 KEEP_INTRADAY 份。

    命名到秒 + 写入带 pid 的 .tmp：同分钟/同秒的并发 hook 不再互相撞车
    （此前分钟级命名曾在并发时写出截断文件与孤儿 journal）。

    **迁移开关**：`data/local-config.json` 里 `"zcode_incremental": "on"` 时
    整段跳过——新链路（paios/zbackup.py：20 分钟增量 + 周 base）已覆盖
    「每会话一份」的恢复点粒度，不必每次会话再全量拷 1.2GB。默认不开，
    等新链路连续演练通过（设计建议 2 周）后由用户开启。
    """
    from . import local_conf
    # P1-1（DSH 复查）：只有明确的 on/1/true 才跳过旧链路；写 "off" 不得误停
    if (local_conf.get("zcode_incremental") or "").strip().lower() in ("on", "1", "true"):
        return
    INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    _clean_stale(INTRADAY_DIR)
    dest = INTRADAY_DIR / ("snap-%s.sqlite" % time.strftime("%Y%m%d-%H%M%S"))
    _online_backup(dest)
    _cleanup(INTRADAY_DIR, "snap-*.sqlite", KEEP_INTRADAY)


def _offsite():
    """异地周备份：最新日级备份拷到配置的异地目录，超 7 天未备则补拷，保 3 份。

    异地目录来自机器级本地配置；未配置（分发包默认）→ 本函数整体不做事。

    **2026-10-18 修复**：原实现用裸 `shutil.copy2` 且只按文件名判断「本周已备」，
    实测 F 盘 4 份异地备份里 3 份是半截文件（sqlite 校验不过），而「本周已备/
    不足 7 天」的短路让坏文件永久留存——典型的「只写不验」。现在与本地日备同
    纪律：拷到 .tmp → `_valid_sqlite` 校验 → `os.replace`；发现最新的异地文件
    是坏的先删再重做。
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
                      if f.startswith("db-weekly-") and f.endswith(".sqlite"))
    if existing:
        last = existing[-1]
        last_path = os.path.join(offsite, last)
        same_week = last == os.path.basename(dest)
        if _valid_sqlite(last_path):
            if same_week:
                return                      # 本周已备且校验通过
            try:
                if time.time() - os.path.getmtime(last_path) < \
                        OFFSITE_MAX_AGE_DAYS * 86400:
                    return                  # 不足 7 天且校验通过
            except OSError:
                pass
        else:
            # 坏的异地文件：删掉重做（否则永远停在坏文件上）
            try:
                os.unlink(last_path)
            except OSError:
                return
    tmp = dest + ".%d.tmp" % os.getpid()
    try:
        shutil.copy2(str(latest), tmp)
        if _valid_sqlite(tmp):
            os.replace(tmp, dest)
        else:
            os.unlink(tmp)
            return
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return
    _cleanup_offsite(offsite)
    print("config-layer sync:", _config_layer_sync())
    print("shared-assets check:", _shared_assets_check())


def _dsh_offsite_dir():
    """DSH 快照异地目录（机器级配置 dsh_offsite_dir；缺失 = 不做异地）。"""
    from . import local_conf
    return local_conf.get("dsh_offsite_dir")


def _offsite_dsh():
    """DSH 快照异地同步：本地 backup/dsh 的最新快照拷到配置目录，保 KEEP_DSH 份。

    与 zcode 异地备份同一纪律：先写 .tmp、按文件大小校验、os.replace；只按
    `dsh-` 前缀轮转，绝不碰目标目录里别人的文件。
    """
    import os
    offsite = _dsh_offsite_dir()
    if not offsite:
        return "off"
    local = sorted(DSH_BACKUP_DIR.glob("dsh-*.zip")) if DSH_BACKUP_DIR.is_dir() else []
    if not local:
        return "no-local"
    try:
        os.makedirs(offsite, exist_ok=True)
    except OSError:
        return "unavailable"
    copied = 0
    for src in local[-KEEP_DSH:]:
        dst = os.path.join(offsite, src.name)
        try:
            if os.path.getsize(dst) == os.path.getsize(src):
                continue                    # 已同步
        except OSError:
            pass
        tmp = dst + ".%d.tmp" % os.getpid()
        try:
            shutil.copy2(str(src), tmp)
            if os.path.getsize(tmp) != os.path.getsize(src):
                os.unlink(tmp)
                continue
            os.replace(tmp, dst)
            copied += 1
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    # 份数 + 总容量双闸门（只轮转 dsh- 前缀，不碰目标目录里别人的文件）
    _cleanup(Path(offsite), "dsh-*.zip", KEEP_DSH, DSH_MAX_BYTES)
    for f in os.listdir(offsite):
        if f.startswith("dsh-") and f.endswith(".tmp"):
            try:
                os.unlink(os.path.join(offsite, f))
            except OSError:
                pass
    return "copied:%d" % copied


def _shared_assets_check():
    """搭车体检：跑共享资产体检脚本（--light 只验在位与取值，不遍历大目录）。

    2026-09-24 落地了「多工具共用公共资产」（环境变量驱动的缓存与编译产物、
    跨工具 skill 单一真源），每周自动核一遍，防的是"某个工具/项目又自己另建了一份"
    这类静默回退。只读，任何失败只回状态串，绝不影响备份流程。
    """
    import os
    import subprocess

    script = os.path.join(os.path.expanduser("~"), ".zcode", "scripts",
                          "check-shared-assets.py")
    if not os.path.isfile(script):
        return "no-script"
    try:
        p = subprocess.run(["python", script, "--light"], capture_output=True,
                           encoding="utf-8", errors="replace",
                           creationflags=0x08000000, timeout=180)
        if p.returncode == 0:
            return "all-ok"
        bad = [l.strip() for l in (p.stdout or "").splitlines()
               if l.strip().startswith("❌")]
        return "issues(%d): %s" % (len(bad), " ; ".join(x[:60] for x in bad[:3]))
    except Exception:
        return "error"

def _config_layer_sync():
    """搭车兜底：把「配置与公共资产」仓库的未提交改动提交并推送。

    搭在异地周备份这一趟上（每周一次），专抓「改了规则/公共资产但那个会话忘了提交」。
    覆盖 ~/.zcode（规则唯一权威）与 ~/.agents（跨工具 skill 公共目录，2026-09-24 建立）。
    只对白名单仓库生效（其 .gitignore 是白名单写法，不会误收运行态数据/私钥）；
    无远端的仓库只提交不推送；任何失败都只返回状态字符串，绝不影响备份流程本身。
    """
    import os
    import pathlib

    home = pathlib.Path(os.path.expanduser("~"))
    repos = [("zcode", home / ".zcode"), ("agents", home / ".agents")]
    return " ".join("%s=%s" % (name, _sync_one_repo(cfg)) for name, cfg in repos)


def _sync_one_repo(cfg):
    """单个仓库的提交与推送；任何失败只回状态串，绝不抛异常。"""
    import os
    import subprocess
    import tempfile

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
        # 无远端的仓库（如 ~/.agents 暂未建远端）只提交不推送
        if not _git("remote").stdout.split():
            return "committed-no-remote" if dirty else "up-to-date-no-remote"
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "master"
        if not _git("log", "--oneline", "origin/%s..HEAD" % branch).stdout.strip():
            return "up-to-date"
        rc = _git("push", "origin", branch).returncode
        return "pushed" if rc == 0 else "push-failed"
    except Exception:
        return "error"


def _cleanup_offsite(offsite):
    """异地目录：轮转保留 KEEP_OFFSITE 份，并清掉只属于我们的边车残渣。

    该目录可能与用户其他备份共处，故一律**限定 db-weekly- 前缀**，
    绝不碰同目录的其他文件。
    """
    import os
    names = sorted(os.listdir(offsite))
    files = [f for f in names if f.startswith("db-weekly-") and f.endswith(".sqlite")]
    for f in files[:-KEEP_OFFSITE]:
        try:
            os.remove(os.path.join(offsite, f))
        except OSError:
            pass
    for f in names:
        if f.startswith("db-weekly-") and f.endswith(("-journal", "-wal", "-shm", ".tmp")):
            try:
                os.remove(os.path.join(offsite, f))
            except OSError:
                pass


def _valid_dsh_zip(path, full=False):
    """快照有效性。full=False 只做轻校验（hook 每次都会走到这里，别为 76MB
    文件做全量 CRC）；full=True 在刚落盘后做一次 CRC 全验。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not any(n.startswith("sessions/") for n in names):
                return False
            if full:
                return zf.testzip() is None
            return len(names) > 0
    except Exception:
        return False


def _snapshot_dsh(dest):
    """把 ~/.dsh 的 sessions/storages/profiles 打成 ZIP。

    原子落盘（.tmp → 校验 → os.replace）；正在被 DSH 写入/独占的容器本轮跳过。
    """
    tmp = dest.with_name(dest.name + ".%d.tmp" % os.getpid())
    n = skipped = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            for sub in DSH_SUBDIRS:
                root = DSH_SRC_DIR / sub
                if not root.is_dir():
                    continue
                # os.walk 而非 rglob：profiles/node_modules 里有指向已删包的失效
                # junction，rglob 的 scandir 会抛 FileNotFoundError；os.walk 默认
                # 忽略不可读目录，且这里直接剪枝掉 node_modules/cache。
                for dirpath, dirnames, filenames in os.walk(str(root),
                                                           followlinks=False):
                    dirnames[:] = [d for d in dirnames if d not in DSH_SKIP_NAMES]
                    for fn in filenames:
                        p = Path(dirpath) / fn
                        rel = p.relative_to(DSH_SRC_DIR)
                        if any(part in DSH_SKIP_NAMES for part in rel.parts):
                            continue
                        try:
                            zf.write(str(p), arcname=str(rel).replace("\\", "/"))
                            n += 1
                        except OSError:
                            skipped += 1
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if n == 0 or not _valid_dsh_zip(tmp, full=True):
        try:
            tmp.unlink()
        except OSError:
            pass
        return "invalid"
    os.replace(str(tmp), str(dest))
    return "ok" if not skipped else "ok(%d skipped)" % skipped


def snapshot_dsh_intraday():
    """DSH 会话级快照（调用方负责「有新写入才拍」；保最近 KEEP_DSH_INTRADAY 份）。

    对应 ZCode 的 intraday 层：ZCode 靠 Stop hook 做到「每个会话结束一份」；
    DSH 没有这个钩子，最细粒度是计划任务的 10 分钟轮询——所以由
    `scripts/dsh_index_now.py` 在检测到 dsh_updated>0 时调用。
    单份约 76MB / 1.3s（ZCode 是 1.2GB / 10s），保留 2 份约 152MB。
    """
    if not DSH_SRC_DIR.is_dir():
        return "no-source"
    DSH_INTRADAY_DIR.mkdir(parents=True, exist_ok=True)
    dest = DSH_INTRADAY_DIR / ("snap-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))
    result = _snapshot_dsh(dest)
    # intraday 也受总容量约束：单份大到 4GB 时，2 份就是 8GB，不能绕过闸门
    _cleanup(DSH_INTRADAY_DIR, "snap-*.zip", KEEP_DSH_INTRADAY, DSH_MAX_BYTES)
    return result


def run_dsh_daily():
    """DSH 数据每日快照（与 ZCode 日级同一趟触发；跨天一份，保 KEEP_DSH 份）。

    还原入口：scripts/restore_dsh.py（先备份现状，再解压覆盖）。
    """
    if not DSH_SRC_DIR.is_dir():
        return "no-source"
    DSH_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ded = DSH_BACKUP_DIR / ("dsh-%s.zip" % time.strftime("%Y%m%d"))
    if ded.exists() and _valid_dsh_zip(ded):
        result = "exists"
    else:
        if ded.exists():
            try:
                ded.unlink()
            except OSError:
                pass
        result = _snapshot_dsh(ded)
    _cleanup(DSH_BACKUP_DIR, "dsh-*.zip", KEEP_DSH, DSH_MAX_BYTES)
    try:
        _offsite_dsh()   # 配置了 dsh_offsite_dir 才动作；未配置静默跳过
    except Exception:
        pass
    return result


def _cleanup_log(msg):
    """清理留痕：容量闸门触发 = 恢复历史被压缩，必须可追溯（不能静默删）。"""
    try:
        with open(str(BACKUP_DIR / "cleanup.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def _cleanup(d, pattern, keep, max_bytes=None):
    """按份数保留最近 keep 份；给了 max_bytes 时再按总容量从最旧删（至少留 1 份）。

    容量触发会写 `backup/cleanup.log`：份数少了要知道是哪天、删了什么。
    """
    deleted = 0
    files = sorted(d.glob(pattern))
    for p in files[:-keep]:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    if max_bytes:
        files = sorted(d.glob(pattern))
        total = 0
        for p in files:
            try:
                total += p.stat().st_size
            except OSError:
                pass
        while len(files) > 1 and total > max_bytes:
            p = files.pop(0)
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            try:
                p.unlink()
                total -= sz
                deleted += 1
            except OSError:
                pass
        if deleted:
            try:
                kept = len(list(d.glob(pattern)))
            except OSError:
                kept = -1
            _cleanup_log("容量闸门 %s：删 %d 份，保留 %d 份，当前 %.1fMB（上限 %.1fMB）" % (
                d, deleted, kept, total / 1e6, max_bytes / 1e6))
    elif deleted:
        try:
            kept = len(list(d.glob(pattern)))
        except OSError:
            kept = -1
        _cleanup_log("份数闸门 %s：删 %d 份，保留 %d 份" % (d, deleted, kept))

