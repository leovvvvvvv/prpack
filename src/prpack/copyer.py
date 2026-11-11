# -*- coding: utf-8 -*-
"""prpack · 执行层

按 plan 复制文件。安全约束：
  1. plan 指纹必须与生成时一致（防止计划被悄悄改过）。
  2. 目标目录必须不存在或为空 —— 绝不覆盖已有产物。
  3. 目标文件已存在且大小一致 → 记为 skipped，支持断点续传。
  4. 大文件分块复制，边复制边回报进度。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .plan import Plan, PlanFile

CHUNK = 8 * 1024 * 1024   # 8 MB


class CopyError(RuntimeError):
    pass


@dataclass
class CopyResult:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_copied: int = 0
    errors: List[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.copied + self.skipped + self.failed


def _long(path: str) -> str:
    """Windows 长路径前缀。超过 240 字符时启用，避免 MAX_PATH 限制。"""
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    if len(p) > 240 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def prepare_output(root: Path, force: bool = False) -> None:
    """目标目录检查。已有内容就拒（除非 force 且明确允许）。"""
    root = Path(root)
    if root.exists():
        if not root.is_dir():
            raise CopyError(f"输出路径已存在且不是目录：{root}")
        if any(root.iterdir()) and not force:
            raise CopyError(
                f"输出目录非空，拒绝覆盖：{root}\n"
                f"（换一个输出路径，或加 --force 后自行清理）"
            )
    else:
        root.mkdir(parents=True, exist_ok=True)


def copy_plan(
    plan: Plan,
    plan_file: Optional[Path] = None,
    progress: Optional[Callable[[int, int, PlanFile], None]] = None,
    resume: bool = True,
) -> CopyResult:
    """执行 plan。返回统计；单个文件失败不中断整体，全部记入 errors。"""
    root = Path(plan.output_root)
    res = CopyResult()

    # 指纹校验
    if plan_file is not None:
        try:
            import json as _json

            recorded = _json.loads(Path(plan_file).read_text(encoding="utf-8")).get("fingerprint")
            if recorded and recorded != plan.fingerprint():
                raise CopyError("plan 指纹不一致：计划文件在生成后被修改过，拒绝执行")
        except FileNotFoundError:
            pass

    for i, f in enumerate(plan.files, 1):
        dst = root / f.dst_abs
        try:
            if not f.exists:
                raise FileNotFoundError("源文件不存在（离线）")

            dst.parent.mkdir(parents=True, exist_ok=True)

            if resume and dst.exists() and dst.stat().st_size == f.size:
                res.skipped += 1
            else:
                _copy_one(Path(f.src), dst)
                res.copied += 1
                res.bytes_copied += f.size
        except Exception as exc:                     # noqa: BLE001  逐个文件兜住
            res.failed += 1
            if len(res.errors) < 20:
                res.errors.append({"src": f.src, "dst": str(dst), "error": str(exc)})

        if progress is not None:
            progress(i, len(plan.files), f)

    return res


def _copy_one(src: Path, dst: Path) -> None:
    """分块复制并保留 mtime。先写 .part 再改名，避免半截文件被当成完成。"""
    tmp = dst.with_name(dst.name + ".part")
    src_s, dst_s = _long(str(src)), _long(str(tmp))
    with open(src_s, "rb") as fi, open(dst_s, "wb") as fo:
        while True:
            buf = fi.read(CHUNK)
            if not buf:
                break
            fo.write(buf)
    shutil.copystat(src_s, dst_s)
    os.replace(dst_s, _long(str(dst)))
