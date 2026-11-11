# -*- coding: utf-8 -*-
"""查清 Media 定义、Source 指向、以及 MasterClip 与 Media 的真实关联。"""
import gzip, re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
root = ET.fromstring(gzip.decompress(p.read_bytes()))

by_id, by_uid = {}, {}
for el in root.iter():
    v = el.get("ObjectID")
    if v and v not in by_id: by_id[v] = el
    u = el.get("ObjectUID")
    if u and u not in by_uid: by_uid[u] = el

print("ObjectID 条目:", len(by_id), " ObjectUID 条目:", len(by_uid))

print("\n=== 有 ObjectUID 的 Media（真正定义）===")
n = 0
for u, el in by_uid.items():
    if el.tag == "Media":
        print(" ".join(ET.tostring(el, encoding="unicode").split())[:900])
        print("-" * 40)
        n += 1
        if n >= 2: break

print("\n=== Source ObjectRef=536 指向 ===")
t = by_id.get("536")
print(t.tag if t is not None else "MISSING", "|", " ".join(ET.tostring(t, encoding="unicode").split())[:600] if t is not None else "")

print("\n=== Media 元素里 FilePath 在哪个层级（取一个带 FilePath 的 Media 向上找结构）===")
for el in root.iter("Media"):
    fp = el.find(".//FilePath")
    if fp is not None and fp.text and re.match(r"^[A-Za-z]:", fp.text):
        print("Media attrs:", el.attrib)
        print("直系子标签:", [c.tag for c in el][:12])
        print("FilePath:", fp.text)
        print("全貌:", " ".join(ET.tostring(el, encoding="unicode").split())[:900])
        break

print("\n=== 统计：Media 里 FilePath 所在深度 ===")
from collections import Counter
c = Counter()
for el in root.iter("Media"):
    for ch in el:
        if ch.tag == "FilePath": c["Media/FilePath"] += 1
        elif ch.find(".//FilePath") is not None: c[f"Media/{ch.tag}/.../FilePath"] += 1
print(dict(c))
