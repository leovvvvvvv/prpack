// CEP 桥接最小实现。只覆盖本面板用到的能力，不依赖 Adobe 官方 CSInterface。
// 参考 CEP 11 规范：__adobe_cep__.evalScript 必须带回调，否则结果被丢弃。

function CSInterface() {}

// Adobe 官方 CSInterface 里的路径常量，桩实现也必须有，否则 getSystemPath 拿不到实际目录
var SystemPath = {
  USER_DATA: "userData",
  COMMON_FILES: "commonFiles",
  MY_DOCUMENTS: "myDocuments",
  APPLICATION: "application",
  EXTENSION: "extension",
  HOST_APPLICATION: "hostApplication"
};

CSInterface.prototype.evalScript = function (script, callback) {
  if (typeof __adobe_cep__ === "undefined") {
    if (callback) callback("ERR|不在 CEP 环境中");
    return;
  }
  try {
    __adobe_cep__.evalScript(script, callback || function () {});
  } catch (e) {
    if (callback) callback("ERR|" + e.toString());
  }
};

CSInterface.prototype.getSystemPath = function (pathType) {
  if (typeof __adobe_cep__ === "undefined") return "";
  try {
    return __adobe_cep__.getSystemPath(pathType);
  } catch (e) {
    return "";
  }
};

CSInterface.prototype.getHostEnvironment = function () {
  if (typeof __adobe_cep__ === "undefined") return null;
  try {
    return JSON.parse(__adobe_cep__.getHostEnvironment());
  } catch (e) {
    return null;
  }
};

CSInterface.prototype.EXTENSION_ID = "extensionId";
CSInterface.prototype.USER_DATA = "userData";
CSInterface.prototype.APPLICATION = "application";
