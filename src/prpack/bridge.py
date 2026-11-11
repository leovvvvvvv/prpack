# -*- coding: utf-8 -*-
"""给 CEP 面板用的桥接层：所有输出走 NDJSON（每行一个 JSON 事件）。

    python -m prpack.bridge inspect <工程.prproj>
    python -m prpack.bridge plan    <工程.prproj> -o <输出目录> [--mode bin|disk|flat]
    python -m prpack.bridge run     <plan.json> [--force]

事件类型：
    {"ev":"log",      "msg":"..."}
    {"ev":"progress", "done":12, "total":112, "cur":"01活动素材/a.mp4"}
    {"ev":"result",   "ok":true, "data":{...}}
    {"ev":"error",    "msg":"...", "trace":"..."}

面板逐行解析即可，不用猜文本格式。
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from .copyer import CopyError, copy_plan, prepare_output
from .plan import Plan, build_plan
from .prproj import ProjectModel
from .relink import relink_project
from .verify import verify_package, write_report


def emit(ev: str, **kw) -> None:
    kw["ev"] = ev
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} GB"


def _split_seq(raw: str | None) -> list[str] | None:
    """--sequences 支持逗号分隔的 uid / uid 前缀 / 序列名。"""
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p] or None


def _resolve_project(raw: str) -> Path:
    """把面板传来的路径落成一个真实存在的 .prproj。

    Premiere Pro 的 app.project.path 在不同版本/写法下语义不一致：多数版本
    给的是「.prproj 完整路径」，也有按目录处理的写法，于是会拼出
    `xxx.prproj\\xxx.prproj` 这种双份路径。这里做一次容错回退，
    错误信息也直接说人话，别让用户看 Errno 2。
    """
    p = Path(raw)
    if p.is_file():
        return p

    # 双份路径：上一级才是真工程
    parent = p.parent
    if parent.suffix.lower() == ".prproj" and parent.is_file():
        return parent

    raise FileNotFoundError(
        f"找不到工程文件：{p}\n"
        f"请确认 PR 里的工程已存盘（Ctrl+S），且面板顶部显示的路径是 .prproj 结尾。"
    )


# ---------------------------------------------------------------- inspect

def do_inspect(project: str) -> int:
    emit("log", msg="正在解析工程…")
    model = ProjectModel.load(_resolve_project(project))
    s = model.stats()

    tree = []
    for it in model.media_items():
        tree.append({"bin": "/".join(it.bin_path), "name": it.name, "file": it.filepath})

    seqs = [
        {
            "uid": i.uid,
            "name": i.name,
            "bin": "/".join(i.bin_path),
            "files": i.file_count,
            "nested": i.nested_names,
            "is_nested": i.is_nested,
        }
        for i in model.visible_sequences()
    ]

    emit(
        "result",
        ok=True,
        data={
            "project": s["project"],
            "bins": ["/".join(b) for b in model.bins],
            "sequences": seqs,
            "stats": {
                "bin_count": s["bin_count"],
                "bin_entries": s["bin_entries"],
                "sequence_items": s["sequence_items"],
                "sequence_count": s["sequence_count"],
                "orphan_sequences": s["orphan_sequences"],
                "media_items": s["media_items"],
                "unique_files": s["unique_files"],
                "unmatched": s["unmatched"],
                "offline": s["offline"],
            },
            "items": tree,
            "warnings": model.warnings,
        },
    )
    return 0


# ------------------------------------------------------------------- plan

def do_plan(project: str, output: str, mode: str = "bin",
            skip_unmatched: bool = False, include_deps: bool = True,
            plan_out: str | None = None, sequences: list[str] | None = None,
            prune: bool | None = None) -> int:
    emit("log", msg="正在解析工程…")
    model = ProjectModel.load(_resolve_project(project))

    if sequences:
        emit("log", msg=f"已选 {len(sequences)} 条序列，正在展开嵌套与素材…")

    emit("log", msg="正在计算去重与目标路径…")
    plan = build_plan(
        model, Path(output), mode=mode,
        include_deps=include_deps, skip_unmatched=skip_unmatched,
        sequence_uids=sequences, prune=prune,
    )
    pp = Path(plan_out) if plan_out else Path(output).parent / f"{model.path.stem}_plan.json"
    plan.save(pp)

    # 目标目录树（只给前两级，够面板展示）
    dirs: dict[str, int] = {}
    for f in plan.files:
        parts = f.dst_rel.replace("\\", "/").split("/")
        key = "/".join(parts[:-1]) or "(根)"
        dirs[key] = dirs.get(key, 0) + 1
    top = sorted(dirs.items(), key=lambda kv: -kv[1])[:40]

    emit(
        "result",
        ok=True,
        data={
            "plan_path": str(pp),
            "fingerprint": plan.fingerprint(),
            "total_files": plan.total_files,
            "total_bytes": plan.total_bytes,
            "total_text": _gb(plan.total_bytes),
            "mode": plan.mode,
            "output_root": plan.output_root,
            "sequences": plan.sequence_names,
            "prune": plan.prune,
            "drop_items": len(plan.drop_item_uids),
            "dirs": [{"path": k, "count": v} for k, v in top],
            "conflicts": len(plan.conflicts),
            "warnings": plan.warnings,
            "source_files": model.stats()["unique_files"],
        },
    )
    return 0


# -------------------------------------------------------------------- run

def do_run(plan_path: str, force: bool = False) -> int:
    plan = Plan.load(Path(plan_path))
    out_root = Path(plan.output_root)

    emit("log", msg=f"目标目录：{out_root}")
    prepare_output(out_root, force=force)
    emit("log", msg=f"待处理 {plan.total_files} 个文件，{_gb(plan.total_bytes)}")

    last = {"pct": -1}

    def on_progress(i: int, total: int, f) -> None:
        pct = int(i * 100 / total) if total else 0
        # 每 1% 或最后一个才推，避免刷屏
        if pct != last["pct"] or i == total:
            last["pct"] = pct
            emit("progress", done=i, total=total, pct=pct, cur=f.dst_rel)

    copy_res = copy_plan(plan, plan_file=Path(plan_path), progress=on_progress)
    emit("log", msg=f"复制完成：成功 {copy_res.copied} / 跳过 {copy_res.skipped} / 失败 {copy_res.failed}")

    emit("log", msg="正在改写工程内路径…")
    original = ProjectModel.load(Path(plan.project_path))
    relink_res = relink_project(plan, update_relative=True, model=original)
    if relink_res.pruned_items:
        emit("log", msg=f"已摘除 {relink_res.pruned_items} 个条目 / "
                        f"{relink_res.pruned_bins} 个空箱")
    emit("log", msg=f"新工程：{relink_res.output_path}")

    emit("log", msg="正在自检…")
    verify_res = verify_package(original, Path(relink_res.output_path), plan)

    report = out_root / "打包报告.md"
    write_report(report, plan, original.stats(), copy_res, relink_res, verify_res)

    emit(
        "result",
        ok=all(c.ok for c in verify_res.checks) and copy_res.failed == 0,
        data={
            "output_root": str(out_root),
            "project_out": str(relink_res.output_path),
            "report": str(report),
            "copied": copy_res.copied,
            "skipped": copy_res.skipped,
            "failed": copy_res.failed,
            "replaced_abs": relink_res.replaced_abs,
            "replaced_rel": relink_res.replaced_rel,
            "cleared_temp": relink_res.cleared_temp,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in verify_res.checks],
            "warnings": relink_res.warnings + verify_res.warnings,
        },
    )
    return 0


# ------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        emit("error", msg="缺少子命令：inspect / plan / run")
        return 2

    action, rest = args[0], args[1:]

    def opt(name: str, default=None):
        if name in rest:
            i = rest.index(name)
            if i + 1 < len(rest):
                return rest[i + 1]
        return default

    try:
        if action == "inspect":
            if not rest:
                emit("error", msg="缺少工程路径")
                return 2
            return do_inspect(rest[0])

        if action == "plan":
            if not rest:
                emit("error", msg="缺少工程路径")
                return 2
            out = opt("-o") or opt("--output")
            if not out:
                emit("error", msg="缺少输出目录 -o")
                return 2
            return do_plan(
                rest[0], out,
                mode=opt("--mode", "bin"),
                skip_unmatched="--skip-unmatched" in rest,
                include_deps="--no-deps" not in rest,
                plan_out=opt("--plan-out"),
                sequences=_split_seq(opt("--sequences")),
                prune=(False if "--no-prune" in rest
                       else (True if "--prune" in rest else None)),
            )

        if action == "run":
            if not rest:
                emit("error", msg="缺少 plan.json 路径")
                return 2
            return do_run(rest[0], force="--force" in rest)

        emit("error", msg=f"未知子命令：{action}")
        return 2

    except CopyError as exc:
        emit("error", msg=str(exc))
        return 2
    except (ValueError, FileNotFoundError) as exc:
        emit("error", msg=str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        emit("error", msg=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
