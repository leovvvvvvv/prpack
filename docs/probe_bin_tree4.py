# -*- coding: utf-8 -*-
"""验证 MasterClip -> Media -> FilePath 链，并还原完整 bin 树。"""
import gzip, re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
root = ET.fromstring(gzip.decompress(p.read_bytes()))

by_uid = {}
for el in root.iter():
    u = el.get("ObjectUID")
    if u:
        by_uid[u] = el

def txt(el, tag):
    if el is None: return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""

# 1. MasterClip 结构
print("=== MasterClip 定义(前2) ===")
n = 0
for u, el in by_uid.items():
    if el.tag == "MasterClip":
        print(" ".join(ET.tostring(el, encoding="unicode").split())[:800])
        print("-" * 50)
        n += 1
        if n >= 2: break

# 2. Media 结构
print("\n=== Media 定义(前1) ===")
for el in root.iter("Media"):
    if el.get("ObjectID"):
        s = " ".join(ET.tostring(el, encoding="unicode").split())
        print(s[:700])
        break

# 3. 走 bin 树的雏形
print("\n=== bin 树遍历（前 30 项） ===")
def find_fp(el, depth=0, seen=None):
    """沿 UID/Ref 递归找 FilePath"""
    if el is None or depth > 8: return None
    if seen is None: seen = set()
    k = id(el)
    if k in seen: return None
    seen.add(k)
    for t in ("FilePath", "ActualMediaFilePath"):
        v = txt(el, t)
        if re.match(r"^[A-Za-z]:[\\/]", v): return v
    for ch in el:
        for attr in ("ObjectURef", "ObjectRef"):
            r = ch.get(attr)
            if r:
                tgt = by_uid.get(r)
                got = find_fp(tgt, depth + 1, seen)
                if got: return got
    return None

rows = []
def walk(el, path):
    if el is None: return
    tag = el.tag
    if tag == "BinProjectItem":
        nm = txt(el, "Name")
        for cont in el.findall("ProjectItemContainer"):
            for it in cont.iter("Item"):
                r = it.get("ObjectURef")
                walk(by_uid.get(r), path + [nm])
    elif tag == "ClipProjectItem":
        nm = txt(el, "Name")
        mc = el.find("MasterClip")
        fp = None
        if mc is not None:
            fp = find_fp(by_uid.get(mc.get("ObjectURef")))
        rows.append(("/".join(path) or "(根)", nm, fp))

rpi = None
for el in root.iter("RootProjectItem"):
    if el.get("ObjectUID"):
        rpi = el; break
if rpi is not None:
    for cont in rpi.findall("ProjectItemContainer"):
        for it in cont.iter("Item"):
            walk(by_uid.get(it.get("ObjectURef")), [])

print(f"总项目项: {len(rows)}  能追到文件: {sum(1 for r in rows if r[2])}")
for r in rows[:30]:
    print(f"  [{r[0][:28]:28s}] {str(r[1])[:24]:26s} -> {Path(r[2]).name if r[2] else '?'}")

print("\n=== 各 bin 的项目项数 ===")
from collections import Counter
for k, v in Counter(r[0] for r in rows).most_common():
    print(f"  {v:4d}  {k}")
