# -*- coding: utf-8 -*-
"""验证 .prproj 里能否还原素材箱层级（bin 模式的前提）。"""
import gzip, re, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
root = ET.fromstring(gzip.decompress(p.read_bytes()))

# 1. 看看 Bin / ProjectItem 的结构
print("=== 标签统计 ===")
from collections import Counter
c = Counter(el.tag for el in root.iter())
for t in ("Bin", "ProjectItem", "Children", "RootBin", "Node", "Ref", "ClipProjectItem", "Media"):
    if t in c:
        print(f"  {t}: {c[t]}")

print("\n=== 第一个 Bin 全貌 ===")
for el in root.iter("Bin"):
    if el.get("ObjectID"):
        print(" ".join(ET.tostring(el, encoding="unicode").split())[:1200])
        break

print("\n=== 前 3 个 ProjectItem ===")
n = 0
for el in root.iter("ProjectItem"):
    if el.get("ObjectID"):
        print(" ".join(ET.tostring(el, encoding="unicode").split())[:600])
        print("---")
        n += 1
        if n >= 3: break

# 2. 顶层结构：RootBin 往下
print("\n=== 根节点 ===")
for el in root:
    if el.tag in ("Project", "RootBin", "ProjectItem"):
        print(f"root child: {el.tag} ObjectID={el.get('ObjectID')}")

# 3. 看是否有 Children 表达父子
print("\n=== 含 Children 的元素 ===")
cnt = 0
for el in root.iter():
    if el.tag == "Children":
        print("parent tag:", el.get("ObjectID"), "children:", len(list(el)))
        cnt += 1
        if cnt >= 3: break
if cnt == 0:
    print("  无 <Children> 标签")

# 4. 找 bin 名字，看命名规律
print("\n=== Bin 名列表(前20) ===")
names = []
for el in root.iter("Bin"):
    nm = el.find("Name")
    if nm is not None and nm.text:
        names.append(nm.text)
print(names[:20], f"... 共 {len(names)}")
