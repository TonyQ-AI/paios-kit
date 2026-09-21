# -*- coding: utf-8 -*-
"""Stop hook 入口：增量同步 + 候选标记 + 每日滚动备份 + 原子库推送。

铁律（ZCode hook 陷阱 8）：
- stdout 绝对静默——ZCode 把 hook 的 stdout 当严格 schema JSON 校验，
  任何输出都会被判失败并报「hook 报错」；诊断信息只走 stderr。
- 永远 exit 0，尽力而为，单步失败不连带。
"""
import os
import sys
import traceback


def _proxy_reachable(url, timeout=0.3):
    """探测代理端口是否真的在本机监听（分发用户可能根本不用代理）。"""
    import socket
    from urllib.parse import urlparse
    p = urlparse(url)
    host = p.hostname or "127.0.0.1"
    if not p.port:
        return False
    try:
        with socket.create_connection((host, p.port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_local_proxy():
    """按机器级本地配置补上兜底代理（个人值不进分发包）。

    背景：push_atoms 的代理自 2026-09-21 起改为环境变量开关（PAIOS_GIT_PROXY，
    分发包不硬编码代理）；本机 GitHub 直连不通，变量缺失会让原子推送每次失败
    （仅有顶栏警示，推送长期不推进）。

    取值来源：`<安装根>/data/local-config.json` 的 `git_proxy` 键——该文件只在
    本机存在，分发包默认没有，故分发用户行为不变（直连）。启用条件：
    ① 环境变量 PAIOS_GIT_PROXY 未设置（显式值永远优先）；
    ② 本地配置里有 git_proxy；
    ③ 该端口确实在本机监听（代理软件没开时不注入死代理）。
    """
    if os.environ.get("PAIOS_GIT_PROXY", "").strip():
        return
    from . import local_conf
    proxy = local_conf.get("git_proxy")
    if not proxy:
        return
    if not _proxy_reachable(proxy):
        return
    os.environ["PAIOS_GIT_PROXY"] = proxy
    print("hook: PAIOS_GIT_PROXY 未设置，已启用本地配置代理 %s" % proxy,
          file=sys.stderr)


def main():
    _ensure_local_proxy()
    from . import indexer, candidates, backup, push_atoms
    for name, fn in (("sync", indexer.sync), ("candidates", candidates.mark),
                     ("backup", backup.run_daily),
                     ("push_atoms", push_atoms.push_atoms)):
        try:
            fn()
        except Exception:
            traceback.print_exc(file=sys.stderr)
    # stdout 保持静默：一个字节都不输出
