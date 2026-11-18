/* 体征档案 */
(function () {
  "use strict";

  var ENDPOINT = "state";
  var PLUGIN = "astrbot_plugin_bio_status";

  /* 兜底名：接口没返回 stageLabels / stateLabels 时才用 */
  var STAGE_FALLBACK = { normal: "平静", mild: "微热", full: "高涨", pounce: "难耐" };
  var INSERT_FALLBACK = { none: "无", moving: "抽动中", inserted: "在位", pulled: "已退出" };

  /* 事件类型名，属于 UI 骨架，不随配置变 */
  var KIND_CN = { stage: "档位", inject: "灌入", pen: "进入", exit: "退出", satisfied: "满足" };

  var themeApplied = false;
  var tempBase = 37.2;
  var tempRise = 3.3;

  var last = null;      // 最近一次快照
  var allEvents = [];   // 全量事件（前端累积）
  var view = "overview";

  function $(id) { return document.getElementById(id); }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  function hhmmss(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) { return "--:--"; }
    sec = Math.max(0, Math.floor(sec));
    return pad(Math.floor(sec / 3600)) + ":" + pad(Math.floor((sec % 3600) / 60)) + ":" + pad(sec % 60);
  }

  function stamp(t) {
    var d = new Date(t * 1000);
    return pad(d.getMonth() + 1) + "/" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function apiUrl(path) {
    return "/api/v1/plugins/extensions/" + PLUGIN + "/" + path;
  }

  /* 部分 SDK 的 apiPost 只收 endpoint 名，这里统一剥掉前面的固定部分 */
  function bare(path) {
    var prefix = "/api/v1/plugins/extensions/" + PLUGIN + "/";
    return path.indexOf(prefix) === 0 ? path.slice(prefix.length) : path;
  }

  function apiGet(path) {
    var p = bare(path);
    if (window.AstrBotPluginPage && typeof window.AstrBotPluginPage.apiGet === "function") {
      return window.AstrBotPluginPage.apiGet(p);
    }
    if (window.AstrBotPluginView && typeof window.AstrBotPluginView.apiGet === "function") {
      return window.AstrBotPluginView.apiGet(p);
    }
    return Promise.reject(new Error("页面还没准备好，刷新一下"));
  }

  function apiPost(path, body) {
    var p = bare(path);
    var sdk = (window.AstrBotPluginPage && window.AstrBotPluginPage.apiPost)
      ? window.AstrBotPluginPage
      : ((window.AstrBotPluginView && window.AstrBotPluginView.apiPost) ? window.AstrBotPluginView : null);
    if (sdk) {
      return sdk.apiPost(p, body || {});
    }
    return Promise.reject(new Error("页面还没准备好，刷新一下"));
  }

  function unwrap(res) {
    if (!res) { return null; }
    if (res.data && typeof res.data === "object" && "desire" in res.data) { return res.data; }
    if ("desire" in res) { return res; }
    return res.data || res;
  }

  function zoneIndex(pct) {
    if (pct < 25) { return 0; }
    if (pct < 55) { return 1; }
    if (pct < 85) { return 2; }
    return 3;
  }

  /* ---------------- 总览 ---------------- */

  function renderOverview(d) {
    var desire = Math.max(0, Math.min(100, Number(d.desire || 0)));
    var cap = Number(d.capacity || 200);
    var residue = Number(d.residue || 0);
    var rPct = cap ? Math.min(100, (residue / cap) * 100) : 0;
    var temp = Number(d.temperature || 0);
    var heat = Math.max(0, Math.min(100, ((temp - tempBase) / tempRise) * 100));

    /* 中间小指标 */
    $("mStore").innerHTML = Math.round(rPct) + "<small>%</small>";
    $("mInsert").textContent = stateName(d);
    $("mStage").textContent = stageName(d);
    $("mTemp").innerHTML = temp.toFixed(1) + "<small>℃</small>";

    /* 参数条 */
    var since = d.sinceSatisfied;
    $("pSince").textContent = (since === null || since === undefined) ? "--:--" : hhmmss(since);
    $("pAbsorb").textContent = Number(d.absorbPerHour || 4);
    $("pTemp").innerHTML = temp.toFixed(2) + "<small>℃</small>";
    var hours = Number(d.hoursToEmpty || 0);
    $("pHold").textContent = residue > 0 ? hhmmss(hours * 3600) : "--:--";

    /* 右栏：储存 */
    $("rRes").textContent = residue.toFixed(1);
    $("rCap").textContent = cap;
    $("rPct").textContent = Math.round(rPct) + "%";
    $("rBar").style.width = rPct + "%";

    var zi = zoneIndex(rPct);
    var zones = $("zones").children;
    for (var i = 0; i < zones.length; i++) {
      zones[i].className = "zone" + (i === zi && residue > 0 ? " on" : "");
    }

    /* 右栏：核心能量（真实热度） */
    $("coreVal").textContent = heat.toFixed(1);
    $("coreBar").style.width = heat + "%";
    $("coreSrc").textContent = "温度 " + temp.toFixed(2) + "℃ · 基准 " + tempBase.toFixed(1) + " / 上限 " + (tempBase + tempRise).toFixed(1);

    /* 右栏：欲望 */
    $("rDesire").textContent = desire;
    $("rDesirePct").textContent = desire + "%";
    $("rDesireBar").style.width = desire + "%";
    $("rNeed").textContent = Math.round(desire / 5);
    $("rStage").textContent = stageName(d);

    /* 右栏：近期 */
    var list = Array.isArray(d.events) ? d.events.slice().reverse() : [];
    var feed = $("feed");
    if (!list.length) {
      feed.innerHTML = '<div style="border:none;">还没有事件</div>';
    } else {
      var html = "";
      for (var j = 0; j < list.length && j < 20; j++) {
        var e = list[j];
        var dt = new Date(e.t * 1000);
        html += '<div><span class="t">' + pad(dt.getHours()) + ":" + pad(dt.getMinutes()) + "</span>" +
          '<span class="k">' + (KIND_CN[e.kind] || e.kind) + "</span>" +
          '<span class="tag">' + (e.detail || "") + "</span></div>";
      }
      feed.innerHTML = html;
    }

    $("feedState").style.color = "#5fe8ff";

    /* 心脏跳动随档位 */
    var heart = $("heart");
    if (heart) {
      heart.style.animationDuration =
        d.stage === "pounce" ? "0.9s" : (d.stage === "full" ? "1.6s" : (d.stage === "mild" ? "2.4s" : "3.4s"));
    }
  }

  /* ---------------- 事件日志 ---------------- */

  function renderEvents() {
    var box = $("logList");
    if (!box) { return; }
    if (!allEvents.length) {
      box.innerHTML = '<div style="color:#624a92;padding:10px;">还没有记录</div>';
      $("logCount").textContent = "0 条";
      return;
    }
    var html = "";
    for (var i = allEvents.length - 1; i >= 0; i--) {
      var e = allEvents[i];
      html += '<div class="logrow">' +
        '<span class="t">' + stamp(e.t) + "</span>" +
        '<span class="k">' + (KIND_CN[e.kind] || e.kind) + "</span>" +
        '<span class="d">' + (e.detail || "") + "</span></div>";
    }
    box.innerHTML = html;
    $("logCount").textContent = allEvents.length + " 条";
  }

  function mergeEvents(list) {
    if (!Array.isArray(list) || !list.length) { return; }
    var seen = {};
    for (var i = 0; i < allEvents.length; i++) { seen[allEvents[i].t + "|" + allEvents[i].kind] = true; }
    for (var j = 0; j < list.length; j++) {
      var e = list[j];
      var key = e.t + "|" + e.kind;
      if (!seen[key]) { seen[key] = true; allEvents.push(e); }
    }
    allEvents.sort(function (a, b) { return a.t - b.t; });
    if (allEvents.length > 500) { allEvents = allEvents.slice(-500); }
  }

  /* ---------------- 设置 ---------------- */

  function fillSettings(d) {
    if (view !== "settings" || !d) { return; }
    var s = $("setDesire");
    if (s && document.activeElement !== s) {
      s.value = d.desire;
      $("setDesireVal").textContent = d.desire;
    }
  }

  function doAction(name) {
    $("setMsg").textContent = "执行中…";
    apiPost("action", { action: name })
      .then(function (res) {
        var d = unwrap(res);
        $("setMsg").textContent = d && d.message ? d.message : "已执行";
        if (d) { apply(d); }
        pull();
      })
      .catch(function (err) {
        $("setMsg").textContent = "失败：" + (err && err.message ? err.message : err);
      });
  }

  /* ---------------- 视图切换 ---------------- */

  function switchView(name) {
    view = name;
    var items = document.querySelectorAll(".side .item");
    for (var i = 0; i < items.length; i++) {
      items[i].className = "item" + (items[i].getAttribute("data-view") === name ? " active" : "");
    }

    /* 只切换中栏内容，侧栏和右栏一直留在原地 */
    var panes = { overview: $("pane-overview"), events: $("pane-events"), settings: $("pane-settings") };
    for (var k in panes) {
      if (!panes.hasOwnProperty(k)) { continue; }
      var el = panes[k];
      if (el) { el.style.display = (k === name) ? "flex" : "none"; }
    }

    if (name === "events") { renderEvents(); }
    if (name === "settings" && last) { fillSettings(last); }
  }

  /* ---------------- 主循环 ---------------- */

  /* 档位显示名：优先接口给的 stageLabels，其次兜底，最后原文 */
  function stageName(d) {
    var s = (d && d.stage) || "";
    var m = (d && d.stageLabels) || {};
    return m[s] || STAGE_FALLBACK[s] || s || "--";
  }

  /* 状态显示名：优先接口给的 stateLabels，其次兜底，最后原文 */
  function stateName(d) {
    var s = (d && d.insertState) || "";
    var m = (d && d.stateLabels) || {};
    return m[s] || INSERT_FALLBACK[s] || s || "--";
  }

  /* 温度公式的两个参数跟着配置走 */
  function applyScales(d) {
    if (typeof d.tempBase === "number") { tempBase = d.tempBase; }
    if (typeof d.tempRise === "number" && d.tempRise > 0) { tempRise = d.tempRise; }
  }

  /* 把接口返回的 theme 写成 CSS 变量 */
  function applyTheme(d) {
    if (themeApplied || !d.theme || typeof d.theme !== "object") { return; }
    var root = document.documentElement;
    if (!root || !root.style) { return; }
    var map = {
      bg0: "--bg0", bg1: "--bg1", line: "--line", line2: "--line2",
      fg: "--fg", dim: "--dim", faint: "--faint",
      accent: "--purple", accent2: "--pink", accent3: "--cyan"
    };
    for (var k in map) {
      if (!Object.prototype.hasOwnProperty.call(map, k)) { continue; }
      var v = d.theme[k];
      if (v && typeof v === "string") {
        root.style.setProperty(map[k], v);
      }
    }
    themeApplied = true;
  }

  function apply(d) {
    if (!d) { return; }
    applyScales(d);
    applyTheme(d);
    last = d;
    mergeEvents(d.events);
    if (view === "overview") { renderOverview(d); }
    if (view === "events") { renderEvents(); }
    fillSettings(d);
  }

  function pull() {
    return apiGet(ENDPOINT)
      .then(function (res) { apply(unwrap(res)); })
      .catch(function () { $("feedState").style.color = "#ff6b6b"; });
  }

  function bind() {
    var items = document.querySelectorAll(".side .item");
    for (var i = 0; i < items.length; i++) {
      items[i].addEventListener("click", function () {
        switchView(this.getAttribute("data-view"));
      });
    }

    var slider = $("setDesire");
    if (slider) {
      slider.addEventListener("input", function () {
        $("setDesireVal").textContent = this.value;
      });
    }
    var btn = $("btnSetDesire");
    if (btn) {
      btn.addEventListener("click", function () {
        $("setMsg").textContent = "执行中…";
        apiPost("action", { action: "set_desire", value: Number($("setDesire").value) })
          .then(function (res) {
            var d = unwrap(res);
            $("setMsg").textContent = d && d.message ? d.message : "已设置";
            if (d) { apply(d); }
            pull();
          })
          .catch(function (err) {
            $("setMsg").textContent = "失败：" + (err && err.message ? err.message : err);
          });
      });
    }
    var actions = document.querySelectorAll("[data-action]");
    for (var k = 0; k < actions.length; k++) {
      actions[k].addEventListener("click", function () {
        doAction(this.getAttribute("data-action"));
      });
    }
  }

  bind();
  switchView("overview");
  pull();
  setInterval(pull, 2000);
})();
