# -*- coding: utf-8 -*-
"""prpack · 重写层（技术核心）

把打包后的 .prproj 里的媒体路径指到新位置。

## 为什么不用 XML 重新序列化

PR 的 .prproj 是 gzip 里的私有 XML。用 ElementTree 解析后写回会改属性顺序、
命名空间、自闭合写法，PR 对这类变化容错极低 —— 实测同类改写有概率直接打不开。
所以这里只做**整文本字面替换**：字节进、字节出，结构一个字节都不动。

## 替换顺序（重要）

1. 先替换 `FilePath` / `ActualMediaFilePath` 的**长绝对路径**。
2. 再替换 `RelativePath` 的**短相对路径**（先长后短，避免短串先命中把长串拆坏）。
3. 最后清空指向本机临时目录的 `ConformedAudioPath` / `PeakFilePath`
   —— 不清空的话，换台机器打开会报错找 `.pek` 波形文件。

## 双保险

绝对路径改对了，PR 打开就用绝对路径；绝对路径失效时（换机器、换盘符）PR 会
fallback 到 `RelativePath`。两个都改，才算真正可迁移。
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from .plan import Plan

# 指向本机临时目录、必须在打包时清空的标签
_TEMP_TAGS = ("ConformedAudioPath", "PeakFilePath", "ConformedAudioFilePath")


@dataclass
class RelinkResult:
    output_path: str = ""
    replaced_abs: int = 0
    replaced_rel: int = 0
    cleared_temp: int = 0
    pruned_items: int = 0
    pruned_bins: int = 0
    untouched: List[str] = field(default_factory=list)   # 一条都没替换到的源文件
    warnings: List[str] = field(default_factory=list)


def _xml_escape_path(p: str) -> str:
    """路径在 XML 文本里的实际写法（& 等会被转义）。"""
    return escape(p)


def relink_project(
    plan: Plan,
    output_project: Optional[Path] = None,
    update_relative: bool = True,
    clear_temp_paths: bool = True,
    model=None,
) -> RelinkResult:
    """生成改好路径的新工程。不碰原工程。"""
    src_project = Path(plan.project_path)
    root = Path(plan.output_root)

    raw = src_project.read_bytes()
    xml_text = gzip.decompress(raw).decode("utf-8")

    res = RelinkResult()

    # ---- 0. 裁剪：先摘条目，再改路径 ----
    # 顺序不能反：路径替换是按「plan 里的文件」逐条替换的，
    # 先摘掉不需要的条目，能少改一大堆即将消失的对象。
    if plan.drop_item_uids:
        from .prune import prune_items
        from .prproj import ProjectModel

        mdl = model if model is not None else ProjectModel.load(src_project)
        pr = prune_items(xml_text, mdl, plan.drop_item_uids)
        xml_text = pr.text
        res.pruned_items = pr.removed_items
        res.pruned_bins = pr.removed_bins
        res.warnings.extend(pr.warnings)

    # ---- 1. 绝对路径 ----
    abs_map: Dict[str, str] = {}
    for f in plan.files:
        new_abs = str((root / f.dst_abs).resolve())
        abs_map[f.src] = new_abs

    # 长的先替换，避免一个路径是另一个路径的前缀
    for old, new in sorted(abs_map.items(), key=lambda kv: -len(kv[0])):
        old_x, new_x = _xml_escape_path(old), _xml_escape_path(new)
        n = xml_text.count(old_x)
        if n:
            xml_text = xml_text.replace(old_x, new_x)
            res.replaced_abs += n
        else:
            res.untouched.append(old)

    # ---- 2. 相对路径 ----
    if update_relative:
        rel_map: Dict[str, str] = {}
        for f in plan.files:
            if not f.bin_path and f.role == "media":
                continue
            new_rel = f.dst_rel.replace("/", "\\")
            # 相对工程文件所在目录
            rel_map[new_rel] = new_rel
        # 旧相对路径 → 新相对路径：从原工程 XML 里按 Media 的 RelativePath 对应关系推
        # 简化做法：凡是 RelativePath 标签、且其值末段等于某个目标文件名，就按落位重写
        by_name: Dict[str, List[str]] = {}
        for f in plan.files:
            by_name.setdefault(Path(f.src).name.lower(), []).append(f.dst_rel.replace("/", "\\"))

        def _fix_rel(m: re.Match) -> str:
            old_rel = m.group(1)
            base = old_rel.replace("/", "\\").split("\\")[-1].lower()
            cands = by_name.get(base)
            if cands and len(cands) == 1:
                res.replaced_rel += 1
                return f"<RelativePath>{cands[0]}</RelativePath>"
            return m.group(0)

        xml_text = re.sub(r"<RelativePath>(.*?)</RelativePath>", _fix_rel, xml_text,
                          flags=re.DOTALL)

    # ---- 3. 清空本机临时路径 ----
    if clear_temp_paths:
        for tag in _TEMP_TAGS:
            pattern = re.compile(rf"<{tag}>[^<]*</{tag}>")
            xml_text, n = pattern.subn(f"<{tag}/>", xml_text)
            if n:
                res.replaced_abs  # noqa: B018  保持命名清晰
                res.cleared_temp += n

    # ---- 4. 自检后落盘 ----
    payload = xml_text.encode("utf-8")
    _assert_parsable(payload)

    if output_project is None:
        output_project = root / f"{plan.project_name}_packed.prproj"
    output_project = Path(output_project)
    output_project.parent.mkdir(parents=True, exist_ok=True)

    output_project.write_bytes(gzip.compress(payload, compresslevel=6))
    res.output_path = str(output_project)

    if res.untouched:
        res.warnings.append(
            f"{len(res.untouched)} 个源文件在工程里没有绝对路径引用（可能只用相对路径），"
            f"已跳过改写：{res.untouched[:5]}"
        )
    return res


def _assert_parsable(xml_bytes: bytes) -> None:
    import xml.etree.ElementTree as ET

    try:
        ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"改写后 XML 不可解析，已中止（不落盘）：{exc}") from exc
