# -*- coding: utf-8 -*-
"""补齐四种 ID 属性，验证 MasterClip -> Clip -> Media -> FilePath 全链路。"""
import gzip, re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
root = ET.fromstring(gzip.decompress(p.read_bytes()))

ids = {}
for el in root.iter():
    for attr in ("ObjectID", "ObjectUID"):
        v = el.get(attr)
        if v:
            ids[v] = el
print(f"ID 索引条目: {len(ids)}")

def deref(el, attr=("ObjectURef", "ObjectRef")):
    if el is None: return None
    for a in attr:
        v = el.get(a)
        if v and v in ids: return ids[v]
    return None

def txt(el, tag):
    if el is None: return ""
    c = el.find(tag)
    if c is None:
        c = el.find(".//" + tag)
    return (c.text or "").strip() if c is not None and c.text else ""

# 看 Clip(42) 和 Media
print("\n=== Clip ObjectID=42 ===")
if "42" in ids:
    print(" ".join(ET.tostring(ids["42"], encoding="unicode").split())[:900])

print("\n=== 前 2 个 Media 原始 ===")
n = 0
for el in root.iter("Media"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:700])
    print("-" * 40)
    n += 1
    if n >= 2: break

print("\n=== 从 MasterClip 追到 FilePath ===")
def chase(el, depth=0, seen=None):
    if el is None or depth > 10: return None
    if seen is None: seen = set()
    k = id(el)
    if k in seen: return None
    seen.add(k)
    for t in ("FilePath", "ActualMediaFilePath"):
        v = txt(el, t)
        if re.match(r"^[A-Za-z]:[\\/]", v): return v
    for ch in el:
        for a in ("ObjectURef", "ObjectRef"):
            r = ch.get(a)
            if r and r in ids:
                got = chase(ids[r], depth + 1, seen)
                if got: return got
    return None

mc = None
for u, el in ids.items():
    if el.tag == "MasterClip":
        mc = el; break
if mc is not None:
    print("MasterClip:", txt(mc, "Name"))
    print("File:", chase(mc))
