# prpack

**Pack an Adobe Premiere Pro project with its media — without the bloat, without losing your bin structure.**

`prpack` is a small, dependency-free Python tool that replaces Premiere Pro's built-in **Project Manager** for the two things it gets wrong.

---

## The problem

Premiere's Project Manager (`File → Project Manager → Collect Files and Copy to a New Location`) has two long-standing defects that Adobe has never fixed — the oldest reports date back to 2007:

1. **It duplicates media per subclip.**
   Project Manager collects by *project item*, not by *physical file*. Cut one master clip into 12 subclips and you get **12 full copies** of the master file. A 13 GB master subclipped 10 times becomes 11 × 13 GB.
2. **It always flattens the output.**
   Every file lands in a single directory. Neither your Premiere bin structure nor your folder structure on disk is preserved.

Secondary issues: `Exclude Unused Clips` frequently misbehaves, and `Unknown error` is common on large projects.

Real-world measurement (a 52-minute event project, Premiere 26.x):

| | Project Manager | prpack |
|---|---|---|
| Source files on disk | 112 | 112 |
| Subclips in project | 198 | — |
| Media files copied | **up to 198** | **112** |
| Output folder layout | flat | bin hierarchy |

---

## What it does

- **Deduplicates by physical file.** 198 subclips sharing 112 files produce 112 copies. Nothing more.
- **Preserves your bin hierarchy on disk** (`--mode bin`, default). Or the original disk layout (`--mode disk`), or flat (`--mode flat`).
- **Packs only the sequences you pick** — and follows nested sequences all the way down, so a nested sequence's media is never missed.
- **Slims the packed project down** to the selected sequences. Unchecked sequences and unused media are removed from the new project's bins, not just skipped during the copy.
- **Rewrites the project's media paths** so the packed project opens with zero offline media.
- **Shows you a plan first.** `prpack plan` writes a `plan.json` you can read before anything is copied.
- **Verifies the result.** After packing, it re-opens the new project and checks that every path resolves.

---

## Install

Requires Python 3.10+. No third-party dependencies.

```bash
pip install -e .
```

Or run from source without installing:

```bash
PYTHONPATH=src python -m prpack.cli --help
```

---

## Usage

### 1. Look before you leap

```bash
prpack inspect "D:\media\project\my_project.prproj"
```

```
工程：my_project.prproj
  素材箱数        4
  面板项目项      117
  序列项          5
  媒体项          112
  物理文件(去重)  112
  未匹配文件      0
  离线文件        0

素材箱层级：
  01_footage
  02_music
  03_reference
  04_sfx
```

### 2. Generate a plan

```bash
prpack plan "D:\media\project\my_project.prproj" -o "D:\archive\my_project_packed"
```

This writes `my_project_plan.json` next to the output folder. **Nothing is copied yet.** Read it, check the destinations.

### 3. Execute

```bash
prpack run "D:\archive\my_project_plan.json"
```

Copies the media, rewrites the project as `my_project_packed.prproj`, verifies it, and writes `打包报告.md`.

### Or do it in one step

```bash
prpack pack "D:\media\project\my_project.prproj" -o "D:\archive\my_project_packed"
```

### Pack only some sequences

A rough-cut project usually holds several versions. Shipping all of them — and all their footage — is a waste. Pick the ones you want:

```bash
# by name
prpack plan "D:\media\project\my_project.prproj" -o "D:\archive\delivery" \
  --sequences "Final cut" "Cut v2"

# or by uid (any unique prefix works, handy when names contain spaces)
prpack plan "..." -o "..." --sequences 7a8292ee 60ee1bcd
```

What this does:

1. **Resolves the media set** for the selected sequences only. Nested sequences are expanded recursively — if sequence A sits inside sequence B, selecting B pulls in everything A uses.
2. **Copies just that footage.** Files used only by unselected sequences are not copied at all.
3. **Prunes the new project.** Bin entries for unselected sequences and unused media are removed, so the delivered project opens showing only what you picked. Empty bins left behind by the pruning are removed too.

Pruning is on by default whenever `--sequences` is used. Pass `--no-prune` to keep the full project structure and only limit what gets copied.

> How pruning works: it removes bin *entries*, not the objects behind them. Anything left unreferenced stays in the file as an inert orphan — Premiere does not show it. That is deliberate: deleting objects is how you end up with a project that refuses to open.

### Options

| Flag | Meaning |
|---|---|
| `--mode bin\|disk\|flat` | Output layout. `bin` (default) mirrors your Premiere bins. |
| `--sequences A B ...` | Pack only these sequences (names, uids, or uid prefixes). |
| `--no-prune` | With `--sequences`: copy only the scoped media but keep the project structure intact. |
| `--prune` | Without `--sequences`: still prune unused media out of the packed project. |
| `--no-deps` | Skip dependency collection (`.sidecar`, `.RDC`, `.aep`). |
| `--skip-unmatched` | Don't collect media that no bin item claims. |
| `--no-relative` | Don't rewrite `RelativePath` entries. |
| `--force` | Proceed even if the output folder is not empty. **Risky.** |

---

## How it works

1. **Parse.** A `.prproj` is gzip-compressed XML. `prpack` builds two lookup tables — `ObjectID` and `ObjectUID` are *separate namespaces* and must never be merged, or references resolve to unrelated objects.
2. **Collect the physical file set** from every `<Media><FilePath>` element. This is the authoritative list of files that must be copied.
3. **Collect the bin tree** from `RootProjectItem` / `BinProjectItem` → `ProjectItemContainer/Items/Item`.
4. **Match** bin entries to files by name. Anything unmatched still gets copied, into `_未匹配素材/`, and is flagged in the report — better a wrong folder than a lost file.
5. **Copy** with resume support (`skip if destination exists with the same size`).
6. **Rewrite paths by literal text replacement**, never by re-serializing the XML. `ElementTree` round-trips change attribute order and namespace handling, and Premiere is not forgiving about it.
7. **Verify** by re-opening the packed project and resolving every path.

`FilePath` (absolute) and `RelativePath` (relative) are both rewritten, so the package survives a drive-letter change.

### Why not lossless trim?

Premiere's Project Manager can transcode/trim; so can commercial tools like PlumePack. `prpack` deliberately does **not** trim in v1: lossless trimming of long-GOP H.264/HEVC requires frame-accurate surgery on the container, which is a different (and much harder) project. `prpack` gives you a *correct* package at the true source size; use a trim tool if you need it smaller.

---

## Safety design

- **Never overwrites your source project.** Output is always a new `_packed.prproj`.
- **Refuses non-empty output folders** unless you pass `--force`.
- **Plan fingerprint** is checked at execution time — if the plan changed after you reviewed it, the run aborts.
- **Writes `.part` then renames**, so an interrupted copy never looks complete.
- **XML is validated before writing.** If the rewritten project doesn't parse, nothing is written.

---

## Known limitations

- Only **files referenced by `<Media>`** are collected. Media used solely inside nested `.prproj` sub-projects is not traversed.
- Bin items matched **by name**; a media item renamed inside Premiere may fall back to `_未匹配素材/`.
- Effects, presets, fonts and MOGRT dependencies are not collected.
- Tested on Windows with Premiere 26.x. The XML format is not officially documented and may change between major versions.

---

## Roadmap

- [ ] CEP panel (in-Premiere GUI)
- [ ] `--mode bin` for nested bins with duplicate names
- [ ] Optional lossless trim for intra-frame codecs (ProRes / DNxHD)
- [ ] Dry-run HTML report

---

## License

**GNU GPL v3.0** — see [LICENSE](LICENSE).

This is a copyleft license. In plain terms:

- **Use it however you want**, including commercially, on your own projects and for clients. Running it locally is not distribution, so nothing is triggered.
- **If you distribute it** — ship a fork, bundle it into a product, or build on top of its code — the whole derivative must be released under the GPL v3 as well, with source available. You cannot take this code, wrap it in a closed-source product, and ship that.
- **No warranty.** Packed projects are your responsibility; keep a backup of the original until you have verified the result.

If you need to embed prpack in a proprietary product, that is a separate conversation — open an issue.

---

**中文摘要**：本项目采用 GPL-3.0。自己用、给客户做项目都不受限制；但**一旦分发**（发自己的分支、打包进产品、在它的代码上做衍生工具），衍生作品也必须以 GPL-3.0 开源。不接受把这份代码闭源包装后当作商业软件出售。
