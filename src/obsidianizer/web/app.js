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
      log(t("status.scanning", { n: total }));
    } else if (kind === "file_started") {
      $("currentFile").textContent = ev.path;
      setProgress(ev.index - 1);
    } else if (kind === "llm_started") {
      renderLog(t("log.llm_started", { path: ev.path }), "warn");
    } else if (kind === "file_done") {
      setProgress(ev.index);
      renderLog(t("log.file_done", { path: ev.path, index: ev.index, total: ev.total }), "ok");
      $("currentFile").textContent = "";
    } else if (kind === "file_skipped") {
      renderLog(t("log.file_skipped", { path: ev.path }), "dim");
    } else if (kind === "file_error") {
      renderLog(t("log.file_error", { path: ev.path, message: ev.message }), "err");
    } else if (kind === "finished") {
      importMsg = ev.message || "";
      if (aiExpected) {
        renderLog(t("log.import_finished"), "ok");
      } else {
        finish(ev);
        importMsg = "";
      }
    } else if (kind === "ai_scan_started") {
      aiStarted = true;
      aiTotal = ev.total || 0;
      $("aiStageLabel").textContent = t("ai.post");
      $("aiProgressSection").classList.remove("hidden");
      setAiProgress(0);
      log(t("log.ai_scan", { n: aiTotal }));
    } else if (kind === "ai_file_started") {
      $("aiCurrentFile").textContent = ev.path;
      setAiProgress(ev.index - 1);
    } else if (kind === "ai_file_done") {
      setAiProgress(ev.index);
      renderLog(t("log.ai_file_done", { path: ev.path, index: ev.index, total: ev.total }), "ok");
      $("aiCurrentFile").textContent = "";
    } else if (kind === "ai_file_skipped") {
      renderLog(t("log.ai_file_skipped", { path: ev.path }), "dim");
    } else if (kind === "ai_file_error") {
      renderLog(t("log.ai_file_error", { path: ev.path, message: ev.message }), "err");
    } else if (kind === "ai_finished") {
      finishAi(ev);
    } else if (kind === "topic_map_started") {
      aiStarted = true;
      aiTotal = ev.total || 0;
      $("aiStageLabel").textContent = t("ai.group_all");
      $("aiProgressSection").classList.remove("hidden");
      setAiProgress(0);
      log(t("log.group_scan", { n: aiTotal }));
    } else if (kind === "topic_scan_started") {
      aiStarted = true;
      aiTotal = ev.total || 0;
      $("aiStageLabel").textContent = t("modal.tab_topic");
      $("aiProgressSection").classList.remove("hidden");
      setAiProgress(0);
      log(t("log.topic_scan", { n: aiTotal }));
    } else if (kind === "topic_file_started") {
      $("aiCurrentFile").textContent = ev.path;
      setAiProgress(ev.index - 1);
    } else if (kind === "topic_file_done") {
      setAiProgress(ev.index);
      renderLog(t("log.topic_file_done", { path: ev.path }), "ok");
      $("aiCurrentFile").textContent = "";
    } else if (kind === "topic_file_error") {
      renderLog(t("log.file_error", { path: ev.path, message: ev.message }), "err");
    } else if (kind === "topic_finished") {
      finishTopic(ev);
    } else if (kind === "obs_scan_started") {
      log(t("log.obs_scan", { n: ev.total }));
      setStatus(t("status.obs_running"), null);
    } else if (kind === "obs_folder_done") {
      const act = {
        created: t("log.obs_created"),
        updated: t("log.obs_updated"),
        skipped: t("log.obs_current"),
        conflict: t("log.obs_conflict"),
      }[ev.message] || ev.message;
      renderLog(
        act + ": " + ev.path + "  (" + ev.index + "/" + ev.total + ")",
        ev.message === "conflict" ? "warn"
          : (ev.message === "created" || ev.message === "updated") ? "ok" : "dim"
      );
    } else if (kind === "obs_finished") {
      let s = t("status.done");
      try {
        const m = JSON.parse(ev.message);
        s += t("status.obs_folders") + m.scanned + t("status.created") + m.created +
          t("status.updated") + m.updated + t("status.current") + m.skipped;
        if (m.conflicts && m.conflicts.length) s += t("status.conflicts_n") + m.conflicts.length;
      } catch (_) { s += ev.message; }
      setStatus(s, "ok");
      runObsScan();
    } else if (kind === "obs_error") {
      setStatus("Obsidianize: " + ev.message, "err");
      renderLog("✗ Obsidianize: " + ev.message, "err");
    } else if (kind === "review_started") {
      log(t("log.review_scan", { n: ev.total, message: ev.message }));
      setStatus(t("status.review_running"), null);
    } else if (kind === "review_folder_done") {
      if (ev.message === "ok") {
        renderLog(t("log.review_ok", { path: ev.path, index: ev.index, total: ev.total }), "ok");
      } else {
        renderLog(t("log.review_fail", { path: ev.path, index: ev.index, total: ev.total }), "err");
      }
    } else if (kind === "review_finished") {
      let s = t("status.done");
      try {
        const m = JSON.parse(ev.message);
        s += t("status.reviews") + m.ok + t("status.errors_eq") + m.errors;
      } catch (_) { s += ev.message; }
      setStatus(s, "ok");
      renderReviewFiles(ev.message);
    } else if (kind === "review_error") {
      setStatus(t("status.review_prefix") + ev.message, "err");
      renderLog("✗ " + t("status.review_prefix") + ev.message, "err");
    }
  };

  window.pushLog = function (msg) { log(msg); };

  function finishAi(ev) {
    setBusy(false);
    const m = ev.message || "";
    let cls = "ok";
    let status = t("status.done");
    if (m.indexOf("отменена") !== -1) {
      cls = "warn";
      status = t("status.cancelled");
    } else if (m.indexOf("критическая") !== -1) {
      cls = "err";
      status = t("status.critical");
    }
    const counts = m.match(/AI-обработано=(\d+), пропущено=(\d+), ошибок=(\d+)/);
    if (counts) {
      let summary = t("status.processed_n", { n: counts[1] }) + " · " +
        t("status.skipped_n", { n: counts[2] }) + " · " +
        t("status.errors_n", { n: counts[3] });
      const pruned = m.match(/удалено сирот=(\d+)/);
      if (pruned) summary += " · " + t("status.orphans_removed_n", { n: pruned[1] });
      if (importMsg) summary = t("status.import_x", { x: importMsg }) + " · " + summary;
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
      setStatus(t("status.cancelled") + m, "warn");
      renderLog("■ " + m, "warn");
    } else if (m.indexOf("критическая ошибка") === 0) {
      setStatus(t("status.critical") + m, "err");
      renderLog("■ " + m, "err");
    } else {
      setStatus(t("status.done") + m, "ok");
      renderLog("■ " + m, "ok");
    }
  }

  function finishTopic(ev) {
    setBusy(false);
    const m = ev.message || "";
    let cls = "ok";
    let status = t("status.done");
    if (m.indexOf("отменено") !== -1) {
      cls = "warn";
      status = t("status.cancelled");
    } else if (m.indexOf("Критическая ошибка") !== -1) {
      cls = "err";
      status = t("status.critical");
    } else if (m.indexOf("актуальна") !== -1) {
      cls = "dim";
      status = t("status.skipped_prefix");
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
        setStatus(t("status.paths_error") + r.error, "err");
      } else if (r && r.ok) {
        setStatus(t("status.paths_ok"), null);
      }
    });
  }

  function run() {
    if (!api) return;
    const source = $("source").value.trim();
    const target = $("target").value.trim();
    if (!source || !target) {
      setStatus(t("status.need_folders"), "err");
      return;
    }
    api.set_paths(source, target, $("enriched").value.trim()).then(function (r) {
      if (r && r.ok === false) {
        setStatus(t("status.paths_error") + r.error, "err");
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
          setStatus(t("status.start_failed") + started.error, "err");
          return;
        }
        setBusy(true);
        setStatus(t("status.running"), null);
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
        setStatus(t("status.start_failed") + started.error, "err");
        return;
      }
      setBusy(true);
      setStatus(t("status.ai_running"), null);
    });
  }

  function stop() {
    if (!api) return;
    api.cancel();
    setStatus(t("status.stopping"), "warn");
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
        setStatus(t("status.ollama_unavailable"), "warn");
        if (modelDropdownOpen) renderModelDropdown(t("status.ollama_short"));
        return;
      }
      const current = $("model").value.trim();
      const names = r.models || [];
      if (current && names.indexOf(current) === -1) names.unshift(current);
      modelNames = names;
      setStatus(t("status.models_updated", { n: names.length }), null);
      if (modelDropdownOpen) renderModelDropdown();
    });
  }

  function renderModelDropdown(emptyHint) {
    const box = $("modelDropdown");
    box.innerHTML = "";
    if (!modelNames.length) {
      const hint = document.createElement("div");
      hint.className = "drop-empty";
      hint.textContent = emptyHint || t("status.no_models");
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
      setStatus(t("status.prompt_reset"), null);
    });
  }

  function savePrompt() {
    if (!api) return;
    const value = $("promptText").value;
    api.set_prompt(promptKind, value).then(function (r) {
      if (r && r.ok === false) {
        setStatus(t("status.prompt_save_failed") + r.error, "err");
        return;
      }
      promptData[promptKind] = value;
      closePrompt();
      setStatus(t("status.prompt_saved"), "ok");
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
        setStatus(t("status.chats_list_failed") + ((r && r.error) || t("status.err_generic")), "err");
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
    $("topicModalHead").textContent = t("modal.topic_head");
    $("btnTopicCreate").textContent = t("modal.create_topic");
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    loadTopicChats(null);
  }

  function openTopicForUpdate(topic) {
    if (!api) return;
    topicUpdateId = topic.topic_id;
    chatContextMode = false;
    $("topicModalHead").textContent = t("modal.update_topic", { name: topic.name });
    $("btnTopicCreate").textContent = t("modal.update_topic_btn");
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    loadTopicChats(topic.chats || []);
  }

  function createTopicFromOrphans() {
    if (!api) return;
    topicUpdateId = null;
    chatContextMode = false;
    $("topicModalHead").textContent = t("modal.topic_head");
    $("btnTopicCreate").textContent = t("modal.create_topic");
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
        ? t("modal.no_matches")
        : t("modal.no_chats");
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
      if (f.messages && f.messages.total) meta.push(t("chat.msgs", { n: f.messages.total }));
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
    $("topicCount").textContent = t("status.selected") + n;
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
        setStatus(t("status.attached", { n: selected.length }), "ok");
      });
      return;
    }
    closeTopic();
    $("log").textContent = "";
    if (topicUpdateId) {
      api.update_topic({ topic_id: topicUpdateId, files: selected }).then(function (started) {
        if (started && started.ok === false) {
          setStatus(t("status.start_failed") + started.error, "err");
          return;
        }
        setBusy(true);
        setStatus(t("status.updating_topic"), null);
      });
      return;
    }
    api.create_topic({ files: selected }).then(function (started) {
      if (started && started.ok === false) {
        setStatus(t("status.start_failed") + started.error, "err");
        return;
      }
      setBusy(true);
      setStatus(t("status.topic_running"), null);
    });
  }

  function groupAll() {
    if (!api) return;
    $("aiProgressSection").classList.add("hidden");
    $("aiCurrentFile").textContent = "";
    $("log").textContent = "";
    api.group_all().then(function (started) {
      if (started && started.ok === false) {
        setStatus(t("status.start_failed") + started.error, "err");
        return;
      }
      setBusy(true);
      setStatus(t("status.group_running"), null);
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
        setStatus(t("status.topics_list_failed") + ((r && r.error) || t("status.err_generic")), "err");
        return;
      }
      renderTopicManageList(r.topics || []);
    });
    api.chats_without_topic().then(function (r) {
      const n = r && r.ok === false ? 0 : (r.files || []).length;
      $("topicOrphansCount").textContent = t("modal.orphans") + n;
    });
  }

  function renderTopicManageList(topics) {
    const box = $("topicManageList");
    box.textContent = "";
    if (!topics.length) {
      const hint = document.createElement("div");
      hint.className = "drop-empty";
      hint.textContent = t("modal.no_topics");
      box.appendChild(hint);
      return;
    }
    topics.forEach(function (topic) {
      const row = document.createElement("div");
      row.className = "topic-manage-item";
      const head = document.createElement("div");
      head.className = "topic-manage-head";
      const name = document.createElement("span");
      name.className = "topic-title";
      name.textContent = topic.name;
      const meta = document.createElement("span");
      meta.className = "topic-info dim";
      meta.textContent = " — " + t("modal.chats_n", { n: topic.chats.length }) + (topic.created ? ", " + topic.created : "");
      head.appendChild(name);
      head.appendChild(meta);
      const actions = document.createElement("div");
      actions.className = "topic-manage-actions";
      actions.appendChild(makeManageButton(t("modal.regenerate"), function () {
        closeTopicManage();
        openTopicForUpdate(topic);
      }));
      actions.appendChild(makeManageButton(t("modal.rename"), function () {
        renameTopicAction(topic);
      }));
      actions.appendChild(makeManageButton(t("modal.delete"), function () {
        deleteTopicAction(topic);
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
    const name = window.prompt(t("modal.new_topic_name"), topic.name);
    if (!name || name.trim() === "" || name === topic.name) return;
    api.rename_topic(topic.topic_id, name.trim()).then(function (r) {
      if (!r || r.ok === false) {
        setStatus(t("status.rename_failed") + ((r && r.error) || t("status.err_generic")), "err");
        return;
      }
      setStatus(t("status.renamed", { name: r.name }), "ok");
      refreshTopicManage();
    });
  }

  function deleteTopicAction(topic) {
    if (!window.confirm(t("modal.delete_confirm", { name: topic.name }))) return;
    api.delete_topic(topic.topic_id).then(function (r) {
      if (!r || r.ok === false) {
        setStatus(t("status.delete_failed") + ((r && r.error) || t("status.err_generic")), "err");
        return;
      }
      setStatus(t("status.topic_deleted"), "ok");
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
    ok: ["obs.st_ok", "s-ok"],
    stale: ["obs.st_stale", "s-stale"],
    missing: ["obs.st_missing", "s-missing"],
    conflict: ["obs.st_conflict", "s-conflict"],
  };

  // ── Tab help ("?" opens the floating help window) ───────────────────────

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

  let currentTab = "obsidianize";

  function renderObsScan(r) {
    const box = $("obsResult");
    $("obsResultSection").classList.remove("hidden");
    $("obsSplitter").classList.remove("hidden");
    applyObsResultHeight();
    if (!r || r.ok === false) {
      box.innerHTML = "";
      setStatus(((r && r.error) || t("status.scan_error")), "err");
      return;
    }
    if (!r.folders || r.folders.length === 0) {
      box.innerHTML = '<p class="dim"></p>';
      box.firstChild.textContent = t("obs.no_files");
      setStatus(t("status.obs_empty", { root: r.root }), "warn");
      return;
    }
    const rows = r.folders.map(function (f) {
      const c = f.categories || {};
      const st = OBS_STATUS[f.card] || ["obs.st_missing", "s-missing"];
      let changesHtml = "";
      if (f.card === "stale" && (f.changes || []).length) {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='8' class='dim'>" +
          "⚠ " + f.changes.map(escHtml).join("<br>⚠ ") +
          "</td></tr>";
      } else if (f.card === "stale") {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='8' class='dim'>⚠ " + escHtml(t("obs.has_changes")) + "</td></tr>";
      } else if (f.card === "conflict" && f.adoptable) {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='8' class='dim'>" +
          escHtml(t("obs.conflict_hint")) +
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
        "<td class='" + st[1] + "'>" + escHtml(t(st[0])) + "</td></tr>" + changesHtml;
    }).join("");
    box.innerHTML = "<table class='obs-table'><thead><tr>" +
      "<th>" + t("obs.th_folder") + "</th><th>" + t("obs.th_files") + "</th><th>📐</th><th>📊</th><th>📄</th><th>🖼️</th><th>📦</th><th>" + t("obs.th_subfolders") + "</th><th>" + t("obs.th_card") + "</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>";
    setStatus("Obsidianize: " + (r.summary || t("obs.n_folders", { n: r.folders.length })) + " · " + r.root, "ok");
  }

  function runObsScan() {
    if (!api) return;
    const path = $("obsDir").value.trim();
    if (!path) { setStatus(t("status.pick_scan_folder"), "err"); return; }
    api.obs_scan(path).then(renderObsScan);
  }

  let integrationRepairPending = false;

  function runIntegration() {
    if (!api) return;
    const vault = $("obsDir").value.trim();
    if (!vault) { setStatus(t("status.pick_vault"), "err"); return; }

    api.obs_integration_status({ vault: vault }).then(function (s) {
      if (!s || s.ok === false) {
        setStatus("Integration: " + ((s && s.error) || t("status.check_error")), "err");
        return;
      }
      if (!s.vault_found) {
        setStatus(t("status.not_vault"), "err");
        return;
      }
      if (!s.templater_installed) {
        setStatus(t("status.no_templater"), "warn");
        return;
      }
      if (!integrationRepairPending && s.template_installed) {
        integrationRepairPending = true;
        setStatus(t("status.repair_hint"), "warn");
        return;
      }

      api.obs_integration_install({ vault: vault, repair: integrationRepairPending }).then(function (r) {
        integrationRepairPending = false;
        if (!r || r.ok === false) {
          setStatus("Integration: " + ((r && r.error) || t("status.install_failed")), "err");
          return;
        }
        setStatus(t("status.integration_ok"), "ok");
        renderLog("✓ Integration: " + r.target, "ok");
        renderLog("  " + (r.hint || ""), "dim");
      });
    });
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
    if (!path) { setStatus(t("status.need_obs_folder"), "err"); return; }
    $("obsResultSection").classList.add("hidden");
    $("obsSplitter").classList.add("hidden");
    setStatus(t("status.obs_starting"), null);
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
        setStatus("Obsidianize: " + ((r && r.error) || t("status.run_failed")), "err");
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
      setStatus(((r && r.error) || t("status.scan_error_low")), "err");
      return;
    }
    if (!r.folders || r.folders.length === 0) {
      box.innerHTML = '<p class="dim"></p>';
      box.firstChild.textContent = t("ai.no_folders");
      return;
    }
    reviewSelection.clear();
    box.innerHTML = "";
    r.folders.forEach(function (f) {
      const label = document.createElement("label");
      label.className = "chat-item";
      label.innerHTML = "<input type='checkbox'> " +
        "<span class='chat-found-rel'>" + escHtml(f.rel || "·") + "</span>" +
        "<span class='dim'>" + escHtml(t("ai.files_n", { n: f.files }) + (f.card === "missing" ? t("ai.no_card") : "")) + "</span>";
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
    $("aiFolderCount").textContent = t("status.selected") + n;
  }

  function renderReviewFiles(message) {
    try {
      const m = JSON.parse(message);
      const box = $("aiReviewList");
      $("aiReviewSection").classList.remove("hidden");
      if (!m.files || m.files.length === 0) {
        box.innerHTML = '<p class="dim"></p>';
        box.firstChild.textContent = t("ai.no_reviews");
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
    if (!path) { setStatus(t("status.need_review_folder"), "err"); return; }
    api.obs_scan(path).then(renderReviewFolders);
  }

  function runReview() {
    if (!api) return;
    const path = $("aiDir").value.trim();
    if (!path) { setStatus(t("status.need_review_folder"), "err"); return; }
    const rels = [];
    $("aiFolderList").querySelectorAll("label").forEach(function (l) {
      if (l.querySelector("input").checked) {
        rels.push(l.querySelector(".chat-found-rel").textContent);
      }
    });
    if (rels.length === 0) { setStatus(t("status.pick_one_folder"), "err"); return; }
    $("aiReviewSection").classList.add("hidden");
    setStatus(t("status.review_starting", { n: rels.length }), null);
    api.review_run({
      path: path,
      rels: rels,
      include_text: $("aiIncludeText").checked,
    }).then(function (r) {
      if (!r || r.ok === false) {
        setStatus(t("status.review_prefix") + ((r && r.error) || t("status.run_failed")), "err");
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
    $("topicModalHead").textContent = t("modal.pick_context");
    $("btnTopicCreate").textContent = t("modal.add_context");
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
    $("topicModalHead").textContent = t("modal.topic_found_head");
    $("btnTopicCreate").textContent = t("modal.create_topic");
    resetTopicList();
    $("topicModal").classList.remove("hidden");
    loadTopicChats(rels);
    setStatus(t("status.found_n", { n: rels.length }), "ok");
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

  function updateLangButtons() {
    const lang = window.I18N_LANG;
    if ($("langRu")) $("langRu").classList.toggle("active", lang === "ru");
    if ($("langEn")) $("langEn").classList.toggle("active", lang === "en");
  }

  function switchLang(lang) {
    window.setLang(lang);
    updateLangButtons();
    if (api && api.set_language) api.set_language(lang);
  }

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
      window.initLang(d.lang_resolved === "en" ? "en" : "ru");
      window.applyI18n(document);
      updateLangButtons();
      toggleAiDetails();
    });

    $("langRu").addEventListener("click", function () { switchLang("ru"); });
    $("langEn").addEventListener("click", function () { switchLang("en"); });

    $("tabNavObs").addEventListener("click", function () { showTab("obsidianize"); });
    $("tabNavChat").addEventListener("click", function () { showTab("chat"); });
    $("tabNavAi").addEventListener("click", function () { showTab("ai"); });
    $("tabNavHelp").addEventListener("click", function () {
      api.open_help_window(currentTab);
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
    $("btnObsIntegration").addEventListener("click", function () {
      api.open_help_window("integration");
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
      if (e.key === "Escape") closeModelDropdown();
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

    setTimeout(fadeSplash, 1800);
    setStatus(t("status.ready"), null);
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