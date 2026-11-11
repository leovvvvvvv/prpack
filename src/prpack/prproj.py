# -*- coding: utf-8 -*-
"""prpack · .prproj 解析层

把 .prproj 变成「素材箱位置 + 物理文件」两张可靠的表。

## 实测事实（PR 26.5.1，2026-11-11）

1. `.prproj` = gzip 压缩的 XML 文本。
2. **ObjectID 与 ObjectUID 是两个独立命名空间**，绝不能混在一张字典里 ——
   `ObjectRef` 只配 `ObjectID`，`ObjectURef` 只配 `ObjectUID`。
   混用会导致引用解析到完全无关的对象（实测：`Clip ObjectRef=67` 解析成了 `StringColumn`）。
3. 对象定义：`ObjectID` 6606 个 / `ObjectUID` 386 个（同一份工程）。
4. **`Media` 用 `ObjectUID` 定义，`FilePath` 是它的直系子元素** —— 这是物理文件全集的权威来源。
5. 素材箱层级：`RootProjectItem` / `BinProjectItem` → `ProjectItemContainer/Items/Item`（按 Index 排序）。
6. 名称在 `ProjectItem/Name`，不在对象第一层。
7. `MasterClip/Clips/Clip` 在 PR 26 里指向的是**列定义**（`StringColumn` 等），
   不是剪辑对象 —— **不要指望从 MasterClip 追到 Media**。本模块走「名字匹配」双路合并：
   - 路 A：bin 树 → `(素材箱路径, 面板名)`
   - 路 B：Media 全集 → `(Title, FilePath)`
   - 按 `面板名 == Title` 匹配，匹配不上的落 `_未匹配素材` 并在报告标红。
   这样宁可位置放错，绝不丢文件。
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Dict, Iterator, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ABS = re.compile(r"^[A-Za-z]:[\\/]")

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_name(name: str, fallback: str = "untitled") -> str:
    """把素材箱名清成合法目录名。"""
    s = _ILLEGAL.sub("_", (name or "").strip()).strip(" .")
    if not s:
        return fallback
    if s.upper().split(".")[0] in _WIN_RESERVED:
        s = "_" + s
    return s


def norm_key(path: str) -> str:
    """物理文件去重键：绝对化 + 统一反斜杠 + 小写。"""
    p = os.path.abspath(path).replace("/", "\\")
    while len(p) > 3 and p.endswith("\\"):
        p = p[:-1]
    return p.lower()


@dataclass
class BinEntry:
    """素材箱树里的一个可显示项（媒体或序列）。"""

    name: str
    bin_path: Tuple[str, ...]
    kind: str
    filepath: str = ""
    matched_by: str = ""      # title / basename / ""(未匹配)
    is_seq: bool = False      # 序列项目项（不需要复制媒体）

    @property
    def is_sequence(self) -> bool:
        return self.is_seq or self.kind in ("Sequence", "SequenceProjectItem")


@dataclass
class ProjectModel:
    path: Path
    xml_text: str
    raw: bytes
    by_id: Dict[str, ET.Element] = field(default_factory=dict)
    by_uid: Dict[str, ET.Element] = field(default_factory=dict)
    bins: List[Tuple[str, ...]] = field(default_factory=list)
    entries: List[BinEntry] = field(default_factory=list)
    media_files: List[str] = field(default_factory=list)   # 物理文件全集（去重）
    sequences: Set[str] = field(default_factory=set)
    warnings: List[str] = field(default_factory=list)

    # ---------------- 构建 ----------------

    @classmethod
    def load(cls, path: Path) -> "ProjectModel":
        path = Path(path)
        raw = path.read_bytes()
        try:
            xml_bytes = gzip.decompress(raw)
        except OSError as exc:
            raise ValueError(f"不是合法的 .prproj（gzip 解压失败）：{path}") from exc
        root = ET.fromstring(xml_bytes)

        model = cls(path=path, xml_text=xml_bytes.decode("utf-8"), raw=raw)
        model._index(root)
        model._collect_media(root)
        model._collect_sequences(root)
        model._walk_bins(root)
        model._match()
        return model

    def _index(self, root: ET.Element) -> None:
        for el in root.iter():
            v = el.get("ObjectID")
            if v and v not in self.by_id:
                self.by_id[v] = el
            u = el.get("ObjectUID")
            if u and u not in self.by_uid:
                self.by_uid[u] = el

    @staticmethod
    def _text(el: Optional[ET.Element], tag: str) -> str:
        if el is None:
            return ""
        c = el.find(tag)
        if c is None:
            c = el.find(".//" + tag)
        return (c.text or "").strip() if c is not None and c.text else ""

    def deref(self, el: Optional[ET.Element]) -> Optional[ET.Element]:
        """按引用类型分别查表 —— ObjectURef 查 UID 表，ObjectRef 查 ID 表。"""
        if el is None:
            return None
        u = el.get("ObjectURef")
        if u:
            return self.by_uid.get(u)
        r = el.get("ObjectRef")
        if r:
            return self.by_id.get(r)
        return None

    def _collect_media(self, root: ET.Element) -> None:
        """物理文件全集。去重后写入 media_files。"""
        seen: Set[str] = set()
        for el in root.iter("Media"):
            fp = self._text(el, "FilePath")
            if not _ABS.match(fp):
                fp = self._text(el, "ActualMediaFilePath")
            if not _ABS.match(fp):
                continue
            k = norm_key(fp)
            if k in seen:
                continue
            seen.add(k)
            self.media_files.append(fp)

    def _collect_sequences(self, root: ET.Element) -> None:
        for el in root.iter("Sequence"):
            nm = self._text(el, "Name")
            if nm:
                self.sequences.add(nm)

    def _container_items(self, el: ET.Element) -> Iterator[ET.Element]:
        for cont in el.findall("ProjectItemContainer"):
            items = cont.find("Items")
            if items is None:
                continue
            nodes = list(items.findall("Item"))
            nodes.sort(key=lambda x: int(x.get("Index", "0") or 0))
            for it in nodes:
                tgt = self.deref(it)
                if tgt is not None:
                    yield tgt

    def _walk_bins(self, root: ET.Element) -> None:
        root_item = None
        for el in root.iter("RootProjectItem"):
            if el.get("ObjectUID") or el.get("ObjectID"):
                root_item = el
                break
        if root_item is None:
            self.warnings.append("找不到 RootProjectItem，素材箱层级不可用")
            return

        seen: Set[Tuple[int, Tuple[str, ...]]] = set()

        def visit(el: ET.Element, path: Tuple[str, ...], depth: int = 0) -> None:
            if depth > 32:
                self.warnings.append("素材箱层级超过 32 层，疑似环，已中止")
                return
            if el.tag == "BinProjectItem":
                nm = sanitize_name(self._text(el, "Name"), f"bin{len(self.bins)}")
                key = (id(el), path + (nm,))
                if key in seen:
                    return
                seen.add(key)
                sub = path + (nm,)
                self.bins.append(sub)
                for ch in self._container_items(el):
                    visit(ch, sub, depth + 1)
            else:
                nm = self._text(el, "Name")
                if not nm:
                    return
                self.entries.append(BinEntry(name=nm, bin_path=path, kind=el.tag))

        for ch in self._container_items(root_item):
            visit(ch, ())

    def _match(self) -> None:
        """按名字把 bin 树条目对到物理文件。两级：Title → 文件名（basename）。"""
        title_map: Dict[str, str] = {}
        for el_iter in [self.by_uid.values()]:
            for el in el_iter:
                if el.tag != "Media":
                    continue
                title = self._text(el, "Title")
                fp = self._text(el, "FilePath")
                if title and _ABS.match(fp):
                    title_map.setdefault(title.lower(), fp)

        base_map: Dict[str, List[str]] = {}
        for fp in self.media_files:
            base_map.setdefault(PurePath(fp).name.lower(), []).append(fp)

        used: Set[str] = set()
        for e in self.entries:
            low = e.name.lower()
            fp = title_map.get(low)
            by = "title" if fp else ""
            if not fp:
                cands = base_map.get(low, [])
                if len(cands) == 1:
                    fp = cands[0]
                    by = "basename"
                elif len(cands) > 1:
                    fp = cands[0]
                    by = "basename+dup"
            if fp:
                e.filepath = fp
                e.matched_by = by
                used.add(norm_key(fp))

        # 标记序列项目项：面板名与某个 Sequence 的 Name 对得上
        for e in self.entries:
            if e.name in self.sequences:
                e.is_seq = True

    # ---------------- 查询 ----------------

    def unmatched_media(self) -> List[str]:
        """物理文件全集里，没被任何素材箱条目认领的。"""
        used = {norm_key(e.filepath) for e in self.entries if e.filepath}
        return [fp for fp in self.media_files if norm_key(fp) not in used]

    def media_items(self) -> List[BinEntry]:
        """需要复制的媒体条目（排除序列）。"""
        return [e for e in self.entries if e.filepath and not e.is_sequence]

    def stats(self) -> dict:
        items = self.media_items()
        missing = [e for e in items if not os.path.exists(e.filepath)]
        return {
            "project": self.path.name,
            "project_path": str(self.path),
            "project_size": self.path.stat().st_size,
            "bin_count": len(self.bins),
            "bin_entries": len(self.entries),
            "sequence_items": sum(1 for e in self.entries if e.is_sequence),
            "media_items": len(items),
            "unique_files": len(self.media_files),
            "unmatched": len(self.unmatched_media()),
            "offline": len(missing),
            "unmatched_names": [e.name for e in self.entries if not e.filepath and not e.is_sequence][:10],
        }
