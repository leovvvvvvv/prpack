/* prpack 面板逻辑
 *
 * 链路：ExtendScript 取工程路径 -> Node spawn Python -> Python 吐 NDJSON -> 面板渲染
 * 没有文件桥，没有轮询，进度是流式的。
 */

(function () {
  "use strict";

  var cs = new CSInterface();

  // ---------------------------------------------------------------- 环境

  var nodeRequire =
    typeof window.cep_node !== "undefined"
      ? window.cep_node.require
      : typeof require !== "undefined"
      ? require
      : null;

  var fs = null, pathMod = null, cp = null;
  if (nodeRequire) {
    try {
      fs = nodeRequire("fs");
      pathMod = nodeRequire("path");
      cp = nodeRequire("child_process");
    } catch (e) {
      log("Node 模块加载失败：" + e, "err");
    }
  }

  // 核心定位顺序：localStorage > 环境变量 PRPACK_HOME > 留空（直接用 pip 装好的包）
  var CORE_ROOT =
    localStorage.getItem("prpack.core") || process.env.PRPACK_HOME || "";

  // Python 定位顺序：环境变量 PRPACK_PYTHON > PATH 里的 python
  var PYTHON_CANDIDATES = [];
  if (process.env.PRPACK_PYTHON) PYTHON_CANDIDATES.push(process.env.PRPACK_PYTHON);
  PYTHON_CANDIDATES.push("python", "python3");

  var state = {
    project: "",
    outDir: localStorage.getItem("prpack.out") || "",
    planPath: "",
    busy: false,
    stage: "",
    action: "",     // 当前跑的子命令：inspect / plan / run（进度分段用）
    seqs: [],       // inspect 回来的序列清单
    picked: {}      // uid -> 是否勾选
  };

  // ------------------------------------------------------------------- DOM

  function $(id) { return document.getElementById(id); }

  function log(msg, cls) {
    var box = $("log");
    var line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = msg;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
    // 防止日志无限增长
    while (box.childNodes.length > 400) box.removeChild(box.firstChild);
  }

  function setProgress(pct, text, count) {
    var bar = $("bar");
    if (pct === null || pct === undefined) {
      // 阶段内部进度未知：跑马灯，至少让人看见在动
      bar.classList.add("indet");
    } else {
      bar.classList.remove("indet");
      $("fill").style.width = Math.max(0, Math.min(100, pct)) + "%";
    }
    if (text !== undefined) $("pText").textContent = text;
    if (count !== undefined) $("pCount").textContent = count || "";
  }

  /** 阶段内部的小字：当前处理的文件。只留最后两段，太长没意义。 */
  function setCur(rel) {
    var box = $("pCur");
    if (!rel) { box.textContent = ""; box.title = ""; return; }
    var parts = String(rel).replace(/\\/g, "/").split("/");
    var short = parts.slice(-2).join("/");
    box.textContent = short;
    box.title = rel;
  }

  /** 把某个子命令自己的 0–100 映射到整条流程上的一段，避免进度条反复归零。
   *  打包 = plan(0–18%) + run(18–100%)，分析 = 独立一段。 */
  function mapPct(pct) {
    if (pct === null || pct === undefined) return null;
    var span = { inspect: [0, 1], plan: [0, 0.18], run: [0.18, 1] }[state.action] || [0, 1];
    var v = span[0] + (pct / 100) * (span[1] - span[0]);
    return Math.round(v * 100);
  }

  // 计时器：解析大工程要几秒到几十秒，有秒表用户才不觉得卡死
  var clock = { t0: 0, id: null };

  function startClock() {
    stopClock();
    clock.t0 = Date.now();
    $("pTime").textContent = "0.0s";
    clock.id = setInterval(function () {
      $("pTime").textContent = ((Date.now() - clock.t0) / 1000).toFixed(1) + "s";
    }, 100);
  }

  function stopClock() {
    if (clock.id) { clearInterval(clock.id); clock.id = null; }
  }

  function setBusy(busy) {
    state.busy = busy;
    $("btnAnalyze").disabled = busy;
    $("btnPick").disabled = busy;
    document.body.style.cursor = busy ? "progress" : "";
    if (busy) {
      startClock();
      $("btnPack").disabled = true;
    } else {
      stopClock();
      setCur("");
      if (typeof updateSeqHint === "function") {
        updateSeqHint();
      } else {
        $("btnPack").disabled = !state.project;
      }
    }
  }

  function fmtBytes(n) {
    if (!n) return "0";
    var gb = n / 1024 / 1024 / 1024;
    if (gb >= 1) return gb.toFixed(2) + " GB";
    return (n / 1024 / 1024).toFixed(0) + " MB";
  }

  // --------------------------------------------------------------- 宿主脚本

  /**
   * 每次面板加载都从磁盘重新 evalFile 一遍 host.jsx。
   * ScriptPath 只在扩展初始化时读一次，改完不重启 PR 就不生效 —— 这层让它即时生效。
   */
  function ensureHostScript(cb) {
    var extPath = "";
    try { extPath = cs.getSystemPath(SystemPath.EXTENSION) || ""; } catch (e) { extPath = ""; }
    if (!extPath) { cb(); return; }
    var jsx = extPath.replace(/\\/g, "/").replace(/\/+$/, "") + "/host.jsx";
    try {
      cs.evalScript('$.evalFile("' + jsx + '")', function (r) {
        // $.evalFile 成功时返回 undefined，CEP 会把它转成字符串 "undefined" —— 不是错误
        var s = r === null || r === undefined ? "" : String(r).trim();
        if (s && s !== "undefined" && s !== "null" && /error|Error|Unable|EvalScript/i.test(s)) {
          log("host.jsx 载入异常：" + s, "warn");
        }
        cb();
      });
    } catch (e) {
      log("host.jsx 载入失败：" + e.message, "warn");
      cb();
    }
  }

  // --------------------------------------------------------------- 工程信息

  function loadProject() {
    cs.evalScript("prpack_info()", function (res) {
      if (!res || res.indexOf("ERR|") === 0) {
        state.project = "";
        $("projName").textContent = "没有可用的工程";
        $("projPath").textContent = (res || "").replace("ERR|", "");
        $("btnPack").disabled = true;
        log("读取工程失败：" + (res || "").replace("ERR|", ""), "err");
        return;
      }
      var parts = res.split("|");
      state.project = parts[1] || "";
      var name = state.project.replace(/\\/g, "/").split("/").pop();
      $("projName").textContent = name;
      $("projPath").textContent = state.project;
      $("btnPack").disabled = false;
      log("已加载工程：" + name, "dim");

      // 路径必须真实存在，否则后面的解析一定报 Errno 2 —— 提前拦，别让用户猜
      if (fs) {
        var asIs = state.project.replace(/\//g, "\\");
        if (!fs.existsSync(asIs)) {
          log("✗ 工程文件不存在：" + asIs, "err");
          log("如果 PR 里确实开着工程，请 Ctrl+S 存盘后重开面板", "warn");
          $("btnPack").disabled = true;
        }
      }
      if (!state.outDir) {
        $("outDir").value = suggestOutDir(state.project);
      }
    });
  }

  function suggestOutDir(projectPath) {
    var norm = projectPath.replace(/\\/g, "/");
    var dir = norm.slice(0, norm.lastIndexOf("/"));
    var stem = norm.slice(norm.lastIndexOf("/") + 1).replace(/\.prproj$/i, "");
    return dir + "/" + stem + "_打包";
  }

  // ----------------------------------------------------------- Python 探测

  function resolveCore() {
    if (!fs) return null;
    // 只有源码模式才校验目录；pip 安装模式下 core 为空，交给 python 自己找包
    if (CORE_ROOT) {
      var bridge = pathMod.join(CORE_ROOT, "src", "prpack", "bridge.py");
      if (!fs.existsSync(bridge)) {
        log("环境变量 PRPACK_HOME 指向的目录里没有 src/prpack/bridge.py", "err");
        log("当前值： " + CORE_ROOT, "dim");
        return null;
      }
    }
    for (var i = 0; i < PYTHON_CANDIDATES.length; i++) {
      var exe = PYTHON_CANDIDATES[i];
      if (exe.indexOf("python") === 0) return { exe: exe, core: CORE_ROOT };
      if (fs.existsSync(exe)) return { exe: exe, core: CORE_ROOT };
    }
    log("找不到可用的 Python 解释器，请设置环境变量 PRPACK_PYTHON", "err");
    return null;
  }

  // ------------------------------------------------------------- 子进程执行

  function runBridge(action, extraArgs, onDone) {
    state.action = action;
    var resolved = resolveCore();
    if (!resolved) {
      setBusy(false);
      return;
    }
    if (!cp) {
      log("Node 不可用，无法启动子进程", "err");
      setBusy(false);
      return;
    }

    var args = ["-m", "prpack.bridge", action].concat(extraArgs);
    var env = {};
    for (var k in process.env) env[k] = process.env[k];
    if (resolved.core) env.PYTHONPATH = pathMod.join(resolved.core, "src");
    env.PYTHONIOENCODING = "utf-8";
    env.PYTHONUTF8 = "1";

    log("$ prpack " + action + " " + extraArgs.join(" "), "dim");

    var child;
    try {
      child = cp.spawn(resolved.exe, args, {
        cwd: resolved.core || undefined,
        env: env,
        windowsHide: true
      });
    } catch (e) {
      log("启动失败：" + e.message, "err");
      setBusy(false);
      return;
    }

    var buf = "";
    var errBuf = "";
    var gotResult = false;

    child.stdout.on("data", function (chunk) {
      buf += chunk.toString("utf8");
      var idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        var line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        var ev;
        try { ev = JSON.parse(line); } catch (e) { log(line, "dim"); continue; }
        handleEvent(ev);
        if (ev.ev === "result" || ev.ev === "error") gotResult = true;
      }
    });

    child.stderr.on("data", function (chunk) {
      errBuf += chunk.toString("utf8");
    });

    child.on("error", function (e) {
      log("子进程错误：" + e.message, "err");
      setBusy(false);
    });

    child.on("close", function (code) {
      if (errBuf.trim()) log(errBuf.trim().split("\n").slice(-6).join("\n"), "err");
      if (!gotResult) log("进程退出，无结果（code=" + code + "）", "err");
      if (onDone) onDone(code, gotResult);
      if (code !== 0) setBusy(false);
    });
  }

  function handleEvent(ev) {
    if (ev.ev === "log") {
      log(ev.msg);
    } else if (ev.ev === "progress") {
      // Python 侧统一推：解析/规划的 pct 是阶段权重，复制阶段是文件数
      var cnt = "";
      if (ev.done !== undefined && ev.total !== undefined) {
        cnt = ev.done + " / " + ev.total;
      }
      setProgress(mapPct(ev.pct), ev.msg || "处理中…", cnt);
      if (ev.cur) setCur(ev.cur);
    } else if (ev.ev === "error") {
      log("✗ " + ev.msg, "err");
      if (ev.trace) log(ev.trace.split("\n").slice(-8).join("\n"), "dim");
      setProgress(0, "失败", "");
      setCur("");
    } else if (ev.ev === "result") {
      if (ev.ok === false) log("✗ 完成但有错误", "err");
      handleResult(ev.data, ev.ok);
    }
  }

  function handleResult(data, ok) {
    if (!data) return;

    // inspect
    if (data.stats) {
      var s = data.stats;
      $("sFiles").textContent = s.unique_files;
      $("sSize").textContent = s.total_text || fmtBytes(s.total_bytes);
      $("sBins").textContent = s.bin_count;
      var off = $("sOffline");
      off.textContent = s.offline;
      off.className = s.offline > 0 ? "bad" : "";
      $("binHint").textContent = s.media_items + " 个项目项";
      state.seqs = data.sequences || [];
      state.picked = {};
      state.seqs.forEach(function (q) { state.picked[q.uid] = true; });
      renderSeqs();
      renderBins(data.items || []);
      if (s.offline > 0) {
        log("⚠️ 有 " + s.offline + " 个媒体离线，打包后同样会离线", "warn");
      }
      if (!state.seqs.length) {
        log("⚠️ 工程里没有找到序列，将按整个工程打包", "warn");
      } else if (s.orphan_sequences) {
        log("另有 " + s.orphan_sequences + " 条序列在素材箱里没有条目（工程历史残留），不影响打包", "dim");
      }
      setProgress(100, "分析完成", "");
      setCur("");
      return;
    }

    // plan
    if (data.plan_path) {
      state.planPath = data.plan_path;
      $("sFiles").textContent = data.total_files;
      $("sSize").textContent = data.total_text;
      if (data.dirs) renderDirs(data.dirs);
      log(
        "计划：复制 " + data.total_files + " 个文件 / " + data.total_text +
        "（源工程 " + data.source_files + " 个物理文件）",
        "ok"
      );
      if (data.sequences && data.sequences.length) {
        log("打包序列：" + data.sequences.join("、"), "dim");
      }
      if (data.prune) {
        log("新工程将摘除 " + data.drop_items + " 个素材箱条目", "dim");
      }
      if (data.warnings && data.warnings.length) {
        data.warnings.forEach(function (w) { log("⚠️ " + w, "warn"); });
      }
      return;
    }

    // run 结果
    if (data.checks) {
      setProgress(100, ok ? "打包完成" : "完成但有错误", "");
      log("复制 " + data.copied + " / 跳过 " + data.skipped + " / 失败 " + data.failed, ok ? "ok" : "warn");
      data.checks.forEach(function (c) {
        log((c.ok ? "✓ " : "✗ ") + c.name + " — " + c.detail, c.ok ? "ok" : "err");
      });
      log("新工程：" + data.project_out, "ok");
      log("报告：" + data.report, "dim");
      if (state.outDir && fs && fs.existsSync(state.outDir)) {
        try { cp.spawn("explorer.exe", [state.outDir], { detached: true }); } catch (e) {}
      }
    }
  }

  // ------------------------------------------------------------ 序列勾选

  function pickedUids() {
    var out = [];
    state.seqs.forEach(function (q) {
      if (state.picked[q.uid]) out.push(q.uid);
    });
    return out;
  }

  function updateSeqHint() {
    var n = pickedUids().length;
    var total = state.seqs.length;
    if (!total) {
      $("seqHint").textContent = "工程里没有序列";
      $("btnPack").disabled = state.busy || !state.project;
      return;
    }
    $("seqHint").textContent = "已选 " + n + " / " + total;
    var ok = n > 0 && !!state.project;
    $("btnPack").disabled = state.busy || !ok;
  }

  function renderSeqs() {
    var box = $("seqList");
    box.innerHTML = "";
    if (!state.seqs.length) {
      box.innerHTML = '<div class="empty">工程里没有序列</div>';
      updateSeqHint();
      return;
    }
    state.seqs.forEach(function (q) {
      var row = document.createElement("label");
      row.className = "seqRow";

      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!state.picked[q.uid];
      cb.addEventListener("change", function () {
        state.picked[q.uid] = cb.checked;
        updateSeqHint();
      });

      var nm = document.createElement("span");
      nm.className = "seqName";
      nm.textContent = q.name;
      nm.title = (q.bin ? q.bin + " / " : "") + q.name;

      var meta = document.createElement("span");
      meta.className = "seqMeta";
      meta.textContent = q.files + " 文件";

      row.appendChild(cb);
      row.appendChild(nm);
      row.appendChild(meta);

      if (q.nested && q.nested.length) {
        var tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = "含嵌套 " + q.nested.length;
        tag.title = "嵌套序列：" + q.nested.join("、");
        row.appendChild(tag);
      }
      box.appendChild(row);
    });
    updateSeqHint();
  }

  // ------------------------------------------------------------ 素材箱/目录

  function renderBins(items) {
    var box = $("binList");
    box.innerHTML = "";
    var counts = {};
    items.forEach(function (it) {
      var key = it.bin || "(根)";
      counts[key] = (counts[key] || 0) + 1;
    });
    var keys = Object.keys(counts).sort();
    if (!keys.length) {
      box.innerHTML = '<div class="empty">工程里没有媒体项</div>';
      return;
    }
    keys.forEach(function (k) {
      var row = document.createElement("div");
      row.className = "binRow";
      var n = document.createElement("span");
      n.textContent = k;
      var c = document.createElement("span");
      c.className = "cnt";
      c.textContent = counts[k] + " 个";
      row.appendChild(n);
      row.appendChild(c);
      box.appendChild(row);
    });
  }

  function renderDirs(dirs) {
    var box = $("binList");
    box.innerHTML = "";
    dirs.slice(0, 40).forEach(function (d) {
      var row = document.createElement("div");
      row.className = "binRow";
      var n = document.createElement("span");
      n.textContent = d.path;
      var c = document.createElement("span");
      c.className = "cnt";
      c.textContent = d.count + " 个";
      row.appendChild(n);
      row.appendChild(c);
      box.appendChild(row);
    });
    $("binHint").textContent = "目标目录 " + dirs.length + " 个";
  }

  // --------------------------------------------------------------- 交互

  $("btnAnalyze").addEventListener("click", function () {
    if (!state.project) return;
    setBusy(true);
    setProgress(0, "分析中…", "");
    runBridge("inspect", [state.project], function () { setBusy(false); });
  });

  $("btnPack").addEventListener("click", function () {
    if (!state.project) return;
    var out = $("outDir").value.trim();
    if (!out) {
      log("请先选输出目录", "err");
      return;
    }
    var picks = pickedUids();
    if (state.seqs.length && !picks.length) {
      log("至少要勾一条序列", "err");
      return;
    }
    state.outDir = out;
    localStorage.setItem("prpack.out", out);

    setBusy(true);
    setProgress(0, "准备中…", "");
    log("—— 开始打包 ——", "dim");
    if (picks.length) {
      log("打包范围：" + state.seqs.filter(function (q) {
        return state.picked[q.uid];
      }).map(function (q) { return q.name; }).join("、"), "dim");
    }

    // 先确保工程已存盘，否则读到的还是旧结构
    cs.evalScript("prpack_save()", function (r) {
      if (r && r.indexOf("ERR|") === 0) log("存盘： " + r, "warn");
      else log("存盘： " + (r || "").replace("OK|", ""), "dim");

      var planArgs = [state.project, "-o", out];
      if (picks.length) planArgs.push("--sequences", picks.join(","));

      runBridge("plan", planArgs, function (code) {
        if (code !== 0) return;
        if (!state.planPath) {
          log("没有拿到计划，中止", "err");
          setBusy(false);
          return;
        }
        setProgress(18, "开始复制…", "");
        runBridge("run", [state.planPath], function () { setBusy(false); });
      });
    });
  });

  $("btnSeqAll").addEventListener("click", function () {
    state.seqs.forEach(function (q) { state.picked[q.uid] = true; });
    renderSeqs();
  });

  $("btnSeqNone").addEventListener("click", function () {
    state.seqs.forEach(function (q) { state.picked[q.uid] = false; });
    renderSeqs();
  });

  $("btnPick").addEventListener("click", function () {
    if (typeof window.cep === "undefined" || !window.cep.fs) {
      log("CEP 文件对话框不可用，请手动输入路径", "warn");
      return;
    }
    var r = window.cep.fs.showOpenDialog(false, true, "选择输出目录", state.outDir || "");
    if (r.err === 0 && r.data && r.data.length) {
      $("outDir").value = r.data[0];
      state.outDir = r.data[0];
      localStorage.setItem("prpack.out", r.data[0]);
    }
  });

  $("outDir").addEventListener("change", function () {
    state.outDir = this.value.trim();
    localStorage.setItem("prpack.out", state.outDir);
  });

  $("logClear").addEventListener("click", function () {
    $("log").innerHTML = "";
  });

  // --------------------------------------------------------------- 启动

  window.addEventListener("load", function () {
    if (!nodeRequire) {
      log("Node 未启用：请确认 manifest 里的 --enable-nodejs", "err");
    }
    log("prpack 面板已加载（核心：" + (CORE_ROOT || "已安装的包") + "）", "dim");
    if (state.outDir) $("outDir").value = state.outDir;
    setProgress(0, "待命", "");
    ensureHostScript(loadProject);
  });
})();
