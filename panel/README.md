# prpack 面板（CEP 扩展）

Premiere Pro 里的 prpack 图形界面。它自己不做任何解析或复制，只负责：

1. 让 ExtendScript 报出当前工程路径
2. 用 Node 的 `child_process` 拉起 `python -m prpack.bridge`
3. 把 bridge 吐出的 NDJSON 事件流渲染成进度条、日志、结果

**核心（`pip install -e .` 或 `PRPACK_HOME` 指向仓库）必须先装好**，面板只是外壳。

## 安装

见主 [README](../README.md) 第三章：开 CEP 调试模式 → 把本目录拷到 CEP 扩展目录 → 设 `PRPACK_HOME` / `PRPACK_PYTHON`。

Windows 目标路径：`%APPDATA%\Adobe\CEP\extensions\com.prpack.panel`
macOS 目标路径：`~/Library/Application Support/Adobe/CEP/extensions/com.prpack.panel`

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` / `styles.css` | 界面 |
| `main.js` | 面板逻辑：定位 Python、拉起子进程、解析事件流 |
| `host.jsx` | ExtendScript 宿主端，只做三件事：报当前工程、报版本、必要时存盘 |
| `CSInterface.js` | Adobe 官方 CEP 桥接库（原样随包分发） |
| `CSXS/manifest.xml` | 扩展声明，`PPRO [14.0,99.9]` 即 PR 2020 及以上 |

## 环境变量

| 变量 | 作用 |
|---|---|
| `PRPACK_HOME` | 仓库根目录（含 `src/prpack/bridge.py`）。源码模式必设 |
| `PRPACK_PYTHON` | Python 可执行文件全路径。不设就走 PATH 里的 `python` |

面板里还能看到「核心路径」输入框，填了会写进 localStorage，优先级高于环境变量。

## 排查

1. 面板不出现：确认 `PlayerDebugMode=1` 已写进注册表 / defaults，且扩展目录名是 `com.prpack.panel`
2. 面板出现但点了没反应：看日志区第一行，通常是找不到 Python（设 `PRPACK_PYTHON`）或 `PRPACK_HOME` 指错了目录
3. `host.jsx 载入异常`：工程尚未保存（Ctrl+S 后重试），或 PR 版本低于 2020
