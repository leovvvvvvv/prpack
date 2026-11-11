# -*- coding: utf-8 -*-
"""prpack · 校验层

打包完成后，用**新工程自己**回答三个问题：
  1. 还能读吗？（XML 可解析、Media 条数不变）
  2. 文件都在吗？（每条 FilePath 在磁盘上真实存在）
  3. 体积对不对？（与 plan 预期比对）

不通过就不许交付。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .plan import Plan
from .prproj import ProjectModel


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyResult:
    checks: List[Check] = field(default_factory=list)
    report_path: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))


def verify_package(
    original: ProjectModel,
    packed_project: Path,
    plan: Plan,
    tolerance: float = 0.05,
) -> VerifyResult:
    res = VerifyResult()
    packed_project = Path(packed_project)

    # 1. 新工程可解析
    try:
        packed = ProjectModel.load(packed_project)
        res.add("新工程可解析", True, f"{packed_project.name}")
    except Exception as exc:                       # noqa: BLE001
        res.add("新工程可解析", False, str(exc))
        return res

    # 2. Media 条数一致
    res.add(
        "媒体条数一致",
        len(packed.media_files) == len(original.media_files),
        f"原 {len(original.media_files)} → 新 {len(packed.media_files)}",
    )

    # 3. 素材箱条数一致
    res.add(
        "素材箱层级一致",
        len(packed.bins) == len(original.bins),
        f"原 {len(original.bins)} 个箱 → 新 {len(packed.bins)} 个箱",
    )

    # 4. 每条路径都落地
    missing = [fp for fp in packed.media_files if not os.path.exists(fp)]
    res.add(
        "全部媒体在线",
        not missing,
        "0 个离线" if not missing else f"{len(missing)} 个离线，例如 {missing[:3]}",
    )

    # 5. 路径已指向新位置
    root = str(Path(plan.output_root).resolve()).lower()
    outside = [fp for fp in packed.media_files if not fp.lower().startswith(root)]
    res.add(
        "路径已全部指向输出目录",
        not outside,
        "全部在包内" if not outside else f"{len(outside)} 个仍指向包外，例如 {outside[:3]}",
    )

    # 6. 体积：只算 plan 里点名的文件，别把工程和报告算进去
    expect = plan.total_bytes
    actual = 0
    for f in plan.files:
        p = Path(plan.output_root) / f.dst_abs
        try:
            actual += p.stat().st_size
        except OSError:
            pass
    ratio = (actual / expect) if expect else 0
    res.add(
        "体积符合预期",
        abs(ratio - 1) <= tolerance,
        f"预期 {expect / 1024**3:.2f} GB → 实际 {actual / 1024**3:.2f} GB（偏差 {abs(ratio - 1) * 100:.1f}%）",
    )

    return res


def _dir_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def write_report(
    path: Path,
    plan: Plan,
    stats: dict,
    copy_res,
    relink_res,
    verify_res: VerifyResult,
) -> Path:
    """写一份人看的报告。"""
    path = Path(path)
    lines: List[str] = []
    a = lines.append

    a(f"# 打包报告 · {plan.project_name}")
    a("")
    a(f"- 原工程：`{plan.project_path}`")
    a(f"- 输出目录：`{plan.output_root}`")
    a(f"- 结构模式：`{plan.mode}`")
    a(f"- plan 指纹：`{plan.fingerprint()}`")
    a("")

    a("## 一、素材统计")
    a("")
    a("| 项 | 值 |")
    a("|---|---|")
    a(f"| 素材箱数 | {stats.get('bin_count', 0)} |")
    a(f"| 面板项目项 | {stats.get('bin_entries', 0)} |")
    a(f"| 物理文件（去重后） | {stats.get('unique_files', 0)} |")
    a(f"| 计划复制文件数 | {plan.total_files} |")
    a(f"| 计划体积 | {plan.total_bytes / 1024**3:.2f} GB |")
    a(f"| 未匹配文件 | {stats.get('unmatched', 0)} |")
    a(f"| 离线文件 | {stats.get('offline', 0)} |")
    a("")

    if copy_res is not None:
        a("## 二、复制结果")
        a("")
        a("| 项 | 值 |")
        a("|---|---|")
        a(f"| 已复制 | {copy_res.copied} |")
        a(f"| 已跳过（已存在） | {copy_res.skipped} |")
        a(f"| 失败 | {copy_res.failed} |")
        a(f"| 复制量 | {copy_res.bytes_copied / 1024**3:.2f} GB |")
        a("")
        if copy_res.errors:
            a("**失败明细**")
            a("")
            for e in copy_res.errors:
                a(f"- `{e['src']}` → {e['error']}")
            a("")

    if relink_res is not None:
        a("## 三、工程重写")
        a("")
        a("| 项 | 值 |")
        a("|---|---|")
        a(f"| 新工程 | `{relink_res.output_path}` |")
        a(f"| 绝对路径替换 | {relink_res.replaced_abs} 处 |")
        a(f"| 相对路径替换 | {relink_res.replaced_rel} 处 |")
        a(f"| 本机临时路径清空 | {relink_res.cleared_temp} 处 |")
        a("")
        for w in relink_res.warnings:
            a(f"> ⚠️ {w}")
            a("")

    if verify_res is not None:
        a("## 四、校验")
        a("")
        a("| 检查项 | 结果 | 说明 |")
        a("|---|---|---|")
        for c in verify_res.checks:
            a(f"| {c.name} | {'✅ 通过' if c.ok else '❌ 失败'} | {c.detail} |")
        a("")
        a(f"**总体结论：{'✅ 可以交付' if verify_res.ok else '❌ 不可交付'}**")
        a("")

    if plan.conflicts:
        a("## 五、重名冲突")
        a("")
        for c in plan.conflicts:
            a(f"- `{c['dst']}` ← `{c['src']}`")
        a("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
