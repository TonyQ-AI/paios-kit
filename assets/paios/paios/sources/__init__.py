# -*- coding: utf-8 -*-
"""会话来源适配器。

ZCode 是主源：直接读 `~/.zcode/cli/db/db.sqlite`，抽取逻辑留在 indexer 里
（`_fetch_sessions` / `_extract_*`），因为它天然按「来源即主库」写成。

DSH 是第二个源，见 `sources/dsh.py`：DSH 把会话落成跨帧 zstd 的 JSONL 容器，
没有可编程的读取入口（CLI 只有 boot/plugin，GUI 的 /export 是手动动作），
所以由本侧只读解析。

新增第三个 harness 时按同一形状加一个模块：`available()` + `scan()`，
解析结果喂给 indexer 的 upsert——解析与索引分离。
"""
