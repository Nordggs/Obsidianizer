/* Obsidianizer GUI — bridge client. Pattern mirrors main.py's app.js. */

(function () {
  "use strict";

  let api = null;
  let busy = false;
  let total = 0;

  const $ = (id) => document.getElementById(id);

  // ── rendering ──────────────────────────────────────────────────────────

  function renderLog(msg, cls) {
    const el = $("log");
    const line = document.createElement("div");
    line.className = cls || "plain";
    line.textContent = msg;
    el.appendChild(line);
    while (el.children.length > 4000) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  }

  function log(msg) { renderLog(msg, "plain"); }

  function setStatus(text, cls) {
    const s = $("status");
    s.textContent = text;
    s.className = "status" + (cls ? " " + cls : "");
  }

  function setBusy(value) {
    busy = value;
    $("btnRun").disabled = value;
    $("btnStop").disabled = !value;
    $("btnSource").disabled = value;
    $("btnTarget").disabled = value;
    $("source").disabled = value;
    $("target").disabled = value;
    $("model").disabled = value;
    $("llm").disabled = value;
    $("dry").disabled = value;
    $("prune").disabled = value;
  }

  function setProgress(index) {
    const p = total > 0 ? Math.round((index / total) * 100) : 0;
    $("bar").style.width = p + "%";
    $("progText").textContent = index + " / " + total;
  }

  // ── events from the core (pushed by the Python bridge) ─────────────────

  window.pushEvent = function (ev) {
    const kind = ev.type;
    if (kind === "scan_started") {
      total = ev.total || 0;
      $("progressSection").classList.remove("hidden");
      setProgress(0);
      log("Сканирование… " + total + " файл(ов)");
    } else if (kind === "file_started") {
      $("currentFile").textContent = ev.path;
      setProgress(ev.index - 1);
    } else if (kind === "llm_started") {
      renderLog("⟳ " + ev.path + " — анализ Ollama…", "warn");
    } else if (kind === "file_done") {
      setProgress(ev.index);
      renderLog("✓ " + ev.path + "  (" + ev.index + "/" + ev.total + ")", "ok");
      $("currentFile").textContent = "";
    } else if (kind === "file_skipped") {
      renderLog("– пропущен (без изменений): " + ev.path, "dim");
    } else if (kind === "file_error") {
      renderLog("✗ " + ev.path + ": " + ev.message, "err");
    } else if (kind === "finished") {
      finish(ev);
    }
  };

  window.pushLog = function (msg) { log(msg); };

  function finish(ev) {
    setBusy(false);
    const m = ev.message || "";
    if (m.indexOf("отменено") === 0) {
      setStatus("Отменено: " + m, "warn");
      renderLog("■ " + m, "warn");
    } else if (m.indexOf("критическая ошибка") === 0) {
      setStatus("Критическая ошибка: " + m, "err");
      renderLog("■ " + m, "err");
    } else {
      setStatus("Готово: " + m, "ok");
      renderLog("■ " + m, "ok");
    }
  }

  // ── actions ────────────────────────────────────────────────────────────

  function pickFolder(kind) {
    const input = kind === "target" ? $("target") : $("source");
    if (!api) return;
    api.choose_folder().then(function (path) {
      if (!path) return;
      input.value = path;
      pushPaths();
    });
  }

  function pushPaths() {
    if (!api) return;
    api.set_paths($("source").value.trim(), $("target").value.trim()).then(function (r) {
      if (r && r.ok === false) {
        setStatus("Ошибка путей: " + r.error, "err");
      } else if (r && r.ok) {
        setStatus("Пути приняты. Нажмите «Обработать».", null);
      }
    });
  }

  function run() {
    if (!api) return;
    const source = $("source").value.trim();
    const target = $("target").value.trim();
    if (!source || !target) {
      setStatus("Укажите папки источника и результата.", "err");
      return;
    }
    api.set_paths(source, target).then(function (r) {
      if (r && r.ok === false) {
        setStatus("Ошибка путей: " + r.error, "err");
        return;
      }
      api.set_llm($("llm").checked, $("model").value.trim());
      $("progressSection").classList.add("hidden");
      $("currentFile").textContent = "";
      $("log").textContent = "";
      api.start_run({
        dry_run: $("dry").checked,
        prune: $("prune").checked,
      }).then(function (started) {
        if (started && started.ok === false) {
          setStatus("Не удалось запустить: " + started.error, "err");
          return;
        }
        setBusy(true);
        setStatus("Выполняется…", null);
      });
    });
  }

  function stop() {
    if (!api) return;
    api.cancel();
    setStatus("Остановка… текущий файл будет завершён", "warn");
  }

  function openTarget() {
    if (!api) return;
    api.open_target();
  }

  // ── init ───────────────────────────────────────────────────────────────

  function init() {
    api = window.pywebview.api;
    api.defaults().then(function (d) {
      $("version").textContent = "v" + d.version;
      $("source").value = d.source;
      $("target").value = d.target;
      $("model").value = d.model;
      $("llm").checked = !!d.llm_enabled;
    });

    $("btnSource").addEventListener("click", function () { pickFolder("source"); });
    $("btnTarget").addEventListener("click", function () { pickFolder("target"); });
    $("source").addEventListener("change", pushPaths);
    $("target").addEventListener("change", pushPaths);
    $("btnRun").addEventListener("click", run);
    $("btnStop").addEventListener("click", stop);
    $("btnOpen").addEventListener("click", openTarget);

    setStatus("Готово. Выберите папки и нажмите «Обработать».", null);
  }

  if (window.pywebview) { init(); }
  else { window.addEventListener("pywebviewready", init); }
})();