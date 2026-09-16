# -*- coding: utf-8 -*-
"""Stop hook 入口：增量同步 + 候选标记 + 每日滚动备份。

铁律（ZCode hook 陷阱 8）：
- stdout 绝对静默——ZCode 把 hook 的 stdout 当严格 schema JSON 校验，
  任何输出都会被判失败并报「hook 报错」；诊断信息只走 stderr。
- 永远 exit 0，尽力而为，单步失败不连带。
"""
import sys
import traceback


def main():
    from . import indexer, candidates, backup, push_atoms
    for name, fn in (("sync", indexer.sync), ("candidates", candidates.mark),
                     ("backup", backup.run_daily),
                     ("push_atoms", push_atoms.push_atoms)):
        try:
            fn()
        except Exception:
            traceback.print_exc(file=sys.stderr)
    # stdout 保持静默：一个字节都不输出
