# -*- coding: utf-8 -*-
"""prpack · 把 Premiere Pro 工程连同素材干净地打包归档。

替代 PR 自带的项目管理器（Project Manager）：
  - 子剪辑不再各复制一份母片（按物理文件去重）
  - 输出目录还原素材箱层级（不再永远平铺）
  - 先出计划，人工核对后再执行
"""

__version__ = "0.2.1"

from .prproj import ProjectModel, BinEntry, SequenceInfo, sanitize_name, norm_key  # noqa: F401
from .plan import Plan, PlanFile, build_plan  # noqa: F401
from .prune import PruneResult, prune_items  # noqa: F401

__all__ = [
    "__version__",
    "ProjectModel",
    "BinEntry",
    "SequenceInfo",
    "PruneResult",
    "prune_items",
    "sanitize_name",
    "norm_key",
    "Plan",
    "PlanFile",
    "build_plan",
]
