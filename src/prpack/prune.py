# -*- coding: utf-8 -*-
"""prpack · 裁剪层

把打包出来的工程瘦下来：只留下选中序列和它们真正用到的素材。

## 只删「条目」，不删「对象」

`.prproj` 里一个剪辑由两半组成：
  1. 素材箱容器里的**条目** —— `<Items><Item Index="3" ObjectURef="..."/></Items>`
  2. 一大坨按 UID 定义的对象（`<Media>` / `<MasterClip>` / `<Sequence>` …）

裁剪只动第 1 半。被摘掉的条目所指向的对象**原样留在文件里**，变成没人引用的孤儿。
这样做的好处是：

  - 不会出现「删了 A，结果 B 还引用 A」导致的工程打不开；
  - 不需要重新序列化 XML，只做行级删除 + Index 重编号，格式一个字节不乱；
  - PR 打开时按容器条目建面板，孤儿对象不显示、不参与媒体检查。

代价是文件里留了少量无用对象（对本项目量级来说可以忽略）。

## 空箱级联

一个箱里的条目被摘光之后，这个箱本身也该从上一级摘掉，否则面板里留一堆空箱。
级联在对象图上算，算完再一次性落到文本上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Set, Tuple

from .prproj import ProjectModel

_BLOCK = re.compile(r"(<Items\b[^>]*>)(.*?)(</Items>)", re.S)
_ITEM_TAG = re.compile(r"<Item\b[^>]*/>")
_UID = re.compile(r'ObjectURef="([^"]+)"')
_INDEX = re.compile(r'Index="\d+"')


@dataclass
class PruneResult:
    text: str = ""
    removed_items: int = 0
    removed_bins: int = 0
    kept_items: int = 0
    warnings: List[str] = field(default_factory=list)


def cascade_drop(model: ProjectModel, drop: Iterable[str]) -> Set[str]:
    """把「所有子项都被摘掉」的箱也加进摘除集合，一直到不动为止。"""
    gone: Set[str] = set(drop)

    changed = True
    while changed:
        changed = False
        for bin_uid, kids in model.bin_children.items():
            if bin_uid in gone or not kids:
                continue
            if all(k in gone for k in kids):
                gone.add(bin_uid)
                changed = True
    return gone


def prune_items(xml_text: str, model: ProjectModel, drop: Iterable[str]) -> PruneResult:
    """删掉指定 uid 的素材箱条目，并把每个 Items 里的 Index 重新编号。

    按 `<Item .../>` 标签处理，不按行 —— 实测有工程把条目全挤在一行，
    按行删会一次删掉一整箱。
    """
    res = PruneResult()
    gone = cascade_drop(model, drop)
    res.removed_bins = len(gone) - len(set(drop))

    def rebuild(m: re.Match) -> str:
        head, body, tail = m.group(1), m.group(2), m.group(3)

        tags = list(_ITEM_TAG.finditer(body))
        if not tags:
            return head + body + tail

        # 条目之间的分隔（缩进 + 换行）。正常工程里全部一致，直接复用。
        sep = body[tags[0].end():tags[1].start()] if len(tags) > 1 else ""
        leading = body[:tags[0].start()]
        trailing = body[tags[-1].end():]

        kept: List[str] = []
        for t in tags:
            raw = t.group(0)
            u = _UID.search(raw)
            uid = u.group(1) if u else ""
            if uid and uid in gone:
                res.removed_items += 1
                continue
            kept.append(_INDEX.sub(f'Index="{len(kept)}"', raw, count=1))

        res.kept_items += len(kept)
        if not kept:
            return head + tail
        return head + leading + sep.join(kept) + trailing + tail

    new_text = _BLOCK.sub(rebuild, xml_text)

    if new_text == xml_text and res.removed_items == 0:
        res.warnings.append("没有可摘除的条目（uid 可能对不上），工程原样输出")
        res.text = xml_text
        return res

    # 安全校验：剩余条目数必须等于「原条目数 - 记录摘除数」
    before = len(_ITEM_TAG.findall(xml_text))
    after = len(_ITEM_TAG.findall(new_text))
    if before - after != res.removed_items:
        raise ValueError(
            f"裁剪计数对不上（原 {before} / 新 {after} / 记录摘除 {res.removed_items}），"
            f"已中止，不写盘"
        )

    res.text = new_text
    return res


def item_count(xml_text: str) -> int:
    return len(_ITEM_TAG.findall(xml_text))


def index_sanity(xml_text: str) -> List[str]:
    """检查每个 Items 里的 Index 是否从 0 连续编号，返回问题描述。"""
    problems: List[str] = []
    for i, m in enumerate(_BLOCK.finditer(xml_text)):
        idxs = [int(x) for x in re.findall(r'Index="(\d+)"', m.group(2))]
        if len(set(idxs)) != len(idxs):
            problems.append(f"第 {i} 个 <Items> 的 Index 有重复：{idxs}")
        elif idxs != list(range(len(idxs))):
            problems.append(f"第 {i} 个 <Items> 的 Index 不连续：{idxs}")
    return problems
