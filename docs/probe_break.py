# -*- coding: utf-8 -*-
"""诊断：某个 ClipProjectItem 的 MasterClip 链在哪断了。"""
import gzip, sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = Path(sys.argv[1])
target = sys.argv[2]  # ObjectUID
root = ET.fromstring(gzip.decompress(p.read_bytes()))

ids = {}
for el in root.iter():
    for a in ("ObjectID", "ObjectUID"):
        v = el.get(a)
        if v and v not in ids:
            ids[v] = el

el = ids.get(target)
print("=== ClipProjectItem ===")
print(" ".join(ET.tostring(el, encoding="unicode").split())[:600])

mc_ref = el.find("MasterClip")
print("\nMasterClip ref:", mc_ref.get("ObjectURef") if mc_ref is not None else None)
mc = ids.get(mc_ref.get("ObjectURef")) if mc_ref is not None else None
print("\n=== MasterClip ===")
print(" ".join(ET.tostring(mc, encoding="unicode").split())[:1200] if mc is not None else "NOT FOUND")

# 逐层看 Clips
if mc is not None:
    clips = mc.find("Clips")
    if clips is not None:
        for c in clips:
            ref = c.get("ObjectRef")
            tgt = ids.get(ref)
            print(f"\n--- Clips/{c.tag} ObjectRef={ref} -> {tgt.tag if tgt is not None else 'MISSING'} ---")
            if tgt is not None:
                print(" ".join(ET.tostring(tgt, encoding="unicode").split())[:500])
                src = tgt.iter()
                # 找 Source
                for node in tgt.iter("Source"):
                    print("  Source attr:", node.attrib)
                    sref = node.get("ObjectRef") or node.get("ObjectURef")
                    st = ids.get(sref)
                    print("  -> target tag:", st.tag if st is not None else "MISSING", "attrs:", st.attrib if st is not None else None)
                    if st is not None:
                        print("  -> content:", " ".join(ET.tostring(st, encoding="unicode").split())[:700])
