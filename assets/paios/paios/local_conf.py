# -*- coding: utf-8 -*-
"""机器级本地设置（个人值不进分发包）：读 `<安装根>/data/local-config.json`。

分发默认：文件不存在 → 全部机器级功能关闭（推送直连、不做异地备份），
行为与不带任何个人配置的干净安装一致。只有本机维护者会创建这个文件。

支持的键（都可选）：
  git_proxy   原子库推送的兜底代理，如 "http://127.0.0.1:7890"
              （仅当环境变量 PAIOS_GIT_PROXY 未设置且该端口确实在监听时启用）
  offsite_dir 主库异地周备份目标目录，如 "D:\\paios-backup"
              （缺失 = 不做异地备份）

为什么放 data/ 而不是写进代码：分发包按白名单复制 `paios/` 包源码，
data/ 目录由安装器在目标机新建，个人路径与端口永远不会被打包带走。
"""
import json

_CACHE = None
_CONFIG_NAME = "local-config.json"


def _load():
    global _CACHE
    if _CACHE is None:
        # 调用时读取 BASE_DIR（不在模块顶层绑定）：便于测试与其他进程内改根
        from . import BASE_DIR
        try:
            raw = (BASE_DIR / "data" / _CONFIG_NAME).read_text(encoding="utf-8")
            _CACHE = json.loads(raw)
        except Exception:
            _CACHE = {}
        if not isinstance(_CACHE, dict):
            _CACHE = {}
    return _CACHE


def get(key, default=""):
    """取一个字符串型设置；缺失/类型不符/空白 一律返回 default。"""
    v = _load().get(key)
    return v.strip() if isinstance(v, str) and v.strip() else default


def reload():
    """丢弃缓存（配置改动后立即生效用；进程内一般不需要）。"""
    global _CACHE
    _CACHE = None
