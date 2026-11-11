// prpack 面板的 ExtendScript 宿主端。
// 只做三件事：报当前工程、报版本、必要时存盘。真正的重活在 Python 侧。

/**
 * 返回 "OK|<工程全路径>|<PR 版本>" 或 "ERR|<原因>"
 * 路径统一用 / 分隔，路径里不会出现 "|"，可以安全 split。
 *
 * 坑：Premiere Pro 的 app.project.path 是 .prproj 的完整路径（含文件名），
 * 跟 AE 的 Project.file.parent 语义不同。老版本里有人按"目录"处理，
 * 结果就拼成 xxx.prproj\xxx.prproj。这里按扩展名判断，两种都兼容。
 */
function prpack_info() {
    try {
        if (!app.project) {
            return "ERR|没有打开的工程";
        }

        var raw = app.project.path;
        var p = "";
        if (typeof raw === "string") {
            p = raw;
        } else if (raw && raw.fsName) {
            p = raw.fsName;
        }
        p = p.replace(/\\/g, "/");

        var full = p;
        if (p && !/\.prproj$/i.test(p)) {
            // 少数情况给的是目录，补工程名
            var name = app.project.name || "";
            if (!name) return "ERR|工程尚未保存，请先 Ctrl+S";
            if (!/\.prproj$/i.test(name)) name += ".prproj";
            full = p.replace(/\/+$/, "") + "/" + name;
        }

        if (!full) return "ERR|工程尚未保存，请先 Ctrl+S";
        return "OK|" + full + "|" + app.version;
    } catch (e) {
        return "ERR|" + e.toString();
    }
}

/** 存盘。打包读的是磁盘上的文件，未保存的改动必须落盘。 */
function prpack_save() {
    try {
        if (!app.project) return "ERR|没有打开的工程";
        if (app.project.dirty) {
            app.project.save();
            return "OK|已保存";
        }
        return "OK|无需保存";
    } catch (e) {
        return "ERR|" + e.toString();
    }
}

/** 面板启动时自检，用来快速定位环境问题。 */
function prpack_probe() {
    try {
        var info = prpack_info();
        var ver = "";
        try { ver = app.version; } catch (e) { ver = "?"; }
        return info + "||" + ver;
    } catch (e) {
        return "ERR|" + e.toString();
    }
}
