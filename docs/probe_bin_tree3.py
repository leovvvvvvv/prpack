# -*- coding: utf-8 -*-
"""dump ProjectItemContainer / BinProjectItem / RootProjectItem，还原 bin 树。"""
import gzip, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
root = ET.fromstring(gzip.decompress(p.read_bytes()))

print("=== RootProjectItem ===")
for el in root.iter("RootProjectItem"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:800])
    print("-" * 50)

print("\n=== ProjectItemContainer ===")
for el in root.iter("ProjectItemContainer"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:900])
    print("-" * 50)

print("\n=== BinProjectItem ===")
for el in root.iter("BinProjectItem"):
    print(" ".join(ET.tostring(el, encoding="unicode").split())[:700])
    print("-" * 50)
