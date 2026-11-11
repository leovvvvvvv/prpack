# -*- coding: utf-8 -*-
"""prpack · 规划层

把「素材箱位置」翻译成「输出目录里的落位」，产出一份可人工核对的 plan.json。

设计要点：
  1. **按物理文件去重**。198 个子剪辑共享 112 个文件，这里只出 112 条。
  2. **位置以素材箱为准**（bin 模式，默认）。同一个文件被多个箱引用时，取层级最浅的那个，
     其余在 `shared_bins` 里记录，报告里列出来。
  3. **先出计划，不落盘**。plan 是给人看的，也是执行的唯一依据 —— 执行时校验 plan 指纹。
  4. 冲突（不同源文件抢同一个落位）加 `_1`、`_2` 后缀，并在 `conflicts` 里标出。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePath
from typing import Dict, List, Optional, Set, Tuple

from .prproj import ProjectModel, norm_key, sanitize_name

PLAN_VERSION = 1

# 需要整目录/整组跟着走的依赖格式
_SIDECAR_DIRS = {
    ".braw": ".sidecar",
    ".r3d": ".RDC",
}


@dataclass
class PlanFile:
    src: str
    dst_rel: str          # 相对 output_root，用 / 分隔
    size: int
    name: str
    bin_path: Tuple[str, ...] = ()
    shared_bins: List[Tuple[str, ...]] = field(default_factory=list)
    role: str = "media"   # media / dep / unmatched
    exists: bool = True

    @property
    def dst_abs(self) -> str:
        return self.dst_rel.replace("/", os.sep)


@dataclass
class Plan:
    project_path: str
    project_name: str
    output_root: str
    mode: str
    files: List[PlanFile] = field(default_factory=list)
    conflicts: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    version: int = PLAN_VERSION

    # ---- 汇总 ----

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def fingerprint(self) -> str:
        """plan 指纹：执行时比对，防止计划被悄悄改过。"""
        payload = json.dumps(
            {
                "v": self.version,
                "project": self.project_path,
                "root": self.output_root,
                "mode": self.mode,
                "files": sorted(f"{f.src}|{f.dst_rel}|{f.size}" for f in self.files),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["totals"] = {
            "files": self.total_files,
            "bytes": self.total_bytes,
            "media": sum(1 for f in self.files if f.role == "media"),
            "deps": sum(1 for f in self.files if f.role == "dep"),
            "unmatched": sum(1 for f in self.files if f.role == "unmatched"),
        }
        d["fingerprint"] = self.fingerprint()
        return d

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Plan":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        files = [PlanFile(**f) for f in data.pop("files")]
        data.pop("totals", None)
        data.pop("fingerprint", None)
        # bin_path / shared_bins 会被 JSON 转成 list，恢复成 tuple
        for f in files:
            f.bin_path = tuple(f.bin_path)
            f.shared_bins = [tuple(b) for b in f.shared_bins]
        return cls(files=files, **data)


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _deps_for(src: str) -> List[str]:
    """找源文件的依赖目录/文件。第一版只认 BRAW 与 R3D。"""
    p = Path(src)
    suffix = p.suffix.lower()
    out: List[str] = []

    sidecar_name = _SIDECAR_DIRS.get(suffix)
    if sidecar_name:
        cand = p.with_suffix(sidecar_name)
        if cand.is_dir():
            for f in sorted(cand.rglob("*")):
                if f.is_file():
                    out.append(str(f))

    # 同名 .aep（AE 动态链接工程）
    aep = p.with_suffix(".aep")
    if aep.is_file():
        out.append(str(aep))

    return out


def build_plan(
    model: ProjectModel,
    output_root: Path,
    mode: str = "bin",
    include_deps: bool = True,
    skip_unmatched: bool = False,
) -> Plan:
    """生成打包计划。只读，不复制任何文件。"""
    output_root = Path(output_root).resolve()
    plan = Plan(
        project_path=str(model.path),
        project_name=model.path.stem,
        output_root=str(output_root),
        mode=mode,
    )

    # 1. 收集：物理文件 → 候选落位
    cand: Dict[str, List[Tuple[Tuple[str, ...], str]]] = {}
    for e in model.media_items():
        if not e.filepath:
            continue
        cand.setdefault(norm_key(e.filepath), []).append((e.bin_path, e.name))

    # 2. 每个物理文件选一个落位（层级最浅，其次字母序）
    chosen: List[Tuple[str, Tuple[str, ...], str, List[Tuple[str, ...]]]] = []
    for k, entries in cand.items():
        entries.sort(key=lambda x: (len(x[0]), x[0], x[1]))
        bin_path, name = entries[0]
        others = [b for b, _ in entries[1:] if b != bin_path]
        src = next(e.filepath for e in model.media_items() if norm_key(e.filepath) == k)
        chosen.append((src, bin_path, name, others))

    # 3. 落位计算 + 冲突处理
    taken: Dict[str, str] = {}   # dst_rel.lower() -> src
    for src, bin_path, name, others in sorted(chosen, key=lambda x: (x[1], Path(x[0]).name)):
        fname = PurePath(src).name
        parts = [sanitize_name(b) for b in bin_path] + [sanitize_name(fname, "file")]

        if mode == "flat":
            parts = [sanitize_name(fname, "file")]
        elif mode == "disk":
            parts = _disk_parts(src, model.path)

        rel = "/".join(parts)
        rel = _dedupe_dst(rel, src, taken, plan.conflicts)

        plan.files.append(
            PlanFile(
                src=src,
                dst_rel=rel,
                size=_size(src),
                name=name,
                bin_path=bin_path,
                shared_bins=others,
                role="media",
                exists=os.path.exists(src),
            )
        )

    # 4. 未认领的物理文件：宁可多收，绝不丢
    for fp in model.unmatched_media():
        if skip_unmatched:
            plan.warnings.append(f"跳过未匹配文件：{fp}")
            continue
        rel = f"_未匹配素材/{sanitize_name(PurePath(fp).name, 'file')}"
        rel = _dedupe_dst(rel, fp, taken, plan.conflicts)
        plan.files.append(
            PlanFile(
                src=fp, dst_rel=rel, size=_size(fp),
                name=PurePath(fp).name, role="unmatched",
                exists=os.path.exists(fp),
            )
        )

    # 5. 依赖文件
    if include_deps:
        existing = {norm_key(f.src) for f in plan.files}
        for f in list(plan.files):
            for dep in _deps_for(f.src):
                if norm_key(dep) in existing:
                    continue
                existing.add(norm_key(dep))
                base = PurePath(f.dst_rel).parent
                rel = f"{base}/{sanitize_name(PurePath(dep).name, 'dep')}"
                rel = _dedupe_dst(str(rel), dep, taken, plan.conflicts)
                plan.files.append(
                    PlanFile(
                        src=dep, dst_rel=rel, size=_size(dep),
                        name=PurePath(dep).name, role="dep",
                        exists=os.path.exists(dep),
                    )
                )

    # 6. 离线检查
    offline = [f for f in plan.files if not f.exists]
    if offline:
        plan.warnings.append(f"{len(offline)} 个源文件离线（不存在）：")
        for f in offline[:10]:
            plan.warnings.append(f"  {f.src}")

    return plan


def _dedupe_dst(rel: str, src: str, taken: Dict[str, str], conflicts: List[dict]) -> str:
    """同名落位冲突 → 加 _1 / _2。taken 的 key 用小写，兼容大小写不敏感的文件系统。"""
    low = rel.lower()
    if low not in taken:
        taken[low] = src
        return rel

    if norm_key(taken[low]) == norm_key(src):
        return rel   # 同一个源文件，不用改名

    stem, dot, ext = rel.rpartition(".")
    if not dot:
        stem, ext = rel, ""
    i = 1
    while True:
        suffix = f"_{i}" + (f".{ext}" if ext else "")
        new = f"{stem}{suffix}"
        if new.lower() not in taken:
            taken[new.lower()] = src
            conflicts.append({"dst": new, "src": src, "replaced_by": taken.get(low, "")})
            return new
        i += 1


def _disk_parts(src: str, project_path: Path) -> List[str]:
    """disk 模式：还原磁盘相对结构。以工程所在目录为基准，向上找到公共根。"""
    src_parts = PurePath(src).parts
    proj_parts = PurePath(str(project_path.resolve())).parts

    # 找最长公共前缀
    common = 0
    for a, b in zip(src_parts, proj_parts):
        if a.lower() == b.lower():
            common += 1
        else:
            break

    if common >= 2:   # 至少共享盘符 + 一级目录
        rel = src_parts[common:]
        if rel:
            return [sanitize_name(p, "x") for p in rel]

    # 跨盘 / 无公共前缀：按盘符建顶层
    drive = src_parts[0].rstrip(":\\/") or "disk"
    return [sanitize_name(f"_{drive}"), *[sanitize_name(p, "x") for p in src_parts[1:]]]
