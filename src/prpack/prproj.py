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
    uid: str = ""             # 对应对象的 ObjectUID（对象图定位用）

    @property
    def is_sequence(self) -> bool:
        return self.is_seq or self.kind in ("Sequence", "SequenceProjectItem")


@dataclass
class SequenceInfo:
    """一条序列，以及它真正用到的物理文件（已递归展开嵌套序列）。"""

    uid: str
    name: str
    item_uid: str = ""                              # 素材箱里的主 ClipProjectItem
    item_uids: List[str] = field(default_factory=list)   # 该序列的全部条目 uid
    bin_path: Tuple[str, ...] = ()
    files: Set[str] = field(default_factory=set)    # norm_key 集合
    nested: List[str] = field(default_factory=list)         # 嵌套序列 uid
    nested_names: List[str] = field(default_factory=list)
    is_nested: bool = False                         # 自己被别的序列嵌套使用
    orphan: bool = False                            # 工程里存在定义，但素材箱里没有条目

    @property
    def file_count(self) -> int:
        return len(self.files)



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

    # 素材箱对象图（裁剪工程时要用）
    root_uid: str = ""
    bin_uids: Set[str] = field(default_factory=set)
    bin_children: Dict[str, List[str]] = field(default_factory=dict)
    item_parent: Dict[str, str] = field(default_factory=dict)

    # 序列
    sequence_defs: Dict[str, ET.Element] = field(default_factory=dict)
    sequence_infos: List[SequenceInfo] = field(default_factory=list)

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
        model._link_sequences()
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
        """序列定义 = 带 ObjectUID 且含 TrackGroups 的 <Sequence>。

        注意：`<Sequence ObjectURef="...">` 只是引用，不带子元素，别当定义。
        """
        for el in root.iter("Sequence"):
            uid = el.get("ObjectUID")
            if not uid:
                continue
            nm = self._text(el, "Name")
            if el.find("TrackGroups") is not None:
                self.sequence_defs[uid] = el
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

        self.root_uid = root_item.get("ObjectUID") or root_item.get("ObjectID") or ""
        visited_bins: Set[str] = set()

        def visit(el: ET.Element, path: Tuple[str, ...], depth: int = 0) -> None:
            if depth > 32:
                self.warnings.append("素材箱层级超过 32 层，疑似环，已中止")
                return
            uid = el.get("ObjectUID") or el.get("ObjectID") or ""
            if el.tag == "BinProjectItem":
                if uid and uid in visited_bins:
                    return
                if uid:
                    visited_bins.add(uid)
                nm = sanitize_name(self._text(el, "Name"), f"bin{len(self.bins)}")
                sub = path + (nm,)
                self.bins.append(sub)
                self.bin_uids.add(uid)
                kids: List[str] = []
                for ch in self._container_items(el):
                    cuid = ch.get("ObjectUID") or ch.get("ObjectID") or ""
                    if cuid:
                        kids.append(cuid)
                        self.item_parent[cuid] = uid
                    visit(ch, sub, depth + 1)
                self.bin_children[uid] = kids
            else:
                nm = self._text(el, "Name")
                if not nm:
                    return
                if uid:
                    self.item_parent.setdefault(uid, self.root_uid)
                self.entries.append(
                    BinEntry(name=nm, bin_path=path, kind=el.tag, uid=uid)
                )

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

    # ---------------- 引用图 ----------------

    def _walk_refs(self, start: ET.Element, max_nodes: int = 600_000) -> List[ET.Element]:
        """从 start 出发 BFS 遍历引用图，按距离由近到远返回访问过的元素。

        不猜中间标签名，只认 `ObjectURef` / `ObjectRef` 两种引用属性；
        对不带引用的容器元素继续下钻，所以
        TrackGroups → TrackGroup → Track → ClipItems → ClipTrackItem → SubClip
        这一整条链能自动走到底，不用为每个版本写死标签。

        为什么必须 BFS：判「这个项目项是哪条序列」时，要的是距离最近的那个序列定义。
        用 DFS 会先钻到最里层的嵌套序列，把父序列认成子序列。

        环（序列互相嵌套）靠 visited 兜住。
        """
        from collections import deque

        seen: Set[int] = set()
        order: List[ET.Element] = []
        q: deque = deque([start])
        while q:
            cur = q.popleft()
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            order.append(cur)
            if len(order) > max_nodes:
                self.warnings.append("引用图遍历超出节点上限，结果可能不完整")
                break
            for ch in list(cur):
                tgt = self.deref(ch)
                q.append(tgt if tgt is not None else ch)
        return order

    def _media_path_of(self, el: ET.Element) -> str:
        fp = self._text(el, "FilePath")
        if not _ABS.match(fp):
            fp = self._text(el, "ActualMediaFilePath")
        return fp if _ABS.match(fp) else ""

    def _seq_files_and_nested(self, seq_el: ET.Element) -> Tuple[Set[str], List[str]]:
        """一条序列用到的物理文件 + 它嵌套了哪些序列。

        嵌套序列的素材是被**递归带出来**的：遍历时走进被嵌套的 `<Sequence>` 定义，
        里面的 Media 自然就被收集了 —— 这就是嵌套序列打包的正确解法，
        不用手工递归，也不会漏。
        """
        files: Set[str] = set()
        nested: List[str] = []
        for x in self._walk_refs(seq_el):
            if x is seq_el:
                continue
            if x.tag == "Media":
                fp = self._media_path_of(x)
                if fp:
                    files.add(norm_key(fp))
            elif x.tag == "Sequence":
                u = x.get("ObjectUID")
                if u and u in self.sequence_defs:
                    nested.append(u)
        return files, nested

    def _link_sequences(self) -> None:
        """把素材箱里的序列项目项对到序列定义，并算出每条序列的物理文件闭包。

        ## 为什么主要靠名字对，而不是顺引用找

        实测 PR 26：`ClipProjectItem → MasterClip → Clips → Clip` 这一路指向的是
        **列定义**（`StringColumn` 之类），根本不是剪辑对象，往下走 48 个节点就到头，
        永远碰不到 `<Sequence>`。所以身份识别只能靠「面板名 == Sequence/Name」，
        这条在实际工程里是可靠的：PR 显示的就是序列名。

        引用遍历仍然要走，但只用它来算**素材闭包**（从序列定义出发），
        不从项目项出发。
        """
        name_to_def: Dict[str, List[str]] = {}
        for uid, el in self.sequence_defs.items():
            nm = self._text(el, "Name")
            if nm:
                name_to_def.setdefault(nm, []).append(uid)

        infos: Dict[str, SequenceInfo] = {}
        claimed: Dict[str, str] = {}      # 条目 uid → 序列 uid

        for e in [x for x in self.entries if x.is_seq]:
            own = ""

            # 1) 先试引用遍历（少数版本能走通）
            el = self.by_uid.get(e.uid)
            if el is None:
                el = self.by_id.get(e.uid)
            if el is not None:
                for x in self._walk_refs(el, max_nodes=200_000):
                    if x is el:
                        continue
                    u = x.get("ObjectUID")
                    if x.tag == "Sequence" and u in self.sequence_defs:
                        own = u
                        break

            # 2) 回退：按名字对
            if not own:
                cands = name_to_def.get(e.name, [])
                if len(cands) == 1:
                    own = cands[0]
                elif len(cands) > 1:
                    free = [u for u in cands if u not in claimed.values()]
                    own = (free or cands)[0]

            if not own:
                self.warnings.append(f"序列项目项「{e.name}」找不到对应的序列定义，已保守保留")
                continue

            claimed[e.uid] = own
            info = infos.get(own)
            if info is None:
                info = SequenceInfo(uid=own, name=e.name, item_uid=e.uid, bin_path=e.bin_path)
                info.files, info.nested = self._seq_files_and_nested(self.sequence_defs[own])
                info.item_uids = [e.uid]
                infos[own] = info
            else:
                # 同一条序列可能在工程里有多份条目（用户复制过），全部记下来，
                # 否则裁剪时会漏摘一份，新工程里就还留着这条序列。
                if e.uid and e.uid not in info.item_uids:
                    info.item_uids.append(e.uid)
                if len(e.bin_path) < len(info.bin_path):
                    info.bin_path, info.item_uid = e.bin_path, e.uid

        for uid, el in self.sequence_defs.items():
            if uid in infos:
                continue
            nm = self._text(el, "Name") or uid[:8]
            info = SequenceInfo(uid=uid, name=nm, orphan=True)
            info.files, info.nested = self._seq_files_and_nested(el)
            infos[uid] = info

        nested_all = {u for i in infos.values() for u in i.nested}
        for info in infos.values():
            info.is_nested = info.uid in nested_all
            info.nested_names = [infos[u].name if u in infos else u[:8] for u in info.nested]

        self.sequence_infos = sorted(
            infos.values(), key=lambda i: (len(i.bin_path), i.bin_path, i.name)
        )

    def visible_sequences(self) -> List["SequenceInfo"]:
        """面板里真有条目的序列 —— 孤儿（只剩定义）不对外提供。

        孤儿是工程里的历史残留：素材箱里没有条目，PR 面板上根本看不到、也点不开。
        把它们算进「可达素材」会让裁剪后的自检虚高，所以一律排除。
        """
        return [i for i in self.sequence_infos if not i.orphan]

    # ---------------- 序列查询 ----------------

    def find_sequence(self, key: str) -> Optional[SequenceInfo]:
        """按 uid、uid 前缀或名字找序列 —— 面板传短 uid 也能命中。"""
        key = (key or "").strip()
        if not key:
            return None
        vis = self.visible_sequences()
        for i in vis:
            if i.uid == key or i.name == key:
                return i
        for i in vis:
            if i.uid.startswith(key):
                return i
        return None

    def media_closure(self, keys: List[str]) -> Tuple[Set[str], Set[str], List[str]]:
        """选中序列 → （物理文件集合, 涉及的序列 uid 集合, 没认出来的 key）"""
        by_uid = {i.uid: i for i in self.visible_sequences()}
        start: List[str] = []
        missing: List[str] = []
        for k in keys:
            info = self.find_sequence(k)
            if info:
                start.append(info.uid)
            else:
                missing.append(k)

        files: Set[str] = set()
        seqs: Set[str] = set()
        stack = list(start)
        while stack:
            u = stack.pop()
            if u in seqs:
                continue
            seqs.add(u)
            info = by_uid.get(u)
            if info is None:
                continue
            files |= info.files
            stack.extend(info.nested)
        return files, seqs, missing

    def prune_sets(self, files: Set[str], seq_uids: Set[str]) -> Tuple[List[str], List[str]]:
        """算出裁剪工程要摘掉 / 保留的素材箱条目 uid。

        保守原则：认不出来的条目一律保留 —— 宁可留下一个多余项，
        也不能因为判错把用户要的素材从工程里摘掉。
        """
        seq_by_item: Dict[str, SequenceInfo] = {}
        for i in self.sequence_infos:
            for u in (i.item_uids or ([i.item_uid] if i.item_uid else [])):
                seq_by_item[u] = i
        keep: List[str] = []
        drop: List[str] = []
        for e in self.entries:
            if not e.uid:
                continue
            if e.is_seq:
                info = seq_by_item.get(e.uid)
                if info is None:
                    keep.append(e.uid)
                elif info.uid in seq_uids:
                    keep.append(e.uid)
                else:
                    drop.append(e.uid)
            else:
                if not e.filepath:
                    keep.append(e.uid)
                elif norm_key(e.filepath) in files:
                    keep.append(e.uid)
                else:
                    drop.append(e.uid)
        return sorted(drop), sorted(keep)

    def reachable_files(self) -> Set[str]:
        """素材箱树能看到的物理文件（裁剪后的工程自检用）。"""
        out = {norm_key(e.filepath) for e in self.entries if e.filepath}
        for info in self.visible_sequences():
            out |= info.files
        return out

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
            "sequence_count": len(self.visible_sequences()),
            "orphan_sequences": sum(1 for i in self.sequence_infos if i.orphan),
            "media_items": len(items),
            "unique_files": len(self.media_files),
            "unmatched": len(self.unmatched_media()),
            "offline": len(missing),
            "unmatched_names": [e.name for e in self.entries if not e.filepath and not e.is_sequence][:10],
        }
