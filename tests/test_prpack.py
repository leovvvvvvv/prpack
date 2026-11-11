# -*- coding: utf-8 -*-
"""合成工程测试 —— 全部素材都是 tmp 目录里现造的空文件，不碰任何真实项目。

跑法：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prpack.copyer import CopyError, copy_plan, prepare_output   # noqa: E402
from prpack.plan import build_plan                               # noqa: E402
from prpack.prproj import ProjectModel, norm_key, sanitize_name  # noqa: E402
from prpack.relink import relink_project                         # noqa: E402
from prpack.verify import verify_package                         # noqa: E402


def make_project(root: Path, tree: dict) -> Path:
    """造一个最小 .prproj。

    tree 结构：{"Footage": ["a.mp4", "b.mp4"], "Audio": ["m.wav"]}
    同时也造出对应的空文件。
    """
    media_xml = []
    for i, (bin_name, files) in enumerate(tree.items()):
        for j, fname in enumerate(files):
            fp = root / bin_name / fname
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(b"\x00" * 1024)
            media_xml.append(
                f'<Media ObjectUID="media-{i}-{j}">'
                f"<RelativePath>..\\{bin_name}\\{fname}</RelativePath>"
                f"<FilePath>{fp}</FilePath>"
                f"<Title>{fname}</Title>"
                f"</Media>"
            )

    bins_xml = []
    root_items = []
    for i, (bin_name, files) in enumerate(tree.items()):
        items = []
        for j, _ in enumerate(files):
            items.append(f'<Item Index="{j}" ObjectURef="clip-{i}-{j}"/>')
        bins_xml.append(
            f'<BinProjectItem ObjectUID="bin-{i}">'
            f"<ProjectItem><Node><ID>{100 + i}</ID></Node><Name>{bin_name}</Name></ProjectItem>"
            f'<ProjectItemContainer><Items>{"".join(items)}</Items></ProjectItemContainer>'
            f"</BinProjectItem>"
        )
        root_items.append(f'<Item Index="{i}" ObjectURef="bin-{i}"/>')

    clips_xml = []
    for i, (bin_name, files) in enumerate(tree.items()):
        for j, fname in enumerate(files):
            clips_xml.append(
                f'<ClipProjectItem ObjectUID="clip-{i}-{j}">'
                f"<ProjectItem><Node><ID>{200 + i * 10 + j}</ID></Node>"
                f"<Name>{fname}</Name></ProjectItem>"
                f'<MasterClip ObjectURef="mc-{i}-{j}"/>'
                f"</ClipProjectItem>"
            )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Project>"
        '<RootProjectItem ObjectUID="root">'
        f'<ProjectItemContainer><Items>{"".join(root_items)}</Items></ProjectItemContainer>'
        "</RootProjectItem>"
        + "".join(bins_xml)
        + "".join(clips_xml)
        + "".join(media_xml)
        + "</Project>"
    )
    out = root / "test.prproj"
    out.write_bytes(gzip.compress(xml.encode("utf-8")))
    return out


class TestSanitize(unittest.TestCase):
    def test_strips_illegal(self):
        self.assertEqual(sanitize_name('a/b:c*d?"e'), "a_b_c_d__e")

    def test_empty(self):
        self.assertEqual(sanitize_name("   "), "untitled")

    def test_windows_reserved(self):
        self.assertTrue(sanitize_name("CON").startswith("_"))


class TestParseAndPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tree = {
            "Footage": ["a.mp4", "b.mp4"],
            "Audio": ["m.wav"],
        }
        self.proj = make_project(self.root, self.tree)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_bins(self):
        m = ProjectModel.load(self.proj)
        self.assertEqual(len(m.bins), 2)
        self.assertEqual([b[0] for b in m.bins], ["Footage", "Audio"])
        self.assertEqual(len(m.media_files), 3)

    def test_plan_layout_mirrors_bins(self):
        m = ProjectModel.load(self.proj)
        plan = build_plan(m, self.root / "out", mode="bin")
        rels = sorted(f.dst_rel for f in plan.files)
        self.assertEqual(
            rels,
            ["Audio/m.wav", "Footage/a.mp4", "Footage/b.mp4"],
        )

    def test_plan_flat_mode(self):
        m = ProjectModel.load(self.proj)
        plan = build_plan(m, self.root / "out", mode="flat")
        self.assertTrue(all("/" not in f.dst_rel for f in plan.files))

    def test_dedupe_same_file_twice(self):
        """同一个物理文件被两个项目项引用 → 只出 1 条。"""
        m = ProjectModel.load(self.proj)
        dup = m.media_items()[0]
        m.entries.append(dup.__class__(name=dup.name, bin_path=("Other",), kind=dup.kind))
        plan = build_plan(m, self.root / "out", mode="bin")
        srcs = [norm_key(f.src) for f in plan.files]
        self.assertEqual(len(srcs), len(set(srcs)))


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = make_project(self.root, {"Footage": ["a.mp4", "b.mp4"]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_pack_roundtrip(self):
        m = ProjectModel.load(self.proj)
        out = self.root / "out"
        plan = build_plan(m, out, mode="bin")
        prepare_output(out)
        res = copy_plan(plan)
        self.assertEqual(res.failed, 0)
        self.assertEqual(res.copied, 2)

        rl = relink_project(plan)
        self.assertTrue(Path(rl.output_path).exists())

        vr = verify_package(m, Path(rl.output_path), plan)
        self.assertTrue(vr.ok, [c.detail for c in vr.checks if not c.ok])

    def test_refuses_nonempty_output(self):
        out = self.root / "out"
        out.mkdir()
        (out / "x.txt").write_text("x")
        with self.assertRaises(CopyError):
            prepare_output(out)

    def test_original_untouched(self):
        before = self.proj.read_bytes()
        m = ProjectModel.load(self.proj)
        plan = build_plan(m, self.root / "out", mode="bin")
        prepare_output(self.root / "out")
        copy_plan(plan)
        relink_project(plan)
        self.assertEqual(self.proj.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
