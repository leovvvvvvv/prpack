# -*- coding: utf-8 -*-
"""prpack 命令行入口。

    prpack inspect <工程.prproj>              只读，看会打包些什么
    prpack plan    <工程.prproj> -o <输出目录>  出 plan.json，人工核对
    prpack run     <plan.json>                 按计划执行（复制 + 重写 + 校验 + 报告）
    prpack pack    <工程.prproj> -o <输出目录>  一步到位
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .copyer import CopyError, copy_plan, prepare_output
from .plan import Plan, build_plan
from .prproj import ProjectModel
from .relink import relink_project
from .verify import verify_package, write_report


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} GB"


def cmd_inspect(args: argparse.Namespace) -> int:
    model = ProjectModel.load(Path(args.project))
    s = model.stats(with_sizes=True)
    sizes = s.get("_sizes", {})

    print(f"工程：{s['project']}")
    print(f"  素材箱数        {s['bin_count']}")
    print(f"  面板项目项      {s['bin_entries']}")
    print(f"  序列项          {s['sequence_items']}")
    print(f"  媒体项          {s['media_items']}")
    print(f"  物理文件(去重)  {s['unique_files']}")
    print(f"  总体积          {_gb(s['total_bytes'])}")
    print(f"  未匹配文件      {s['unmatched']}")
    print(f"  离线文件        {s['offline']}")

    seqs = model.visible_sequences()
    if seqs:
        width = min(34, max(len(i.name) for i in seqs) + 2)
        print("\n序列（--sequences 只打勾选的，嵌套自动展开）：")
        for i in seqs:
            got = sum(sizes.get(k, 0) for k in i.files)
            got = max(got, 0)
            tag = ""
            if i.nested_names:
                tag = "  含嵌套: " + "、".join(i.nested_names[:3])
            print(f"  {i.name:<{width}} {i.file_count:>5} 个文件  {_gb(got):>10}{tag}")

    if model.bins:
        print("\n素材箱层级：")
        for b in model.bins:
            print("  " + "/".join(b))

    if s["unmatched_names"]:
        print("\n未认领的面板项（多为序列）：")
        for n in s["unmatched_names"]:
            print("  " + n)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    model = ProjectModel.load(Path(args.project))
    out_root = Path(args.output)
    plan = build_plan(
        model,
        out_root,
        mode=args.mode,
        include_deps=not args.no_deps,
        skip_unmatched=args.skip_unmatched,
        sequence_uids=args.sequences,
        prune=(False if args.no_prune else (True if args.prune else None)),
    )
    plan_path = Path(args.plan_out) if args.plan_out else out_root.parent / f"{model.path.stem}_plan.json"
    plan.save(plan_path)

    print(f"计划已生成：{plan_path}")
    print(f"  指纹          {plan.fingerprint()}")
    print(f"  文件数        {plan.total_files}")
    print(f"  总体积        {_gb(plan.total_bytes)}")
    print(f"  冲突          {len(plan.conflicts)}")
    print(f"  结构模式      {plan.mode}")
    print(f"  打包序列      {'、'.join(plan.sequence_names) or '全部'}")
    if plan.prune:
        print(f"  新工程摘除    {len(plan.drop_item_uids)} 个素材箱条目")
    for w in plan.warnings:
        print(f"  ⚠️  {w}")
    print("\n核对无误后执行：prpack run " + str(plan_path))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    plan = Plan.load(Path(args.plan))
    out_root = Path(plan.output_root)

    prepare_output(out_root, force=args.force)

    print(f"目标目录：{out_root}")
    print(f"待处理 {plan.total_files} 个文件，{_gb(plan.total_bytes)}\n")

    def on_progress(i: int, total: int, f) -> None:
        if i % 20 == 0 or i == total:
            print(f"  [{i}/{total}] {f.dst_rel[:70]}")

    copy_res = copy_plan(plan, plan_file=Path(args.plan), progress=on_progress)
    print(f"\n复制完成：复制 {copy_res.copied} / 跳过 {copy_res.skipped} / 失败 {copy_res.failed}")

    relink_res = None
    verify_res = None
    if args.no_relink:
        print("（已跳过硬链接改写）")
    else:
        original = ProjectModel.load(Path(plan.project_path))
        relink_res = relink_project(
            plan, update_relative=not args.no_relative, model=original
        )
        print(f"新工程：{relink_res.output_path}")
        print(f"  绝对路径替换 {relink_res.replaced_abs} 处 / 相对路径 {relink_res.replaced_rel} 处 / 清临时路径 {relink_res.cleared_temp} 处")
        for w in relink_res.warnings:
            print(f"  ⚠️  {w}")

        verify_res = verify_package(original, Path(relink_res.output_path), plan)
        print("\n校验：")
        for c in verify_res.checks:
            print(f"  {'✅' if c.ok else '❌'} {c.name} — {c.detail}")

    report = out_root / "打包报告.md"
    stats = ProjectModel.load(Path(plan.project_path)).stats()
    write_report(report, plan, stats, copy_res, relink_res, verify_res)
    print(f"\n报告：{report}")

    if verify_res is not None and not verify_res.ok:
        return 1
    if copy_res.failed:
        return 1
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    model = ProjectModel.load(Path(args.project))
    out_root = Path(args.output)
    plan = build_plan(
        model, out_root, mode=args.mode,
        include_deps=not args.no_deps, skip_unmatched=args.skip_unmatched,
        sequence_uids=args.sequences,
        prune=(False if args.no_prune else (True if args.prune else None)),
    )
    plan_path = out_root.parent / f"{model.path.stem}_plan.json"
    plan.save(plan_path)
    print(f"计划：{plan.total_files} 个文件 / {_gb(plan.total_bytes)} → {plan_path}")

    ns = argparse.Namespace(
        plan=str(plan_path), force=args.force,
        no_relink=False, no_relative=args.no_relative,
    )
    return cmd_run(ns)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prpack",
        description="把 Premiere Pro 工程连同素材按素材箱层级干净打包（替代 PR 项目管理器）",
    )
    p.add_argument("--version", action="version", version=f"prpack {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("inspect", help="只读统计")
    sp.add_argument("project")
    sp.set_defaults(func=cmd_inspect)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--output", required=True, help="输出目录")
    common.add_argument("--mode", choices=["bin", "disk", "flat"], default="bin",
                        help="目录结构模式，默认 bin（还原素材箱层级）")
    common.add_argument("--plan-out", help="plan.json 路径")
    common.add_argument("--skip-unmatched", action="store_true", help="不收集未匹配文件")
    common.add_argument("--no-deps", action="store_true", help="不收集依赖文件（sidecar 等）")
    common.add_argument("--no-relative", action="store_true", help="不改写相对路径")
    common.add_argument("--force", action="store_true", help="目标目录非空也继续（危险）")
    common.add_argument("--sequences", nargs="+", metavar="SEQ",
                        help="只打包这些序列（uid / uid 前缀 / 序列名），支持多个")
    common.add_argument("--prune", action="store_true", help="强制裁剪新工程（摘掉未选中的序列与未用素材）")
    common.add_argument("--no-prune", action="store_true", help="不裁剪新工程，只限制复制的素材")

    sp = sub.add_parser("plan", parents=[common], help="只出计划")
    sp.add_argument("project")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("run", help="按计划执行")
    sp.add_argument("plan")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--no-relink", action="store_true", help="只复制，不改工程")
    sp.add_argument("--no-relative", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("pack", parents=[common], help="一步到位")
    sp.add_argument("project")
    sp.set_defaults(func=cmd_pack)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CopyError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
