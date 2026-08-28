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
    const s = $("statusText");
    s.textContent = text;
    s.className = "status-text" + (cls ? " " + cls : "");
  }

  function setBusy(value) {
    busy = value;
    if (value) closeModelDropdown();
    $("btnRun").disabled = value;
    $("btnStop").disabled = !value;
    $("btnSource").disabled = value;
    $("btnTarget").disabled = value;
    $("btnEnriched").disabled = value;
    $("source").disabled = value;
    $("target").disabled = value;
    $("enriched").disabled = value;
    $("model").disabled = value;
    $("ai").disabled = value;
    $("btnAi").disabled = value;
    $("dry").disabled = value;
    $("prune").disabled = value;
    $("pruneAi").disabled = value;
  }

  function setProgress(index) {
    const p = total > 0 ? Math.round((index / total) * 100) : 0;
    $("bar").style.width = p + "%";
    $("progText").textContent = index + " / " + total;
  }

  let aiTotal = 0;

  function setAiProgress(index) {
    const p = aiTotal > 0 ? Math.round((index / aiTotal) * 100) : 0;
    $("aiBar").style.width = p + "%";
    $("aiProgText").textContent = index + " / " + aiTotal;
  }

  // ── events from the core (pushed by the Python bridge) ─────────────────

  let importMsg = "";
  let aiExpected = false;
  let aiStarted = false;

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
      importMsg = ev.message || "";
      if (aiExpected) {
        renderLog("✓ Импорт завершён", "ok");
      } else {
        finish(ev);
        importMsg = "";
      }
    } else if (kind === "ai_scan_started") {
      aiStarted = true;
      aiTotal = ev.total || 0;
      $("aiStageLabel").textContent = "AI-постобработка";
      $("aiProgressSection").classList.remove("hidden");
      setAiProgress(0);
      log("AI-постобработка: " + aiTotal + " файл(ов)");
    } else if (kind === "ai_file_started") {
      $("aiCurrentFile").textContent = ev.path;
      setAiProgress(ev.index - 1);
    } else if (kind === "ai_file_done") {
      setAiProgress(ev.index);
      renderLog("✓ AI " + ev.path + "  (" + ev.index + "/" + ev.total + ")", "ok");
      $("aiCurrentFile").textContent = "";
    } else if (kind === "ai_file_skipped") {
      renderLog("– AI пропущен (актуальный результат): " + ev.path, "dim");
    } else if (kind === "ai_file_error") {
      renderLog("✗ AI " + ev.path + ": " + ev.message, "err");
    } else if (kind === "ai_finished") {
      finishAi(ev);
    } else if (kind === "topic_map_started") {
      aiStarted = true;
      aiTotal = ev.total || 0;
      $("aiStageLabel").textContent = "Авто-группировка";
      $("aiProgressSection").classList.remove("hidden");
      setAiProgress(0);
      log("Авто-группировка: карта тем по " + aiTotal + " чат(ам)");
    } else if (kind === "topic_scan_started") {
      aiStarted = true;
      aiTotal = ev.total || 0;
      $("aiStageLabel").textContent = "Объединение в тему";
      $("aiProgressSection").classList.remove("hidden");
      setAiProgress(0);
      log("Объединение в тему: " + aiTotal + " файл(ов)");
    } else if (kind === "topic_file_started") {
      $("aiCurrentFile").textContent = ev.path;
      setAiProgress(ev.index - 1);
    } else if (kind === "topic_file_done") {
      setAiProgress(ev.index);
      renderLog("✓ собрано: " + ev.path, "ok");
      $("aiCurrentFile").textContent = "";
    } else if (kind === "topic_file_error") {
      renderLog("✗ " + ev.path + ": " + ev.message, "err");
    } else if (kind === "topic_finished") {
      finishTopic(ev);
    } else if (kind === "obs_scan_started") {
      log("Obsidianize: сканирование… " + ev.total + " папок");
      setStatus("Obsidianize: создание карточек…", null);
    } else if (kind === "obs_folder_done") {
      const act = {
        created: "✓ создана карточка",
        updated: "✓ обновлена карточка",
        skipped: "– карточка актуальна",
        conflict: "⚠ конфликт (чужой .md)",
      }[ev.message] || ev.message;
      renderLog(
        act + ": " + ev.path + "  (" + ev.index + "/" + ev.total + ")",
        ev.message === "conflict" ? "warn"
          : (ev.message === "created" || ev.message === "updated") ? "ok" : "dim"
      );
    } else if (kind === "obs_finished") {
      let s = "Готово: ";
      try {
        const m = JSON.parse(ev.message);
        s += "папок=" + m.scanned + ", создано=" + m.created + ", обновлено=" + m.updated +
          ", актуально=" + m.skipped;
        if (m.conflicts && m.conflicts.length) s += ", конфликтов=" + m.conflicts.length;
      } catch (_) { s += ev.message; }
      setStatus(s, "ok");
      runObsScan();
    } else if (kind === "obs_error") {
      setStatus("Obsidianize: " + ev.message, "err");
      renderLog("✗ Obsidianize: " + ev.message, "err");
    } else if (kind === "review_started") {
      log("AI-анализ: " + ev.total + " папок (" + ev.message + ")");
      setStatus("AI-анализ: формирование обзоров…", null);
    } else if (kind === "review_folder_done") {
      if (ev.message === "ok") {
        renderLog("✓ обзор сформирован: " + ev.path + "  (" + ev.index + "/" + ev.total + ")", "ok");
      } else {
        renderLog("✗ обзор не сформирован: " + ev.path + "  (" + ev.index + "/" + ev.total + ")", "err");
      }
    } else if (kind === "review_finished") {
      let s = "Готово: ";
      try {
        const m = JSON.parse(ev.message);
        s += "обзоров=" + m.ok + ", ошибок=" + m.errors;
      } catch (_) { s += ev.message; }
      setStatus(s, "ok");
      renderReviewFiles(ev.message);
    } else if (kind === "review_error") {
      setStatus("AI-анализ: " + ev.message, "err");
      renderLog("✗ AI-анализ: " + ev.message, "err");
    }
  };

  window.pushLog = function (msg) { log(msg); };

  function finishAi(ev) {
    setBusy(false);
    const m = ev.message || "";
    let cls = "ok";
    let status = "Готово: ";
    if (m.indexOf("отменена") !== -1) {
      cls = "warn";
      status = "Отменено: ";
    } else if (m.indexOf("критическая") !== -1) {
      cls = "err";
      status = "Критическая ошибка: ";
    }
    const counts = m.match(/AI-обработано=(\d+), пропущено=(\d+), ошибок=(\d+)/);
    if (counts) {
      let summary = "Обработано: " + counts[1] + " · Пропущено: " + counts[2] +
        " · Ошибок: " + counts[3];
      const pruned = m.match(/удалено сирот=(\d+)/);
      if (pruned) summary += " · Сирот удалено: " + pruned[1];
      if (importMsg) summary = "Импорт " + importMsg + " · " + summary;
      setStatus(status + summary, cls);
    } else {
      setStatus(status + m, cls);
    }
    renderLog("■ " + m, cls);
    importMsg = "";
    aiExpected = false;
    aiStarted = false;
    $("aiCurrentFile").textContent = "";
  }

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

  function finishTopic(ev) {
    setBusy(false);
    const m = ev.message || "";
    let cls = "ok";
    let status = "Готово: ";
    if (m.indexOf("отменено") !== -1) {
      cls = "warn";
      status = "Отменено: ";
    } else if (m.indexOf("Критическая ошибка") !== -1) {
      cls = "err";
      status = "Критическая ошибка: ";
    } else if (m.indexOf("актуальна") !== -1) {
      cls = "dim";
      status = "Пропущено: ";
    }
    setStatus(status + m, cls);
    renderLog("■ " + m, cls);
    $("aiCurrentFile").textContent = "";
  }

  // ── actions ────────────────────────────────────────────────────────────

  function pickFolder(kind) {
    const input = kind === "target" ? $("target") : kind === "enriched" ? $("enriched") : $("source");
    if (!api) return;
    api.choose_folder(kind).then(function (path) {
      if (!path) return;
      input.value = path;
      pushPaths();
    });
  }

  function pushFlags() {
    if (!api) return;
    api.set_flags($("dry").checked, $("prune").checked, $("pruneAi").checked);
  }

  function pushAi() {
    if (!api) return;
    api.set_llm($("ai").checked, $("model").value.trim(), $("chatModel").value.trim());
  }

  function toggleAiDetails() {
    const on = $("ai").checked;
    $("aiDetails").classList.toggle("hidden", !on);
  }

  function onAiToggle() {
    pushAi();
    toggleAiDetails();
  }

  function pushPaths() {
    if (!api) return;
    api.set_paths(
      $("source").value.trim(),
      $("target").value.trim(),
      $("enriched").value.trim()
    ).then(function (r) {
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
    api.set_paths(source, target, $("enriched").value.trim()).then(function (r) {
      if (r && r.ok === false) {
        setStatus("Ошибка путей: " + r.error, "err");
        return;
      }
      aiExpected = false;
      pushFlags();
      $("progressSection").classList.add("hidden");
      $("aiProgressSection").classList.add("hidden");
      $("currentFile").textContent = "";
      $("aiCurrentFile").textContent = "";
      $("log").textContent = "";
      api.start_run({
        dry_run: $("dry").checked,
        prune: $("prune").checked,
        prune_ai: $("pruneAi").checked,
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

  function runAi() {
    if (!api) return;
    aiExpected = true;
    $("aiProgressSection").classList.add("hidden");
    $("aiCurrentFile").textContent = "";
    $("log").textContent = "";
    api.set_llm($("ai").checked, $("model").value.trim(), $("chatModel").value.trim());
    pushFlags();
    api.start_ai({ prune_ai: $("pruneAi").checked }).then(function (started) {
      if (started && started.ok === false) {
        setStatus("Не удалось запустить: " + started.error, "err");
        return;
      }
      setBusy(true);
      setStatus("AI-постобработка…", null);
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

  function openEnriched() {
    if (!api) return;
    api.open_enriched();
  }

  // ── model list ─────────────────────────────────────────────────────────

  let modelNames = [];
  let modelDropdownOpen = false;

  function refreshModels() {
    if (!api) return;
    api.list_models().then(function (r) {
      if (!r || r.ok === false) {
        setStatus("Ollama недоступен — список моделей не обновлён", "warn");
        if (modelDropdownOpen) renderModelDropdown("Ollama недоступен");
        return;
      }
      const current = $("model").value.trim();
      const names = r.models || [];
      if (current && names.indexOf(current) === -1) names.unshift(current);
      modelNames = names;
      setStatus("Список моделей обновлён: " + names.length, null);
      if (modelDropdownOpen) renderModelDropdown();
    });
  }

  function renderModelDropdown(emptyHint) {
    const box = $("modelDropdown");
    box.innerHTML = "";
    if (!modelNames.length) {
      const hint = document.createElement("div");
      hint.className = "drop-empty";
      hint.textContent = emptyHint || "Модели не загружены. Нажмите «Обновить».";
      box.appendChild(hint);
      return;
    }
    modelNames.forEach(function (n) {
      const item = document.createElement("div");
      item.className = "drop-item";
      item.textContent = n;
      item.addEventListener("click", function () { selectModel(n); });
      box.appendChild(item);
    });
  }

  function toggleModelDropdown() {
    const box = $("modelDropdown");
    if (box.classList.contains("hidden")) {
      box.classList.remove("hidden");
      modelDropdownOpen = true;
      if (modelNames.length) {
        renderModelDropdown();
      } else {
        refreshModels();
      }
    } else {
      closeModelDropdown();
    }
  }

  function closeModelDropdown() {
    $("modelDropdown").classList.add("hidden");
    modelDropdownOpen = false;
  }

  function selectModel(name) {
    $("model").value = name;
    pushAi();
    closeModelDropdown();
  }

  // ── prompt editor ──────────────────────────────────────────────────────

  let promptKind = "ai_prompt";
  let promptData = null;

  function openPrompt() {
    if (!api) return;
    api.get_prompts().then(function (p) {
      if (!p) return;
      promptData = p;
      showPromptTab(promptKind);
      $("promptModal").classList.remove("hidden");
    });
  }

  function closePrompt() {
    $("promptModal").classList.add("hidden");
  }

  function showPromptTab(kind) {
    if (!promptData) return;
    promptKind = kind;
    const isAi = kind === "ai_prompt";
    const isTopic = kind === "topic_prompt";
    const isMap = kind === "map_prompt";
    $("tabImport").classList.toggle("active", !isAi && !isTopic && !isMap);
    $("tabAi").classList.toggle("active", isAi);
    $("tabTopic").classList.toggle("active", isTopic);
    $("tabMap").classList.toggle("active", isMap);
    $("promptText").value = isAi ? promptData.ai_prompt
      : isTopic ? promptData.topic_prompt
      : isMap ? promptData.map_prompt
      : promptData.prompt;
  }

  function resetPrompt() {
    if (!api) return;
    api.reset_prompt(promptKind).then(function (r) {
      if (!r || r.ok === false) return;
      promptData[promptKind] = r.value;
      $("promptText").value = r.value;
      setStatus("Промпт восстановлен к стандартному", null);
    });
  }

  function savePrompt() {
    if (!api) return;
    const value = $("promptText").value;
    api.set_prompt(promptKind, value).then(function (r) {
      if (r && r.ok === false) {
        setStatus("Не удалось сохранить промпт: " + r.error, "err");
        return;
      }
      promptData[promptKind] = value;
      closePrompt();
      setStatus("Промпт сохранён", "ok");
    });
  }

  // ── topic builder ──────────────────────────────────────────────────────

  let topicFiles = [];
  let topicFilterText = "";
  let topicUpdateId = null;
  let chatContextMode = false;

  function resetTopicList() {
    topicFilterText = "";
    $("topicFilter").value = "";
    $("topicList").textContent = "";
    renderTopicCount();
  }

  function loadTopicChats(preselect) {
    if (!api) return;
    api.list_chats().then(function (r) {
      if (!r || r.ok === false) {
        setStatus("Не удалось получить список чатов: " + ((r && r.error) || "ошибка"), "err");
        return;
      }
      topicFiles = r.files || [];
      if (preselect) {
        topicFiles.forEach(function (f) { f._selected = preselect.indexOf(f.rel) !== -1; });
      }
      renderTopicList();
    });
  }

  function openTopic() {
    if (!api) return;
    topicUpdateId = null;
    chatContextMode = false;
    $("topicModalHead").textContent = "Объединить чаты в тему";
    $("btnTopicCreate").textContent = "Создать справку";
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    loadTopicChats(null);
  }

  function openTopicForUpdate(topic) {
    if (!api) return;
    topicUpdateId = topic.topic_id;
    chatContextMode = false;
    $("topicModalHead").textContent = "Обновить тему: " + topic.name;
    $("btnTopicCreate").textContent = "Обновить справку";
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    loadTopicChats(topic.chats || []);
  }

  function createTopicFromOrphans() {
    if (!api) return;
    topicUpdateId = null;
    chatContextMode = false;
    $("topicModalHead").textContent = "Объединить чаты в тему";
    $("btnTopicCreate").textContent = "Создать справку";
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    api.chats_without_topic().then(function (r) {
      const rels = (r && r.files || []).map(function (f) { return f.rel; });
      loadTopicChats(rels);
    });
  }

  function closeTopic() {
    $("topicModal").classList.add("hidden");
  }

  function renderTopicList() {
    const box = $("topicList");
    box.textContent = "";
    const q = topicFilterText.toLowerCase();
    const visible = topicFiles.filter(function (f) {
      return !q ||
        (f.title || "").toLowerCase().indexOf(q) !== -1 ||
        (f.service || "").toLowerCase().indexOf(q) !== -1;
    });
    if (!visible.length) {
      const hint = document.createElement("div");
      hint.className = "drop-empty";
      hint.textContent = q
        ? "Ничего не найдено"
        : "Нет обработанных чатов. Сначала выполните импорт.";
      box.appendChild(hint);
      return;
    }
    visible.forEach(function (f) {
      const label = document.createElement("label");
      label.className = "topic-item";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!f._selected;
      cb.addEventListener("change", function () {
        f._selected = cb.checked;
        renderTopicCount();
      });
      const title = document.createElement("span");
      title.className = "topic-title";
      title.textContent = f.title || f.rel;
      const meta = [];
      if (f.service) meta.push(f.service);
      if (f.date) meta.push(f.date);
      if (f.messages && f.messages.total) meta.push(f.messages.total + " сообщ.");
      const info = document.createElement("span");
      info.className = "topic-info dim";
      info.textContent = meta.length ? " — " + meta.join(", ") : "";
      label.appendChild(cb);
      label.appendChild(title);
      label.appendChild(info);
      box.appendChild(label);
    });
  }

  function renderTopicCount() {
    const n = topicFiles.filter(function (f) { return f._selected; }).length;
    $("topicCount").textContent = "Выбрано: " + n;
    $("btnTopicCreate").disabled = n === 0;
  }

  function createTopic() {
    if (!api) return;
    const selected = topicFiles.filter(function (f) { return f._selected; })
      .map(function (f) { return f.rel; });
    if (!selected.length) return;
    if (chatContextMode) {
      closeTopic();
      api.set_chat_context(selected).then(function () {
        setStatus("Заметки прикреплены к чату: " + selected.length, "ok");
      });
      return;
    }
    closeTopic();
    $("log").textContent = "";
    if (topicUpdateId) {
      api.update_topic({ topic_id: topicUpdateId, files: selected }).then(function (started) {
        if (started && started.ok === false) {
          setStatus("Не удалось запустить: " + started.error, "err");
          return;
        }
        setBusy(true);
        setStatus("Обновление темы…", null);
      });
      return;
    }
    api.create_topic({ files: selected }).then(function (started) {
      if (started && started.ok === false) {
        setStatus("Не удалось запустить: " + started.error, "err");
        return;
      }
      setBusy(true);
      setStatus("Объединение в тему…", null);
    });
  }

  function groupAll() {
    if (!api) return;
    $("aiProgressSection").classList.add("hidden");
    $("aiCurrentFile").textContent = "";
    $("log").textContent = "";
    api.group_all().then(function (started) {
      if (started && started.ok === false) {
        setStatus("Не удалось запустить: " + started.error, "err");
        return;
      }
      setBusy(true);
      setStatus("Авто-группировка…", null);
    });
  }

  // ── topic management (list / rename / delete / orphans) ─────────────────

  function openTopicManage() {
    if (!api) return;
    $("topicManageModal").classList.remove("hidden");
    refreshTopicManage();
  }

  function closeTopicManage() {
    $("topicManageModal").classList.add("hidden");
  }

  function refreshTopicManage() {
    if (!api) return;
    api.list_topics().then(function (r) {
      if (!r || r.ok === false) {
        setStatus("Не удалось получить список тем: " + ((r && r.error) || "ошибка"), "err");
        return;
      }
      renderTopicManageList(r.topics || []);
    });
    api.chats_without_topic().then(function (r) {
      const n = r && r.ok === false ? 0 : (r.files || []).length;
      $("topicOrphansCount").textContent = "Чаты без темы: " + n;
    });
  }

  function renderTopicManageList(topics) {
    const box = $("topicManageList");
    box.textContent = "";
    if (!topics.length) {
      const hint = document.createElement("div");
      hint.className = "drop-empty";
      hint.textContent = "Тем ещё нет. Создайте первую через «Объединить в тему…».";
      box.appendChild(hint);
      return;
    }
    topics.forEach(function (t) {
      const row = document.createElement("div");
      row.className = "topic-manage-item";
      const head = document.createElement("div");
      head.className = "topic-manage-head";
      const name = document.createElement("span");
      name.className = "topic-title";
      name.textContent = t.name;
      const meta = document.createElement("span");
      meta.className = "topic-info dim";
      meta.textContent = " — " + t.chats.length + " чат(ов)" + (t.created ? ", " + t.created : "");
      head.appendChild(name);
      head.appendChild(meta);
      const actions = document.createElement("div");
      actions.className = "topic-manage-actions";
      actions.appendChild(makeManageButton("Перегенерировать", function () {
        closeTopicManage();
        openTopicForUpdate(t);
      }));
      actions.appendChild(makeManageButton("Переименовать", function () {
        renameTopicAction(t);
      }));
      actions.appendChild(makeManageButton("Удалить", function () {
        deleteTopicAction(t);
      }, true));
      row.appendChild(head);
      row.appendChild(actions);
      box.appendChild(row);
    });
  }

  function makeManageButton(label, handler, danger) {
    const btn = document.createElement("button");
    btn.className = "ghost" + (danger ? " danger" : "");
    btn.textContent = label;
    btn.addEventListener("click", handler);
    return btn;
  }

  function renameTopicAction(topic) {
    const name = window.prompt("Новое имя темы:", topic.name);
    if (!name || name.trim() === "" || name === topic.name) return;
    api.rename_topic(topic.topic_id, name.trim()).then(function (r) {
      if (!r || r.ok === false) {
        setStatus("Не удалось переименовать: " + ((r && r.error) || "ошибка"), "err");
        return;
      }
      setStatus("Тема переименована: " + r.name, "ok");
      refreshTopicManage();
    });
  }

  function deleteTopicAction(topic) {
    if (!window.confirm("Удалить тему «" + topic.name + "»? (чаты не удаляются)")) return;
    api.delete_topic(topic.topic_id).then(function (r) {
      if (!r || r.ok === false) {
        setStatus("Не удалось удалить: " + ((r && r.error) || "ошибка"), "err");
        return;
      }
      setStatus("Тема удалена", "ok");
      refreshTopicManage();
    });
  }

  // ── Folder Obsidianizer (tab 1) ────────────────────────────────────────

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  }

  const OBS_STATUS = {
    ok: ["? ok", "s-ok"],
    stale: ["~ требует обновления", "s-stale"],
    missing: ["- нет", "s-missing"],
    conflict: ["? конфликт", "s-conflict"],
  };

  // ── Tab help (context "?" button) ───────────────────────────────────────

  const TAB_ICONS = {
    obsidianize:
      '<svg class="tab-ico" viewBox="0 0 24 28" aria-hidden="true">' +
      '<path d="M12 1 L21 6.5 L21 15.2 L12 21 L3 15.2 L3 6.5 Z" fill="#8b5cf6"/>' +
      '<path d="M12 1 L21 6.5 L16 10.8 L7.2 7.6 Z" fill="#c4b5fd"/>' +
      '<path d="M7.2 7.6 L16 10.8 L12 21 Z" fill="#7c3aed"/>' +
      '<path d="M3 6.5 L7.2 7.6 L12 21 L3 15.2 Z" fill="#5b21b6"/>' +
      '<path d="M12 1 L7.2 7.6 L3 6.5 Z" fill="#ddd6fe"/></svg>',
    chat: "💬",
    ai: "🤖",
  };

  const TAB_HELP = {
    obsidianize: {
      title: "📁 → Справка: Obsidianize",
      body:
        "<p>Эта вкладка создаёт <b>карточки проектов</b> — заметку в стиле " +
        "GitHub для каждой папки: какие файлы лежат, что в подпапках, поля " +
        "проекта, галерея.</p>" +
        "<ul>" +
        "<li><b>Просканировать</b> — только смотрит и показывает таблицу: где " +
        "карточка ок, где устарела, где её ещё нет. <i>Ничего не пишет.</i></li>" +
        "<li><b>✨ Obsidianize</b> — создаёт и обновляет карточки. Сами ваши " +
        "файлы не изменяются.</li>" +
        "<li>Файл <b>&lt;папка&gt;_заметки.md</b> — ваша личная территория " +
        "(клиент, адрес, дизайнер, комментарии…). Программа создаёт его один " +
        "раз и <b>никогда не перезаписывает</b>.</li>" +
        "<li>Если в папке случайно появилась заметка от Obsidian (Ctrl+клик) — " +
        "без вашего разрешения она не тронется. Включите <b>«Принять " +
        "существующую заметку как заметки»</b>, и она станет файлом заметок, " +
        "а на её месте появится карточка.</li>" +
        "<li>Полоса-разделитель под таблицей тянется мышью: сотни карточек не " +
        "уведут результаты обработки за экран. Высота запоминается.</li>" +
        "<li>В Obsidian настройте хоткей «Obsidianizer Update» — одно нажатие " +
        "обновляет карточку той папки, в которой вы стоите.</li>" +
        "</ul>",
    },
    chat: {
      title: "💬 Справка: Чат-обработка",
      body:
        "<p>Конвейер из двух папок с необязательным AI-этапом:</p>" +
        "<ul>" +
        "<li><b>Источник</b> — сырые экспорты чатов. <b>Результат обработки</b> " +
        "(processed) — чистый импорт: метаданные, медиа, структура. " +
        "<b>AI-результат</b> (enriched) — обогащённое хранилище, <i>его и " +
        "открывайте в Obsidian</i>.</li>" +
        "<li><b>ОБРАБОТАТЬ</b> — чистый импорт: модель не вызывается, файлы " +
        "не изменяются в источнике.</li>" +
        "<li><b>AI-постобработка</b> — отдельный прогон модели по processed: " +
        "резюме, теги. Уже обработанные файлы не пересчитываются.</li>" +
        "<li><b>Объединить в тему…</b> — слить выбранные чаты в одну обзорную " +
        "заметку (enriched/topics). <b>Авто-группировка</b> — разобрать всю " +
        "коллекцию на темы автоматически.</li>" +
        "<li><b>Стоп</b> — корректная остановка: текущий файл завершится, " +
        "остальные пропустятся.</li>" +
        "<li><b>Только просмотр</b> — покажет план, ничего не записывая. " +
        "<b>Удалить старые результаты</b> — убирает только файлы, созданные " +
        "самой программой.</li>" +
        "</ul>",
    },
    ai: {
      title: "🤖 Справка: AI-анализ",
      body:
        "<p>Локальная модель читает содержимое выбранной папки и пишет " +
        "обзор — файл <b>&lt;папка&gt;_обзор.md</b> рядом с карточкой.</p>" +
        "<ul>" +
        "<li><b>Просканировать</b> — список папок с карточками; отметьте " +
        "нужные галочками.</li>" +
        "<li><b>Сформировать обзор</b> — модель анализирует каждую выбранную " +
        "папку и сохраняет обзор. Источники не изменяются.</li>" +
        "<li>Обзор <b>автоматически встраивается</b> в карточку папки (секция " +
        "AI Review) и появляется/исчезает вместе с файлом.</li>" +
        "<li><b>Включать содержимое текстовых файлов</b> — модель прочитает и " +
        "тексты (обзор подробнее, но дольше).</li>" +
        "<li>Модель — та же, что и для AI-постобработки (Ollama).</li>" +
        "</ul>",
    },
  };

  let currentTab = "obsidianize";

  function openHelp() {
    const h = TAB_HELP[currentTab] || TAB_HELP.obsidianize;
    $("helpModalHead").innerHTML =
      (TAB_ICONS[currentTab] || "") + " " + h.title;
    $("helpBody").innerHTML = h.body;
    $("helpModal").classList.remove("hidden");
  }

  function closeHelp() {
    $("helpModal").classList.add("hidden");
  }

  function renderObsScan(r) {
    const box = $("obsResult");
    $("obsResultSection").classList.remove("hidden");
    $("obsSplitter").classList.remove("hidden");
    applyObsResultHeight();
    if (!r || r.ok === false) {
      box.innerHTML = "";
      setStatus(((r && r.error) || "Ошибка сканирования"), "err");
      return;
    }
    if (!r.folders || r.folders.length === 0) {
      box.innerHTML = '<p class="dim">Файлов и подпапок не найдено.</p>';
      setStatus("Obsidianize: пусто (" + r.root + ")", "warn");
      return;
    }
    const rows = r.folders.map(function (f) {
      const c = f.categories || {};
      const st = OBS_STATUS[f.card] || ["—", "s-missing"];
      let changesHtml = "";
      if (f.card === "stale" && (f.changes || []).length) {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='8' class='dim'>" +
          "⚠ " + f.changes.map(escHtml).join("<br>⚠ ") +
          "</td></tr>";
      } else if (f.card === "stale") {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='8' class='dim'>⚠ есть изменения</td></tr>";
      } else if (f.card === "conflict" && f.adoptable) {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='8' class='dim'>" +
          "чужая заметка — отметь «Принять существующую заметку как заметки» и запусти Obsidianize" +
          "</td></tr>";
      }
      return "<tr><td class='mono'>" + escHtml(f.rel || "·") + "</td>" +
        "<td>" + f.files + "</td>" +
        "<td>" + (c.drafting || 0) + "</td>" +
        "<td>" + (c.tables || 0) + "</td>" +
        "<td>" + (c.docs || 0) + "</td>" +
        "<td>" + (c.images || 0) + "</td>" +
        "<td>" + (c.other || 0) + "</td>" +
        "<td>" + f.subfolders + "</td>" +
        "<td class='" + st[1] + "'>" + st[0] + "</td></tr>" + changesHtml;
    }).join("");
    box.innerHTML = "<table class='obs-table'><thead><tr>" +
      "<th>Папка</th><th>Файлов</th><th>📐</th><th>📊</th><th>📄</th><th>🖼️</th><th>📦</th><th>Подпапок</th><th>Карточка</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>";
    setStatus("Obsidianize: " + (r.summary || (r.folders.length + " папок")) + " · " + r.root, "ok");
  }

  function runObsScan() {
    if (!api) return;
    const path = $("obsDir").value.trim();
    if (!path) { setStatus("Выберите папку для сканирования", "err"); return; }
    api.obs_scan(path).then(renderObsScan);
  }

  function applyObsResultHeight() {
    const box = $("obsResult");
    if (!box) return;
    const saved = localStorage.getItem("obsResultH");
    if (saved) box.style.maxHeight = saved;
  }

  function initObsSplitter() {
    const sp = $("obsSplitter");
    if (!sp) return;
    sp.addEventListener("pointerdown", function (e) {
      const box = $("obsResult");
      if (!box || $("obsResultSection").classList.contains("hidden")) return;
      e.preventDefault();
      sp.setPointerCapture(e.pointerId);
      sp.classList.add("dragging");
      document.body.classList.add("dragging-splitter");
      sp._startY = e.clientY;
      sp._startH = box.getBoundingClientRect().height;
    });
    sp.addEventListener("pointermove", function (e) {
      if (!sp.classList.contains("dragging")) return;
      const box = $("obsResult");
      if (!box) return;
      // absolute cursor position relative to the table top — immune to
      // container scrolling / scroll anchoring while dragging
      const top = box.getBoundingClientRect().top;
      const max = Math.max(window.innerHeight * 0.8, 240);
      const h = Math.min(Math.max(e.clientY - top, 120), max);
      box.style.maxHeight = h + "px";
    });
    function endDrag() {
      if (!sp.classList.contains("dragging")) return;
      sp.classList.remove("dragging");
      document.body.classList.remove("dragging-splitter");
      const box = $("obsResult");
      if (box) {
        const h = box.getBoundingClientRect().height;
        if (h > 0) localStorage.setItem("obsResultH", Math.round(h) + "px");
      }
    }
    sp.addEventListener("pointerup", endDrag);
    sp.addEventListener("pointercancel", endDrag);
  }


  function runObs() {
    if (!api) return;
    const path = $("obsDir").value.trim();
    if (!path) { setStatus("Укажите папку для Obsidianize", "err"); return; }
    $("obsResultSection").classList.add("hidden");
    $("obsSplitter").classList.add("hidden");
    setStatus("Obsidianize: запуск…", null);
    api.obs_obsidianize({
      path: path,
      recursive: $("obsRecursive").checked,
      gallery: $("obsGallery").checked,
      adopt: $("obsAdopt").checked,
      vault_root: $("obsVaultRoot").value.trim(),
      gallery_prefix: $("obsGalleryPrefix").value.trim(),
      template: $("obsTemplate").value,
    }).then(function (r) {
      if (!r || r.ok === false) {
        setStatus("Obsidianize: " + ((r && r.error) || "не удалось запустить"), "err");
      }
    });
  }

  function pickFolderObs(kind) {
    if (!api) return;
    api.choose_folder(kind).then(function (path) {
      if (!path) return;
      if (kind === "vault_root") {
        $("obsVaultRoot").value = path;
        api.set_obsidianize_vault_root(path);
      } else {
        $("obsDir").value = path;
        api.set_obsidianize_dir(path);
      }
    });
  }

  function showTab(page) {
    currentTab = page;
    document.querySelectorAll(".tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.page === page);
    });
    document.querySelectorAll(".tab-page").forEach(function (p) {
      p.classList.toggle("active", p.id === "page-" + page);
    });
    const ico = $("tabHelpIcon");
    if (ico) ico.innerHTML = TAB_ICONS[page] || "";
  }

  // ── AI folder review (tab 3) ────────────────────────────────────────────

  const reviewSelection = new Map(); // rel -> folder entry (session state)

  function renderReviewFolders(r) {
    const box = $("aiFolderList");
    $("aiFoldersSection").classList.remove("hidden");
    if (!r || r.ok === false) {
      box.innerHTML = "";
      setStatus(((r && r.error) || "ошибка сканирования"), "err");
      return;
    }
    if (!r.folders || r.folders.length === 0) {
      box.innerHTML = '<p class="dim">Папок не найдено.</p>';
      return;
    }
    reviewSelection.clear();
    box.innerHTML = "";
    r.folders.forEach(function (f) {
      const label = document.createElement("label");
      label.className = "chat-item";
      label.innerHTML = "<input type='checkbox'> " +
        "<span class='chat-found-rel'>" + escHtml(f.rel || "·") + "</span>" +
        "<span class='dim'>" + f.files + " файл(ов)" + (f.card === "missing" ? " · карточки нет" : "") + "</span>";
      const cb = label.querySelector("input");
      cb.checked = true;
      cb.addEventListener("change", function () {
        updateReviewCount();
      });
      label.addEventListener("click", function (e) {
        if (e.target.tagName === "INPUT") return;
        cb.checked = !cb.checked;
        updateReviewCount();
      });
      reviewSelection.set(f.rel || "", f);
      box.appendChild(label);
    });
    updateReviewCount();
  }

  function updateReviewCount() {
    const labels = $("aiFolderList").querySelectorAll("label");
    let n = 0;
    labels.forEach(function (l) {
      if (l.querySelector("input").checked) n += 1;
    });
    $("aiFolderCount").textContent = "Выбрано: " + n;
  }

  function renderReviewFiles(message) {
    try {
      const m = JSON.parse(message);
      const box = $("aiReviewList");
      $("aiReviewSection").classList.remove("hidden");
      if (!m.files || m.files.length === 0) {
        box.innerHTML = '<p class="dim">Обзоры не сформированы.</p>';
        return;
      }
      box.innerHTML = m.files.map(function (f) {
        return "<div class='log-line ok'>✓ " + escHtml(f) + "</div>";
      }).join("");
    } catch (_) { /* ignore malformed summary */ }
  }

  function runReviewScan() {
    if (!api) return;
    const path = $("aiDir").value.trim();
    if (!path) { setStatus("Укажите папку для анализа", "err"); return; }
    api.obs_scan(path).then(renderReviewFolders);
  }

  function runReview() {
    if (!api) return;
    const path = $("aiDir").value.trim();
    if (!path) { setStatus("Укажите папку для анализа", "err"); return; }
    const rels = [];
    $("aiFolderList").querySelectorAll("label").forEach(function (l) {
      if (l.querySelector("input").checked) {
        rels.push(l.querySelector(".chat-found-rel").textContent);
      }
    });
    if (rels.length === 0) { setStatus("Выберите хотя бы одну папку", "err"); return; }
    $("aiReviewSection").classList.add("hidden");
    setStatus("AI-анализ: запуск (" + rels.length + " папок)…", null);
    api.review_run({
      path: path,
      rels: rels,
      include_text: $("aiIncludeText").checked,
    }).then(function (r) {
      if (!r || r.ok === false) {
        setStatus("AI-анализ: " + ((r && r.error) || "не удалось запустить"), "err");
      }
    });
  }

  function setReviewAll(checked) {
    $("aiFolderList").querySelectorAll("label input").forEach(function (cb) {
      cb.checked = checked;
    });
    updateReviewCount();
  }

  // ── AI assistant chat window (dedicated pywebview window) ─────────────────

  function openChatModal() {
    if (!api) return;
    api.open_chat_window();
  }

  // Note picker in chat-context mode — opened by the chat window via the
  // ``send_chat_context_request`` bridge. On confirm ``createTopic`` calls
  // ``api.set_chat_context`` back (state lives in the Python bridge).
  function openChatAttach() {
    if (!api) return;
    topicUpdateId = null;
    chatContextMode = true;
    $("topicModalHead").textContent = "Выберите заметки для контекста";
    $("btnTopicCreate").textContent = "Добавить в контекст";
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    api.chat_context().then(function (r) {
      loadTopicChats(r && r.ok !== false && r.rels ? r.rels : []);
    });
  }

  window.openChatAttach = openChatAttach;
  window.openTopicFromFound = openTopicFromFound;

  function openTopicFromFound(rels) {
    if (!api) return;
    topicUpdateId = null;
    chatContextMode = false;
    $("topicModalHead").textContent = "Объединить найденные чаты в тему";
    $("btnTopicCreate").textContent = "Создать справку";
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    loadTopicChats(rels);
    setStatus("Найдено " + rels.length + " чат(ов) — отметьте нужные и создайте тему", "ok");
  }

  // ── draggable modals ─────────────────────────────────────────────────────

  const modalPositions = new Map(); // session-only: last drag position per modal

  function makeDraggable(modalId) {
    const modal = $(modalId);
    const box = modal.querySelector(".modal-box");
    const head = box.querySelector(".modal-head");
    let dragging = null;

    // The box is ``position: relative`` inside a flex-centered parent, so
    // left/top are offsets from the flex position (0,0 = centered) — they
    // persist after the drag and never snap back to the center.
    function offsetTop() {
      return box.style.top ? (parseInt(box.style.top, 10) || 0) : 0;
    }

    head.addEventListener("pointerdown", function (e) {
      if (e.target.closest("button")) return; // never drag from buttons
      if (e.button !== 0) return;
      dragging = {
        startX: e.clientX,
        startY: e.clientY,
        left: box.style.left ? (parseInt(box.style.left, 10) || 0) : 0,
        top: offsetTop(),
      };
      box.classList.add("modal-dragging");
      try { head.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });

    head.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      box.style.left = (dragging.left + e.clientX - dragging.startX) + "px";
      box.style.top = (dragging.top + e.clientY - dragging.startY) + "px";
    });

    function stopDrag(e) {
      if (!dragging) return;
      box.classList.remove("modal-dragging");
      modalPositions.set(modalId, { left: box.style.left, top: box.style.top });
      dragging = null;
      try { head.releasePointerCapture(e.pointerId); } catch (_) {}
    }
    head.addEventListener("pointerup", stopDrag);
    head.addEventListener("pointercancel", stopDrag);

    // Restore the last dragged position whenever the modal becomes visible.
    new MutationObserver(function () {
      if (!modal.classList.contains("hidden")) {
        const pos = modalPositions.get(modalId);
        if (pos) {
          box.classList.add("modal-dragging");
          box.style.left = pos.left;
          box.style.top = pos.top;
        }
      }
    }).observe(modal, { attributes: true, attributeFilter: ["class"] });
  }

  // ── init ───────────────────────────────────────────────────────────────

  function init() {
    api = window.pywebview.api;
    api.defaults().then(function (d) {
      $("version").textContent = "v" + d.version;
      $("source").value = d.source;
      $("target").value = d.target;
      $("enriched").value = d.enriched || "";
      $("model").value = d.model;
      $("chatModel").value = d.chat_model || "";
      $("ai").checked = !!d.llm_enabled;
      $("dry").checked = !!d.dry_run;
      $("prune").checked = !!d.prune;
      $("pruneAi").checked = !!d.prune_enriched;
      $("obsDir").value = d.obsidianize_dir || "";
      $("obsVaultRoot").value = d.obsidianize_vault_root || "";
      $("obsGalleryPrefix").value = d.obsidianize_gallery_prefix || "";
      $("obsTemplate").value = d.obsidianize_template || "github";
      $("aiDir").value = d.obsidianize_dir || "";
      toggleAiDetails();
    });

    $("tabNavObs").addEventListener("click", function () { showTab("obsidianize"); });
    $("tabNavChat").addEventListener("click", function () { showTab("chat"); });
    $("tabNavAi").addEventListener("click", function () { showTab("ai"); });
    $("tabNavHelp").addEventListener("click", openHelp);
    $("btnHelpClose").addEventListener("click", closeHelp);
    $("btnHelpOk").addEventListener("click", closeHelp);
    $("helpModal").addEventListener("click", function (e) {
      if (e.target === $("helpModal")) closeHelp();
    });
    $("tabHelpIcon").innerHTML = TAB_ICONS.obsidianize;
    initObsSplitter();
    $("btnObsDir").addEventListener("click", function () { pickFolderObs("obsidianize"); });
    $("btnObsVaultRoot").addEventListener("click", function () { pickFolderObs("vault_root"); });
    $("obsDir").addEventListener("change", function () {
      api.set_obsidianize_dir($("obsDir").value.trim());
    });
    $("obsVaultRoot").addEventListener("change", function () {
      api.set_obsidianize_vault_root($("obsVaultRoot").value.trim());
    });
    $("obsGalleryPrefix").addEventListener("change", function () {
      api.set_obsidianize_gallery_prefix($("obsGalleryPrefix").value.trim());
    });
    $("obsTemplate").addEventListener("change", function () {
      api.set_obsidianize_template($("obsTemplate").value);
    });
    $("btnObsScan").addEventListener("click", runObsScan);
    $("btnObsRun").addEventListener("click", runObs);
    $("btnObsOpen").addEventListener("click", function () {
      api.obs_open_folder($("obsDir").value.trim());
    });
    $("btnAiDir").addEventListener("click", function () { pickFolderObs("obsidianize"); });
    $("btnAiScan").addEventListener("click", runReviewScan);
    $("btnAiRun").addEventListener("click", runReview);
    $("btnAiOpen").addEventListener("click", function () {
      api.obs_open_folder($("aiDir").value.trim());
    });
    $("btnAiCheckAll").addEventListener("click", function () { setReviewAll(true); });
    $("btnAiCheckNone").addEventListener("click", function () { setReviewAll(false); });
    $("aiDir").addEventListener("change", function () {
      api.set_obsidianize_dir($("aiDir").value.trim());
    });

    $("btnSource").addEventListener("click", function () { pickFolder("source"); });
    $("btnTarget").addEventListener("click", function () { pickFolder("target"); });
    $("btnEnriched").addEventListener("click", function () { pickFolder("enriched"); });
    $("source").addEventListener("change", pushPaths);
    $("target").addEventListener("change", pushPaths);
    $("enriched").addEventListener("change", pushPaths);
    $("ai").addEventListener("change", onAiToggle);
    $("model").addEventListener("change", pushAi);
    $("chatModel").addEventListener("change", pushAi);
    $("dry").addEventListener("change", pushFlags);
    $("prune").addEventListener("change", pushFlags);
    $("pruneAi").addEventListener("change", pushFlags);
    $("btnRun").addEventListener("click", run);
    $("btnStop").addEventListener("click", stop);
    $("btnAi").addEventListener("click", runAi);
    $("btnOpen").addEventListener("click", openTarget);
    $("btnOpenEnriched").addEventListener("click", openEnriched);
    $("btnRefreshModels").addEventListener("click", refreshModels);
    $("btnModelDropdown").addEventListener("click", toggleModelDropdown);
    document.addEventListener("click", function (e) {
      if (modelDropdownOpen && !e.target.closest(".model-wrap")) closeModelDropdown();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        closeModelDropdown();
        if (!$("helpModal").classList.contains("hidden")) closeHelp();
      }
    });
    $("btnPrompt").addEventListener("click", openPrompt);
    $("btnPromptClose").addEventListener("click", closePrompt);
    $("btnPromptCancel").addEventListener("click", closePrompt);
    $("btnPromptSave").addEventListener("click", savePrompt);
    $("btnPromptReset").addEventListener("click", resetPrompt);
    $("tabImport").addEventListener("click", function () { showPromptTab("prompt"); });
    $("tabAi").addEventListener("click", function () { showPromptTab("ai_prompt"); });
    $("tabTopic").addEventListener("click", function () { showPromptTab("topic_prompt"); });
    $("tabMap").addEventListener("click", function () { showPromptTab("map_prompt"); });
    $("promptModal").addEventListener("click", function (e) {
      if (e.target === $("promptModal")) closePrompt();
    });
    $("btnTopic").addEventListener("click", openTopic);
    $("btnTopics").addEventListener("click", openTopicManage);
    $("btnGroupAll").addEventListener("click", groupAll);
    $("btnTopicClose").addEventListener("click", closeTopic);
    $("btnTopicCancel").addEventListener("click", closeTopic);
    $("btnTopicCreate").addEventListener("click", createTopic);
    $("btnTopicManageClose").addEventListener("click", closeTopicManage);
    $("btnTopicManageClose2").addEventListener("click", closeTopicManage);
    $("btnTopicManageRefresh").addEventListener("click", refreshTopicManage);
    $("btnTopicOrphansCreate").addEventListener("click", createTopicFromOrphans);
    $("topicManageModal").addEventListener("click", function (e) {
      if (e.target === $("topicManageModal")) closeTopicManage();
    });
    $("topicFilter").addEventListener("input", function () {
      topicFilterText = $("topicFilter").value.trim();
      renderTopicList();
    });
    $("topicModal").addEventListener("click", function (e) {
      if (e.target === $("topicModal")) closeTopic();
    });
    $("btnChat").addEventListener("click", openChatModal);
    $("btnAi").disabled = false;

    makeDraggable("promptModal");
    makeDraggable("topicModal");
    makeDraggable("topicManageModal");
    makeDraggable("helpModal");

    setTimeout(fadeSplash, 1800);
    setStatus("Готово. Выберите папки и нажмите «Обработать».", null);
  }

  function fadeSplash() {
    const s = document.getElementById("splash");
    if (s && !s.classList.contains("fade-out")) {
      s.classList.add("fade-out");
      setTimeout(function () { s.style.display = "none"; }, 600);
    }
  }

  if (window.pywebview) { init(); }
  else { window.addEventListener("pywebviewready", init); }
})();