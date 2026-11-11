# prpack

**把 Premiere Pro 工程连同素材一起打包 —— 不臃肿，不丢素材箱层级。**

`prpack` 是一个零第三方依赖的 Python 工具 + Premiere 内置面板，专门治 Premiere Pro 自带「项目管理器」长期没修的两个毛病。

---

## 一、它解决什么问题

Premiere 的项目管理器（`文件 → 项目管理器 → 收集文件并复制到新位置`）有两个从 CS 时代一路带到 2026 的老毛病，Adobe 社区的报障最早能追到 2007 年：

1. **每个子剪辑都复制一份素材。**
   项目管理器是按「项目项」收集的，不是按「物理文件」。一个母素材被切出 12 个子剪辑，就会得到 **12 份完整拷贝**。13 GB 的母带切 10 刀，输出 143 GB。
2. **输出目录永远被压平。**
   所有文件都塞进同一个文件夹。你在 Premiere 里理好的素材箱结构、磁盘上分好的文件夹，全部消失。

附带问题：`排除未使用剪辑` 经常判错，大工程上动不动弹 `未知错误`。

实测对照（52 分钟活动片工程，Premiere 26.x）：

| | PR 项目管理器 | prpack |
|---|---|---|
| 磁盘上源文件 | 112 | 112 |
| 工程内子剪辑 | 198 | — |
| 复制的媒体文件 | **最多 198** | **112** |
| 输出目录结构 | 单一平铺 | 还原素材箱层级 |

---

## 二、功能

1. **按物理文件去重。** 198 个子剪辑共享 112 个文件，只复制 112 份，一个都不多。
2. **还原素材箱层级。** 默认 `--mode bin`，磁盘目录一比一对应你在 Premiere 里的素材箱；也可选按原始磁盘结构（`--mode disk`）或全平铺（`--mode flat`）。
3. **只打包你勾选的序列。** 粗剪工程里常躺着七八个版本，勾哪条打哪条。
4. **自动展开嵌套序列。** 序列 A 嵌在序列 B 里，勾 B 会把 A 用到的素材一并带上，不会漏。
5. **顺手把新工程减肥。** 没勾的序列、没用到的素材，从新工程的素材箱里摘掉，不是只跳过复制 —— 打开就是干净的交付工程。
6. **改写工程内素材路径。** `FilePath` 和 `RelativePath` 一起改，打包后的工程打开零离线，换盘符也能找到。
7. **先出计划再动手。** `plan` 只生成一份 JSON，不复制任何文件，看完落位再执行。
8. **打包完自检。** 重新解析新工程，逐个核验路径是否落地、是否有离线。
9. **Premiere 内图形界面。** 不用记命令行，面板里选序列、点按钮，带进度条和秒表。

---

## 三、安装

### 3.1 核心（必装）

需要 Python 3.10 以上，无第三方依赖。

```bash
pip install -e .
```

不想装也行，直接从源码跑：

```bash
# Windows PowerShell
$env:PYTHONPATH="D:\你的路径\prpack\src"; python -m prpack.cli --help

# macOS / Linux
PYTHONPATH=src python -m prpack.cli --help
```

### 3.2 Premiere 面板（可选，推荐）

面板是 CEP 扩展，会让 PR 里多一个「prpack 工程打包」窗口。**核心仍然要装**，面板只是调用它。

**第一步：打开 CEP 调试模式。** 未签名的扩展必须开这个才加载得上。PowerShell 里执行（把 9 到 14 都写一遍，省得猜版本）：

```powershell
9..14 | ForEach-Object {
  $k = "HKCU:\Software\Adobe\CSXS.$_"
  New-Item -Path $k -Force | Out-Null
  Set-ItemProperty -Path $k -Name PlayerDebugMode -Value "1" -Type String
}
```

macOS 对应命令：

```bash
for v in 9 10 11 12 13 14; do
  defaults write com.adobe.CSXS.$v PlayerDebugMode 1
done
```

**第二步：把 `panel/` 拷到 CEP 扩展目录。**

```powershell
# Windows
$dst = "$env:APPDATA\Adobe\CEP\extensions\com.prpack.panel"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force ".\panel\*" $dst
```

```bash
# macOS
cp -R panel ~/Library/Application\ Support/Adobe/CEP/extensions/com.prpack.panel
```

**第三步：告诉面板核心在哪。** 面板按这个顺序找 Python 和代码：

1. `PRPACK_HOME` 环境变量 → 指向仓库根目录（含 `src/prpack/bridge.py`）
2. `PRPACK_PYTHON` 环境变量 → 指向 Python 可执行文件（不设就用 PATH 里的 `python`）
3. 都不设也行 —— 如果 `pip install -e .` 装好了，`python` 在 PATH 里就能直接跑

用源码模式的话，设一下更稳：

```powershell
[Environment]::SetEnvironmentVariable("PRPACK_HOME", "D:\你的路径\prpack", "User")
[Environment]::SetEnvironmentVariable("PRPACK_PYTHON", "C:\Python313\python.exe", "User")
```

设完重启 Premiere，在 `窗口 → 扩展 → prpack 工程打包` 打开面板。

---

## 四、用法

### 4.1 命令行

**第一步：先看规模。** 什么都不复制，只出统计。

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
  总体积          128.42 GB
  未匹配文件      0
  离线文件        0

序列（--sequences 只打勾选的，嵌套自动展开）：
  正片 v4        96 个文件    34.67 GB  含嵌套: 空镜集
  预告 30s       22 个文件     8.10 GB

素材箱层级：
  01_footage
  02_music
  03_reference
  04_sfx
```

序列那一栏就是 `--sequences` 能填的名字，右边是这条序列实际要用到的物理文件数和体积 —— 打包前就知道要占多少盘。

**第二步：生成计划。** 仍然不复制。

```bash
prpack plan "D:\media\project\my_project.prproj" -o "D:\archive\my_project_packed" \
  --sequences "0920喀交会-1104"
```

输出目录旁边会生成 `my_project_plan.json`，里面每个源文件对应哪个落位一目了然。

**第三步：执行。**

```bash
prpack run "D:\archive\my_project_plan.json"
```

复制素材、改写新工程为 `my_project_packed.prproj`、自检、写 `打包报告.md`。

**想一步到位也可以：**

```bash
prpack pack "D:\media\project\my_project.prproj" -o "D:\archive\my_project_packed" \
  --sequences "0920喀交会-1104"
```

**选序列的写法**（名字、uid、uid 前缀都认）：

```bash
# 按名字，可以一次给多条
prpack plan "..." -o "..." --sequences "成片 v4" "预告 30s"

# 按 uid 前缀（名字带空格或重名时更省事）
prpack plan "..." -o "..." --sequences 7a8292ee 60ee1bcd
```

### 4.2 Premiere 面板

1. 在 PR 里打开工程，`窗口 → 扩展 → prpack 工程打包`
2. 面板自动读取当前工程路径（工程没存过会提示先 Ctrl+S）
3. 点 **分析** —— 出素材箱数、物理文件数、总体积，并列出所有序列
4. 勾选要打包的序列（可多选，嵌套会自动展开）
5. 选输出目录，点 **打包**
6. 进度条走完，日志区给出复制数、裁剪条目数、自检结果、新工程路径

面板的进度分三段：分析（读盘 → 解压 → 解析 → 索引 → 箱树 → 匹配 → 展开序列）、规划（逐文件算落位）、复制（按文件数）。阶段内部耗时不可预知时走跑马灯，旁边有秒表，不会让人误以为卡死。

### 4.3 参数

| 参数 | 说明 |
|---|---|
| `--mode bin\|disk\|flat` | 输出结构。`bin`（默认）还原 Premiere 素材箱层级 |
| `--sequences A B ...` | 只打包这些序列（名字 / uid / uid 前缀） |
| `--no-prune` | 配合 `--sequences`：只限制复制范围，保留新工程完整结构 |
| `--prune` | 不选序列时也可强制裁剪未使用素材 |
| `--no-deps` | 不收集依赖文件（`.sidecar`、`.RDC`、`.aep`） |
| `--skip-unmatched` | 不收集没有任何素材箱条目认领的媒体 |
| `--no-relative` | 不改写 `RelativePath` |
| `--force` | 输出目录非空也继续。**危险，会覆盖同名文件** |

---

## 五、工作原理

1. **解析。** `.prproj` 是 gzip 压缩的 XML。建立两张表：`ObjectID` 和 `ObjectUID` 是**两个独立命名空间**，混用会让引用解到完全无关的对象上。
2. **取物理文件全集。** 扫所有 `<Media><FilePath>`，这是「必须复制哪些文件」的唯一权威来源。
3. **取素材箱树。** 从 `RootProjectItem` / `BinProjectItem` → `ProjectItemContainer/Items/Item` 逐层走，得到每个条目的层级路径。
4. **名字匹配。** 素材箱条目对物理文件。序列条目有两个名字 —— `ProjectItem/Name`（面板显示名）和 `MasterClip/Name`（和 `<Sequence>/Name` 同步），两个都收作别名，否则 PR 复制/改名会留下同名残留，导致序列认错。对不上的文件照样复制，落到 `_未匹配素材/`，宁可放错目录也不能丢文件。
5. **复制。** 支持断点续传：目标已存在且大小一致就跳过。
6. **改写路径用整文本字面替换**，绝不重新序列化 XML。`ElementTree` 往返会改属性顺序和命名空间写法，Premiere 对此不容错。落盘前先校验 XML 能否解析，解析不了就不写。
7. **自检。** 重新解析新工程，逐条核验路径。

### 为什么不直接做无损裁剪

商业工具（PlumePack 等）会做无损修剪。`prpack` v1 故意不做：长 GOP 的 H.264/HEVC 要帧精确切割，属于另一个难得多的问题，做不好就是素材错帧。`prpack` 保证给你一份**正确**的、体积等于真实源文件的包；想更小再上修剪工具。

### 关于「减肥」的实现方式

裁剪只摘素材箱**条目**，不删背后的对象。摘掉引用后剩下的对象留在文件里变成惰性孤儿，Premiere 不显示、也不影响打开。这是刻意的：删对象才是把工程搞成打不开的元凶。

---

## 六、安全设计

1. **绝不覆盖源工程。** 输出永远是新的 `_packed.prproj`。
2. **输出目录非空默认拒绝**，除非显式 `--force`。
3. **计划指纹校验。** 执行时比对计划是否在你审阅后被改过，改过就中止。
4. **先写 `.part` 再改名。** 中途断电不会留下一个「看起来完整」的半截文件。
5. **落盘前校验 XML。** 改写后的工程解析不了就一个字节都不写。
6. **裁剪前先核对计数。** 摘除条目数量与预期不符即中止，不落盘。

---

## 七、已知限制

1. 只收集被 `<Media>` 引用的文件。只存在于嵌套 `.prproj` 子工程里的素材不会进去。
2. 素材箱条目按**名字**匹配。在 Premiere 里改过名、又没同步母剪辑名的条目，可能落到 `_未匹配素材/`。
3. 特效、预设、字体、MOGRT 的依赖不收集。
4. 在 Windows + Premiere 26.x 上实测。工程 XML 格式 Adobe 从未公开，大版本之间可能变。
5. 面板依赖 CEP，Adobe 已宣布逐步转向 UXP；CEP 在 PR 2026 上仍然可用。

---

## 八、更新日志

**v0.2.1**

1. 序列身份改用 `MasterClip/Name` 优先匹配，修「面板名相同导致序列被吞」的问题
2. 解析 / 规划 / 复制三段全部流式进度，面板加秒表与当前文件名
3. 修三处隐藏 IO：合并重复的 `exists`+`getsize` 扫描、去掉 `build_plan` 的 O(n²) 反查、省掉多余的 `stats()` 调用
4. 分析阶段直接给出工程总体积

**v0.2.0**

1. 按序列打包，支持多选
2. 嵌套序列递归展开
3. 新工程裁剪（只保留选中序列与其素材）
4. CEP 面板
5. 协议改为 GPL-3.0

**v0.1.0** —— 核心五层打通：解析、规划、复制、改写路径、自检。

---

## 九、许可

**GNU GPL v3.0**，全文见 [LICENSE](LICENSE)。

白话版：

1. **自己用随便用。** 给客户做项目、公司内部用，都不算分发，不触发任何义务。
2. **一旦分发就必须开源。** 发自己的分支、打包进产品、在它的代码上做衍生工具 —— 衍生作品必须同样以 GPL-3.0 发布并提供源码。不接受把这份代码闭源包装后当商业软件卖。
3. **没有担保。** 打包结果自己负责，核验通过之前保留原工程备份。

需要把 prpack 嵌进闭源产品，属于另一件事，开 issue 谈。
