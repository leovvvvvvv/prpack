# -*- coding: utf-8 -*-
"""序列范围与嵌套序列的测试 —— 合成工程，不碰真实项目。

覆盖三件事：
  1. 序列的素材闭包算得对不对（Media 在图里钻得出来）
  2. 嵌套序列的素材有没有被递归带出来
  3. 只打一条序列时，新工程有没有把别的序列和没用到的素材摘掉
"""

from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prpack.copyer import copy_plan, prepare_output     # noqa: E402
from prpack.plan import build_plan                      # noqa: E402
from prpack.prproj import ProjectModel                   # noqa: E402
from prpack.prune import index_sanity, prune_items       # noqa: E402
from prpack.relink import relink_project                 # noqa: E402
from prpack.verify import verify_package                 # noqa: E402


def make_seq_project(root: Path) -> Path:
    """造一个带序列和嵌套序列的工程。

    结构：
        素材箱 A：a.mp4
        素材箱 B：b.mp4, c.wav
        序列「主剪」  ：用了 a.mp4
        序列「总片」  ：嵌了「主剪」（所以间接用了 a.mp4）
    """
    files = {"A": ["a.mp4"], "B": ["b.mp4", "c.wav"]}

    media_xml, media_uid = [], {}
    for i, (bin_name, names) in enumerate(files.items()):
        for j, fname in enumerate(names):
            fp = root / bin_name / fname
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(b"\x00" * 2048)
            uid = f"media-{i}-{j}"
            media_uid[fname] = uid
            media_xml.append(
                f'<Media ObjectUID="{uid}">'
                f"<RelativePath>..\\{bin_name}\\{fname}</RelativePath>"
                f"<FilePath>{fp}</FilePath><Title>{fname}</Title></Media>"
            )

    # 素材箱 + 序列项目项
    bins_xml, root_items = [], []
    for i, (bin_name, names) in enumerate(files.items()):
        items = "".join(
            f'<Item Index="{j}" ObjectURef="clip-{i}-{j}"/>' for j in range(len(names))
        )
        bins_xml.append(
            f'<BinProjectItem ObjectUID="bin-{i}">'
            f"<ProjectItem><Node><ID>{100 + i}</ID></Node><Name>{bin_name}</Name></ProjectItem>"
            f'<ProjectItemContainer><Items>{items}</Items></ProjectItemContainer>'
            f"</BinProjectItem>"
        )
        root_items.append(f'<Item Index="{i}" ObjectURef="bin-{i}"/>')

    clips_xml = []
    for i, (bin_name, names) in enumerate(files.items()):
        for j, fname in enumerate(names):
            clips_xml.append(
                f'<ClipProjectItem ObjectUID="clip-{i}-{j}">'
                f"<ProjectItem><Node><ID>{200 + i * 10 + j}</ID></Node>"
                f"<Name>{fname}</Name></ProjectItem>"
                f"<MasterClip/></ClipProjectItem>"
            )

    # 序列项目项放在根上（和实测工程一致）
    root_items.append('<Item Index="2" ObjectURef="item-seq-main"/>')
    root_items.append('<Item Index="3" ObjectURef="item-seq-all"/>')
    clips_xml.append(
        '<ClipProjectItem ObjectUID="item-seq-main">'
        "<ProjectItem><Node><ID>300</ID></Node><Name>主剪</Name></ProjectItem>"
        "<MasterClip/></ClipProjectItem>"
    )
    clips_xml.append(
        '<ClipProjectItem ObjectUID="item-seq-all">'
        "<ProjectItem><Node><ID>301</ID></Node><Name>总片</Name></ProjectItem>"
        "<MasterClip/></ClipProjectItem>"
    )

    # 「主剪」：ClipTrackItem → SubClip(ObjectRef) → Clip → Source(ObjectURef) → Media
    seq_main = f"""
    <Sequence ObjectUID="seq-main">
      <TrackGroups Version="1">
        <TrackGroup Version="1" Index="0"><Second ObjectRef="700"/></TrackGroup>
      </TrackGroups>
      <Name>主剪</Name>
    </Sequence>
    <VideoTrackGroup ObjectID="700">
      <Track><ClipItems>
        <ClipTrackItem Version="8"><SubClip ObjectRef="710"/></ClipTrackItem>
      </ClipItems></Track>
    </VideoTrackGroup>
    <Clip ObjectID="710"><Source ObjectURef="{media_uid['a.mp4']}"/></Clip>
    """

    # 「总片」：把一个 SequenceSource 当剪辑用 —— 这就是嵌套序列
    seq_all = """
    <Sequence ObjectUID="seq-all">
      <TrackGroups Version="1">
        <TrackGroup Version="1" Index="0"><Second ObjectRef="800"/></TrackGroup>
      </TrackGroups>
      <Name>总片</Name>
    </Sequence>
    <VideoTrackGroup ObjectID="800">
      <Track><ClipItems>
        <ClipTrackItem Version="8"><SubClip ObjectRef="810"/></ClipTrackItem>
      </ClipItems></Track>
    </VideoTrackGroup>
    <Clip ObjectID="810">
      <Source><VideoSequenceSource><Sequence ObjectURef="seq-main"/></VideoSequenceSource></Source>
    </Clip>
    """

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><Project>'
        '<RootProjectItem ObjectUID="root"><ProjectItemContainer><Items>'
        + "".join(root_items)
        + "</Items></ProjectItemContainer></RootProjectItem>"
        + "".join(bins_xml)
        + "".join(clips_xml)
        + "".join(media_xml)
        + seq_main
        + seq_all
        + "</Project>"
    )
    out = root / "seqtest.prproj"
    out.write_bytes(gzip.compress(xml.encode("utf-8")))
    return out


class TestSequenceScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = make_seq_project(self.root)
        self.m = ProjectModel.load(self.proj)

    def tearDown(self):
        self.tmp.cleanup()

    # ---------------- 解析 ----------------

    def test_lists_both_sequences(self):
        names = sorted(i.name for i in self.m.visible_sequences())
        self.assertEqual(names, ["主剪", "总片"])

    def test_sequence_media_closure(self):
        info = self.m.find_sequence("主剪")
        self.assertIsNotNone(info)
        self.assertEqual(info.file_count, 1)

    def test_nested_sequence_is_expanded(self):
        """嵌套序列的素材必须被递归带出来 —— 这是本功能最容易漏的地方。"""
        info = self.m.find_sequence("总片")
        self.assertIsNotNone(info)
        self.assertEqual(info.file_count, 1, "总片只嵌了主剪，间接用 1 个文件")
        self.assertIn("主剪", info.nested_names)
        self.assertTrue(self.m.find_sequence("主剪").is_nested)

    def test_closure_of_nested_selection(self):
        """只选外层序列时，内层序列也要算进「保留序列」，否则新工程里会缺。"""
        files, seqs, missing = self.m.media_closure(["总片"])
        self.assertEqual(len(files), 1)
        self.assertEqual(len(seqs), 2)
        self.assertEqual(missing, [])

    def test_unknown_sequence_reported(self):
        _files, _seqs, missing = self.m.media_closure(["不存在的序列"])
        self.assertEqual(missing, ["不存在的序列"])

    # ---------------- 计划 ----------------

    def test_plan_only_selected_sequence(self):
        plan = build_plan(self.m, self.root / "out", mode="bin", sequence_uids=["总片"])
        self.assertEqual(plan.total_files, 1)
        self.assertEqual(Path(plan.files[0].src).name, "a.mp4")
        self.assertTrue(plan.prune)

    def test_plan_drops_other_sequence(self):
        plan = build_plan(self.m, self.root / "out", mode="bin", sequence_uids=["主剪"])
        self.assertEqual(plan.sequence_names, ["主剪"])
        self.assertEqual(plan.total_files, 1)

    def test_plan_warns_about_excluded(self):
        plan = build_plan(self.m, self.root / "out", mode="bin", sequence_uids=["主剪"])
        joined = " ".join(plan.warnings)
        self.assertIn("总片", joined)

    def test_no_selection_keeps_everything(self):
        plan = build_plan(self.m, self.root / "out", mode="bin")
        self.assertEqual(plan.total_files, 3)
        self.assertFalse(plan.prune)
        self.assertEqual(plan.drop_item_uids, [])

    def test_empty_closure_raises(self):
        """选的序列一个文件都算不出来时，不能默默打出一个空包。"""
        with self.assertRaises(ValueError):
            build_plan(self.m, self.root / "out", sequence_uids=["不存在的序列"])

    # ---------------- 裁剪 ----------------

    def test_prune_removes_items_and_keeps_index_dense(self):
        files, seqs, _ = self.m.media_closure(["主剪"])
        drop, keep = self.m.prune_sets(files, seqs)
        self.assertTrue(drop)
        res = prune_items(self.m.xml_text, self.m, drop)
        self.assertEqual(index_sanity(res.text), [])
        # 级联摘掉的空箱也会计入，所以只会 >= len(drop)
        self.assertGreaterEqual(res.removed_items, len(drop))

    def test_prune_removes_bin_that_becomes_empty(self):
        """B 箱（b.mp4 / c.wav）里的两个文件都不在范围内 → 整箱也该摘掉。"""
        files, seqs, _ = self.m.media_closure(["主剪"])
        drop, _keep = self.m.prune_sets(files, seqs)
        res = prune_items(self.m.xml_text, self.m, drop)
        self.assertGreaterEqual(res.removed_bins, 1)

    def test_prune_reports_unknown_uid_safely(self):
        res = prune_items(self.m.xml_text, self.m, ["no-such-uid"])
        self.assertEqual(res.removed_items, 0)

    # ---------------- 端到端 ----------------

    def test_pack_selected_sequence_end_to_end(self):
        for pick in ("主剪", "总片"):
            with self.subTest(pick=pick):
                out = self.root / f"out_{pick}"
                plan = build_plan(self.m, out, mode="bin", sequence_uids=[pick])
                prepare_output(out)
                res = copy_plan(plan)
                self.assertEqual(res.failed, 0)
                self.assertEqual(res.copied, 1)

                rl = relink_project(plan, model=self.m)
                self.assertGreater(rl.pruned_items, 0)

                packed = ProjectModel.load(Path(rl.output_path))
                names_now = [i.name for i in packed.visible_sequences()]
                self.assertIn(pick, names_now)
                if pick == "主剪":
                    # 非嵌套：别的序列必须被摘掉
                    self.assertEqual(names_now, ["主剪"])
                else:
                    # 嵌套：内层序列必须留着，否则嵌套剪辑没得引用
                    self.assertEqual(sorted(names_now), ["主剪", "总片"])

                vr = verify_package(self.m, Path(rl.output_path), plan)
                self.assertTrue(vr.ok, [c.name + ":" + c.detail for c in vr.checks if not c.ok])

    def test_original_untouched_after_prune(self):
        before = self.proj.read_bytes()
        plan = build_plan(self.m, self.root / "out", mode="bin", sequence_uids=["主剪"])
        prepare_output(self.root / "out")
        copy_plan(plan)
        relink_project(plan, model=self.m)
        self.assertEqual(self.proj.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
