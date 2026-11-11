# -*- coding: utf-8 -*-
"""dump ProjectItem / Node 的原始结构，找父子关系与 bin 名。"""
import gzip, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
root = ET.fromstring(gzip.decompress(p.read_bytes()))

print("=== 前 5 个 ProjectItem 原始（不按 ObjectID 过滤） ===")
n = 0
for el in root.iter("ProjectItem"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:800])
    print("-" * 60)
    n += 1
    if n >= 5: break

print("\n=== 所有 ProjectItem 的属性名 ===")
attrs = set()
for el in root.iter("ProjectItem"):
    attrs.update(el.attrib.keys())
print(attrs)

print("\n=== 所有 ProjectItem 的子标签 ===")
tags = set()
for el in root.iter("ProjectItem"):
    tags.update(ch.tag for ch in el)
print(tags)

print("\n=== 前 3 个 Node 原始 ===")
n = 0
for el in root.iter("Node"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:500])
    print("-" * 40)
    n += 1
    if n >= 3: break

print("\n=== ClipProjectItem 前 3 个 ===")
n = 0
for el in root.iter("ClipProjectItem"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:500])
    print("-" * 40)
    n += 1
    if n >= 3: break
