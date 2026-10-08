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
    const d = ev.data || {};
    const m = ev.message || "";
    let cls = "ok";
    let status = t("status.done");
    if (d.cancelled) {
      cls = "warn";
      status = t("status.cancelled");
    } else if (d.critical) {
      cls = "err";
      status = t("status.critical");
    }
    if (typeof d.processed === "number") {
      let summary = t("status.processed_n", { n: d.processed }) + " · " +
        t("status.skipped_n", { n: d.skipped }) + " · " +
        t("status.errors_n", { n: d.errors });
      if (d.pruned) summary += " · " + t("status.orphans_removed_n", { n: d.pruned });
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
    const d = ev.data || {};
    const m = ev.message || "";
    if (d.cancelled) {
      setStatus(t("status.cancelled") + m, "warn");
      renderLog("■ " + m, "warn");
    } else if (d.critical) {
      setStatus(t("status.critical") + m, "err");
      renderLog("■ " + m, "err");
    } else {
      setStatus(t("status.done") + m, "ok");
      renderLog("■ " + m, "ok");
    }
  }

  function finishTopic(ev) {
    setBusy(false);
    const d = ev.data || {};
    const m = ev.message || "";
    let cls = "ok";
    let status = t("status.done");
    const st = d.state || "";
    if (st === "cancelled") {
      cls = "warn";
      status = t("status.cancelled");
    } else if (st === "critical") {
      cls = "err";
      status = t("status.critical");
    } else if (st === "uptodate") {
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
    api.set_chat_only($("chatOnly").checked);
  }

  // ── Scan preview ────────────────────────────────────────────────────────

  function renderDetected(r) {
    const box = $("detectSummary");
    if (!box) return;
    const a = r.aggregate || {};
    const rows = [
      ["proc.count_chats", a.chats || 0],
      ["proc.count_documents", a.documents || 0],
      ["proc.count_images", a.images || 0],
      ["proc.count_cad", a.cad || 0],
      ["proc.count_tables", a.tables || 0],
      ["proc.count_folders", a.folders || 0],
    ];
    box.textContent = "";
    rows.forEach(function (pair) {
      const chip = document.createElement("span");
      chip.className = "detect-chip";
      chip.textContent = t(pair[0]) + ": " + pair[1];
      box.appendChild(chip);
    });
    const total = document.createElement("span");
    total.className = "detect-chip total";
    total.textContent = t("proc.count_total") + ": " + (a.total || 0);
    box.appendChild(total);
  }

  function doScan() {
    if (!api) return;
    // TICKET-011: persist exclusions first, so the preview reflects them.
    // TICKET-014: the scan path is real — every step lands in the log, so it
    // is always clear whether a scan actually happened.
    const srcPath = $("source").value.trim();
    renderLog(t("log.scan_started", { root: srcPath || "…" }));
    setStatus(t("status.scan_started", { root: srcPath || "…" }), null);
    api.set_exclusions($("excludeDirs").value.trim(), $("excludeMasks").value.trim())
      .then(function () { return api.scan(); })
      .then(function (r) {
        if (!r || r.ok === false) {
          const err = (r && r.error) || "";
          const shown = err || "—";
          renderLog(t("log.scan_error", { error: shown }), "err");
          setStatus(t("status.scan_error") + shown, "err");
          return;
        }
        renderDetected(r);
        var list = $("scanFileList");
        list.innerHTML = "";
        var files = r.files || [];
        files.forEach(function (f) {
          var lbl = document.createElement("label");
          var cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = !!f.is_chat;
          cb.dataset.path = f.path;
          cb.dataset.isChat = f.is_chat ? "1" : "0";
          lbl.appendChild(cb);
          var nameSpan = document.createElement("span");
          nameSpan.textContent = f.path;
          lbl.appendChild(nameSpan);
          if (f.service) {
            var tag = document.createElement("span");
            tag.className = "service-tag";
            tag.textContent = f.service;
            lbl.appendChild(tag);
          } else if (!f.is_chat) {
            var tag = document.createElement("span");
            tag.className = "service-tag";
            tag.textContent = t("chat.filter.non_chat_files");
            lbl.appendChild(tag);
          }
          list.appendChild(lbl);
        });
        $("scanPreview").classList.remove("hidden");
        updateScanSummary();
        const a = r.aggregate || {};
        const folders = a.folders || 0;
        const total = a.total || 0;
        const params = { total: total, folders: folders, files: total - folders, root: srcPath || "…" };
        renderLog(t("log.scan_done", params), "ok");
        setStatus(t("status.scan_done", params), "ok");
        renderDetTree(r);
        $("detFilesSplitter").classList.remove("hidden");
        $("detTreeSplitter").classList.remove("hidden");
        applySplitterHeight("scanFileList", "detFilesH");
        applySplitterHeight("detectedTree", "detTreeH");
      })
      .catch(function (err) {
        const msg = (err && err.message) || String(err || "");
        renderLog(t("log.scan_error", { error: msg }), "err");
        setStatus(t("status.scan_error") + msg, "err");
      });
  }

  function updateScanSummary() {
    var cbs = $("scanFileList").querySelectorAll("input[type=checkbox]");
    var total = cbs.length;
    var checked = 0;
    cbs.forEach(function (cb) { if (cb.checked) checked++; });
    $("scanSummary").textContent = checked + " / " + total;
  }

  function selectAllChats() {
    $("scanFileList").querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      if (cb.dataset.isChat === "1") cb.checked = true;
    });
    updateScanSummary();
  }

  function deselectAll() {
    $("scanFileList").querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      cb.checked = false;
    });
    updateScanSummary();
  }

  function getSelectedFiles() {
    var sel = [];
    var hasPreview = !$("scanPreview").classList.contains("hidden");
    if (hasPreview) {
      $("scanFileList").querySelectorAll("input[type=checkbox]:checked").forEach(function (cb) {
        sel.push(cb.dataset.path);
      });
    }
    return { selection: sel, selection_provided: hasPreview };
  }

  // ── Detected tree (TICKET-014, sel_tree for the Overview) ───────────────
  // Independent from #scanFileList (sel_files for import, frozen above):
  // own container #detectedTree, own state detTreeChecked, own buttons
  // detSelectAll/detDeselectAll/detInvert. Manual checks survive rescans
  // (merge rule: excluded wins is N/A here — the index already prunes
  // excluded objects, so every row defaults to checked on first sight).

  let detRows = [];         // flat rel list from the last scan payload
  let detTreeChecked = {};  // rel -> bool (survives rescans)
  let detTreeData = null;   // last nested tree NODE ({rel, kind, children[]})
  let detScannedRoot = "";  // r.root of the last successful scan (stale guard)

  // TICKET-014-PURE-START — DOM-free helpers, covered by tests/test_ticket014_js.py.
  // Kept free of $, t, document and api so the block can be eval'd under node.
  function detUnwrap(r) {
    // Backend payload shape: r.tree = {root, tree: node}. Returns the node;
    // a bare node is tolerated (unit tests, future DTOs).
    const w = r && r.tree;
    if (w && w.tree && typeof w.tree === "object") return w.tree;
    if (w && typeof w.rel === "string") return w;
    return null;
  }

  function detCollectRels(node) {
    const rels = [];
    (function walk(n) {
      if (!n) return;
      rels.push(n.rel || "");
      (n.children || []).forEach(walk);
    })(node);
    const seen = {};
    return rels.filter(function (rel) {
      if (seen[rel]) return false;
      seen[rel] = true;
      return true;
    });
  }

  function detBranchApply(rows, checked, rel, v) {
    // rel == "" addresses the whole branch (every rel) — the empty-prefix
    // bug of the tab-1 pattern is NOT copied here.
    rows.forEach(function (row) {
      if (row === rel || rel === "" || row.indexOf(rel + "/") === 0) {
        checked[row] = (v === "invert") ? !checked[row] : v;
      }
    });
    return checked;
  }
  // TICKET-014-PURE-END

  function detMergeScan(r) {
    const node = detUnwrap(r);
    const rels = detCollectRels(node);
    const seen = {};
    detRows = rels.filter(function (rel) {
      if (seen[rel]) return false;
      seen[rel] = true;
      return true;
    });
    detRows.forEach(function (rel) {
      if (detTreeChecked[rel] === undefined) detTreeChecked[rel] = true;
    });
    // drop checks of vanished rels
    Object.keys(detTreeChecked).forEach(function (k) {
      if (!seen[k]) delete detTreeChecked[k];
    });
    detTreeData = detUnwrap(r);
    detScannedRoot = normObsPath((r && r.root) || (r && r.tree && r.tree.root) || "");
  }

  function detCounts() {
    let sel = 0;
    detRows.forEach(function (rel) { if (detTreeChecked[rel]) sel++; });
    return { total: detRows.length, selected: sel };
  }

  function detUpdateSummary() {
    const c = detCounts();
    const el = $("detSummary");
    if (el) el.textContent = t("proc.det_selected", { n: c.selected }) + " / " + c.total;
  }

  function detSetAll(v) {
    detRows.forEach(function (rel) { detTreeChecked[rel] = v; });
    renderDetTree();
  }

  function detInvert() {
    detRows.forEach(function (rel) { detTreeChecked[rel] = !detTreeChecked[rel]; });
    renderDetTree();
  }

  function detBranch(rel, v) {
    detBranchApply(detRows, detTreeChecked, rel, v);
    renderDetTree();
  }

  function detAddToExcludes(rel) {
    if (!api || !rel) return;
    const base = rel.split("/").pop();
    const cur = $("excludeDirs").value.split(",").map(function (s) { return s.trim(); })
      .filter(function (s) { return s; });
    if (cur.indexOf(base) === -1) cur.push(base);
    $("excludeDirs").value = cur.join(", ");
    api.set_exclusions($("excludeDirs").value.trim(), $("excludeMasks").value.trim())
      .then(function () { doScan(); });
  }

  function detHideCtx() {
    const m = $("detCtx");
    if (m) m.classList.add("hidden");
  }

  function detShowCtx(x, y, rel) {
    let m = $("detCtx");
    if (!m) return;
    m.innerHTML = "";
    const items = [
      ["proc.det_select_all", function () { detSetAll(true); }],
      ["proc.det_deselect_all", function () { detSetAll(false); }],
      ["proc.det_invert", detInvert],
      ["proc.det_branch_select", function () { detBranch(rel, true); }],
      ["proc.det_branch_deselect", function () { detBranch(rel, false); }],
      ["proc.det_branch_invert", function () { detBranch(rel, "invert"); }],
      ["proc.det_add_to_excludes", function () { detAddToExcludes(rel); }],
    ];
    items.forEach(function (it) {
      const b = document.createElement("button");
      b.className = "pick";
      b.textContent = t(it[0]);
      b.addEventListener("click", function () { detHideCtx(); it[1](); });
      m.appendChild(b);
    });
    m.classList.remove("hidden");
    m.style.left = x + "px";
    m.style.top = y + "px";
  }

  function detMakeNode(node) {
    const rel = node.rel || "";
    const li = document.createElement("li");
    li.setAttribute("data-det-rel", rel);
    const isDir = node.kind === "folder";
    const kids = node.children || [];
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!detTreeChecked[rel];
    cb.setAttribute("data-det-check", rel);
    cb.addEventListener("change", function () {
      detTreeChecked[rel] = cb.checked;
      detUpdateSummary();
    });
    const nameSpan = document.createElement("span");
    nameSpan.className = "det-name";
    const short = rel === "" ? "·" : rel.split("/").pop();
    nameSpan.textContent = (isDir ? "📁 " : "📄 ") + short;
    if (node.file_type && node.file_type !== "folder") {
      const tag = document.createElement("span");
      tag.className = "service-tag";
      tag.textContent = node.file_type;
      nameSpan.appendChild(document.createTextNode(" "));
      nameSpan.appendChild(tag);
    }
    if (kids.length && isDir) {
      const det = document.createElement("details");
      det.open = true;
      const sum = document.createElement("summary");
      sum.appendChild(cb);
      sum.appendChild(nameSpan);
      det.appendChild(sum);
      const ul = document.createElement("ul");
      kids.forEach(function (k) { ul.appendChild(detMakeNode(k)); });
      det.appendChild(ul);
      li.appendChild(det);
    } else {
      const lbl = document.createElement("label");
      lbl.className = "det-leaf";
      lbl.appendChild(cb);
      lbl.appendChild(nameSpan);
      li.appendChild(lbl);
    }
    li.addEventListener("contextmenu", function (e) {
      e.preventDefault();
      // Nested <li> nodes bubble to their parents: stop here, otherwise the
      // root branch ("") wins and branch ops hit the whole tree.
      e.stopPropagation();
      detShowCtx(e.clientX, e.clientY, rel);
    });
    return li;
  }

  function renderDetTree(r) {
    const box = $("detectedTree");
    if (!box) return;
    if (r) detMergeScan(r);
    box.innerHTML = "";
    if (!detTreeData) {
      const p = document.createElement("p");
      p.className = "dim";
      p.textContent = t("proc.det_empty");
      box.appendChild(p);
      detUpdateSummary();
      return;
    }
    const ul = document.createElement("ul");
    ul.className = "det-root";
    ul.appendChild(detMakeNode(detTreeData));
    box.appendChild(ul);
    const ctx = document.createElement("div");
    ctx.id = "detCtx";
    ctx.className = "hidden";
    ctx.style.position = "fixed";
    ctx.style.zIndex = "50";
    box.appendChild(ctx);
    // Re-rendered on every scan/selection op — never stack the handler.
    document.removeEventListener("click", detHideCtx);
    document.addEventListener("click", detHideCtx);
    detUpdateSummary();
  }

  function detSelectionFresh(path) {
    return detRows.length > 0 && detScannedRoot !== "" &&
      detScannedRoot === normObsPath(path);
  }

  function getTreeSelection() {
    // Checked rels of sel_tree for the Overview builder. The scan root
    // ("") is reported explicitly so the backend can treat it as the
    // whole index; an empty tree means no scan ran yet.
    const sel = detRows.filter(function (rel) { return detTreeChecked[rel]; });
    return { selection: sel, selection_provided: detRows.length > 0 };
  }

  function pushAi() {
    if (!api) return;
    api.set_llm($("ai").checked, $("model").value.trim());
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
      var sel = getSelectedFiles();
      api.start_run({
        dry_run: $("dry").checked,
        prune: $("prune").checked,
        prune_ai: $("pruneAi").checked,
        selection: sel.selection,
        selection_provided: sel.selection_provided,
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
    api.set_llm($("ai").checked, $("model").value.trim());
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
    const tabs = {
      prompt: "tabImport",
      ai_prompt: "tabAi",
      topic_prompt: "tabTopic",
      map_prompt: "tabMap",
      folders_prompt: "tabFolders",
      chat_prompt: "tabChat",
    };
    Object.keys(tabs).forEach(function (k) {
      $(tabs[k]).classList.toggle("active", k === kind);
    });
    $("promptText").value = promptData[kind] || "";
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
    proc: "🧩",
  };

  let currentTab = "obsidianize";

  // ── Tab-1 selection (TICKET-013) ─────────────────────────────────────
  // Rule (config.yml exclusion) sets the default checkbox; the checked set
  // only decides which cards the current run generates (one-shot override —
  // checking an excluded dir never changes the rule). Manual checks for
  // surviving rels are kept across rescans.

  let obsRows = [];       // merged folders + excluded_dirs, deduped by rel
  let obsChecked = {};    // rel -> bool (survives rescans)
  let obsCollapsed = {};  // rel -> bool, hidden subtree (visibility only, never selection)
  let obsRules = null;
  let obsScannedRoot = ""; // r.root of the last successful scan (stale guard)

  const OBS_KIND_LABEL = {
    system: "obs.system_excluded",
    user: "obs.user_excluded",
    "hidden-only": "obs.hidden_excluded",
  };

  function obsDepth(rel) {
    if (!rel) return 0;
    return rel.split("/").length;
  }

  function obsMergeScan(r) {
    const byRel = {};
    (r.folders || []).forEach(function (f) {
      byRel[f.rel] = {
        rel: f.rel, data: f,
        excluded: !!f.excluded, kind: f.kind || null,
        inTree: true,
        checkedDefault: f.checked_default !== false,
      };
    });
    (r.excluded_dirs || []).forEach(function (e) {
      const rel = e.rel;
      if (byRel[rel]) {
        // in-tree excluded: excluded wins for the checkbox, but a strict
        // conflict marker is kept and shown next to the badge.
        byRel[rel].excluded = true;
        byRel[rel].kind = e.kind || byRel[rel].kind;
        byRel[rel].checkedDefault = false;
      } else {
        byRel[rel] = {
          rel: rel, data: null,
          excluded: true, kind: e.kind || "hidden-only",
          inTree: false,
          checkedDefault: false,
        };
      }
    });
    const rows = Object.keys(byRel).map(function (k) { return byRel[k]; });
    rows.sort(function (a, b) {
      if (a.rel === "") return -1;
      if (b.rel === "") return 1;
      return a.rel.localeCompare(b.rel);
    });
    rows.forEach(function (row) {
      if (obsChecked[row.rel] === undefined) obsChecked[row.rel] = row.checkedDefault;
    });
    // drop checks and collapse flags of vanished rels
    Object.keys(obsChecked).forEach(function (k) {
      if (!byRel[k]) delete obsChecked[k];
    });
    Object.keys(obsCollapsed).forEach(function (k) {
      if (!byRel[k]) delete obsCollapsed[k];
    });
    obsRows = rows;
    obsRules = r.rules || null;
    obsScannedRoot = normObsPath(r.root || "");
  }

  function normObsPath(p) {
    return String(p || "").trim().replace(/[\\/]+$/, "");
  }

  function obsSelectionFresh(path) {
    return obsRows.length > 0 && obsScannedRoot !== "" &&
      obsScannedRoot === normObsPath(path);
  }

  function obsCounts() {
    let sel = 0, exc = 0;
    obsRows.forEach(function (row) {
      if (row.excluded) exc++;
      if (obsChecked[row.rel]) sel++;
    });
    return { found: obsRows.length, selected: sel, excluded: exc };
  }

  function obsSetAll(v) {
    obsRows.forEach(function (row) { obsChecked[row.rel] = v; });
    renderObsTable();
  }

  function obsInvert() {
    obsRows.forEach(function (row) { obsChecked[row.rel] = !obsChecked[row.rel]; });
    renderObsTable();
  }

  let obsKidRels = {};  // rel -> true when it has row descendants (rebuilt per render)

  function obsRebuildKids() {
    const kids = {};
    obsRows.forEach(function (row) {
      if (!row.rel) return;
      kids[""] = true;
      let cur = row.rel;
      for (;;) {
        const i = cur.lastIndexOf("/");
        if (i < 0) break;
        cur = cur.slice(0, i);
        kids[cur] = true;
      }
    });
    obsKidRels = kids;
  }

  function obsIsHidden(rel) {
    if (!rel) return false;
    let cur = rel;
    for (;;) {
      const i = cur.lastIndexOf("/");
      cur = i < 0 ? "" : cur.slice(0, i);
      if (obsCollapsed[cur]) return true;
      if (!cur) return false;
    }
  }

  function obsCollapseAll(v) {
    if (!v) {
      obsCollapsed = {};
    } else {
      obsRows.forEach(function (row) {
        if (obsKidRels[row.rel]) obsCollapsed[row.rel] = true;
      });
    }
    renderObsTable();
  }

  function obsBranch(rel, v) {
    const prefix = rel ? rel + "/" : "";
    obsRows.forEach(function (row) {
      if (row.rel === rel || (prefix && row.rel.indexOf(prefix) === 0)) {
        obsChecked[row.rel] = (v === "invert") ? !obsChecked[row.rel] : v;
      }
    });
    renderObsTable();
  }

  function obsAddToExcludes(rel) {
    if (!api || !rel) return;
    const base = rel.split("/").pop();
    const dirs = ((obsRules && obsRules.user_dirs) || []).slice();
    if (dirs.indexOf(base) === -1) dirs.push(base);
    const masks = ((obsRules && obsRules.masks) || []).slice();
    $("excludeDirs").value = dirs.join(", ");
    $("excludeMasks").value = masks.join(", ");
    api.set_exclusions(dirs.join(", "), masks.join(", ")).then(function () {
      runObsScan();
    });
  }

  function obsRemoveByName(base) {
    if (!api || !base) return;
    const dirs = ((obsRules && obsRules.user_dirs) || []).filter(function (d) { return d !== base; });
    const masks = ((obsRules && obsRules.masks) || []).slice();
    // Sync the Source tab fields: doScan reads them and would bring back stale values.
    $("excludeDirs").value = dirs.join(", ");
    $("excludeMasks").value = masks.join(", ");
    api.set_exclusions(dirs.join(", "), masks.join(", ")).then(function () {
      runObsScan();
    });
  }

  function obsRemoveFromExcludes(rel) {
    if (!api || !rel) return;
    obsRemoveByName(rel.split("/").pop());
  }

  function obsKindOf(rel) {
    for (var i = 0; i < obsRows.length; i++) {
      if (obsRows[i].rel === rel) {
        return obsRows[i].excluded ? (obsRows[i].kind || "hidden-only") : null;
      }
    }
    return null;
  }

  function obsHideCtx() {
    const m = $("obsCtx");
    if (m) m.classList.add("hidden");
  }

  function obsShowCtx(x, y, rel) {
    let m = $("obsCtx");
    if (!m) return;
    m.innerHTML = "";
    const items = [
      ["obs.select_all", function () { obsSetAll(true); }],
      ["obs.deselect_all", function () { obsSetAll(false); }],
      ["obs.invert", obsInvert],
      ["obs.branch_select", function () { obsBranch(rel, true); }],
      ["obs.branch_deselect", function () { obsBranch(rel, false); }],
      ["obs.branch_invert", function () { obsBranch(rel, "invert"); }],
      ["obs.add_to_excludes", function () { obsAddToExcludes(rel); }],
    ];
    // Removal is offered only for user rules: system names can never be
    // removed (union), hidden-only rows carry no rule at all.
    if (rel && obsKindOf(rel) === "user") {
      items.push([
        { key: "obs.remove_from_excludes", name: rel.split("/").pop() },
        function () { obsRemoveFromExcludes(rel); },
      ]);
    }
    items.forEach(function (it) {
      const b = document.createElement("button");
      b.className = "pick";
      b.textContent = (typeof it[0] === "string") ? t(it[0]) : t(it[0].key, it[0]);
      b.addEventListener("click", function () { obsHideCtx(); it[1](); });
      m.appendChild(b);
    });
    m.classList.remove("hidden");
    m.style.left = x + "px";
    m.style.top = y + "px";
  }

  function obsBadge(kind) {
    if (!kind) return "";
    const key = OBS_KIND_LABEL[kind] || "obs.system_excluded";
    return " <span class='dim'>[" + escHtml(t(key)) + "]</span>";
  }

  function renderObsTable() {
    const box = $("obsResult");
    const c = obsCounts();
    obsRebuildKids();
    let html = "<div class='row' style='margin-bottom:6px'>" +
      "<span>" + escHtml(t("obs.counters", { found: c.found, selected: c.selected, excluded: c.excluded })) + "</span>" +
      "</div><div class='row' style='margin-bottom:6px'>" +
      "<button class='pick' id='obsSelAll'>" + escHtml(t("obs.select_all")) + "</button>" +
      "<button class='pick' id='obsSelNone'>" + escHtml(t("obs.deselect_all")) + "</button>" +
      "<button class='pick' id='obsSelInv'>" + escHtml(t("obs.invert")) + "</button>" +
      "<button class='pick' id='obsFoldAll'>" + escHtml(t("obs.fold_all")) + "</button>" +
      "<button class='pick' id='obsUnfoldAll'>" + escHtml(t("obs.unfold_all")) + "</button>" +
      "</div>";
    if (obsRules) {
      const userDirs = obsRules.user_dirs || [];
      const sysDirs = obsRules.system || [];
      let chips = "<div class='row obs-rules-row'><span class='dim'>" +
        escHtml(t("obs.rules_dirs")) + "</span>";
      if (userDirs.length === 0) {
        chips += "<span class='dim'>–</span>";
      }
      userDirs.forEach(function (d) {
        chips += "<button class='pick obs-chip' data-obs-unexclude='" + escHtml(d) +
          "' title='" + escHtml(t("obs.unexclude_title")) + "'>" +
          escHtml(d) + " ×</button>";
      });
      if (sysDirs.length) {
        chips += "<span class='dim'>" + escHtml(t("obs.rules_system") + ": " + sysDirs.join(", ")) + "</span>";
      }
      chips += "</div>";
      html += chips;
      html += "<p class='dim'>" + escHtml(t("obs.rules_masks", {
        masks: (obsRules.masks || []).join(", ") || "–",
      })) + "</p>";
    }
    const rows = obsRows.map(function (row) {
      const f = row.data || {};
      const cc = f.categories || {};
      const card = row.inTree ? (f.card || "missing") : "excluded";
      const st = OBS_STATUS[card] || ["obs.st_missing", "s-missing"];
      let statusHtml = escHtml(t(st[0]));
      if (card === "excluded") statusHtml = escHtml(t("obs.st_excluded"));
      let extra = "";
      if (row.inTree && f.card === "conflict") {
        extra = " ⚠ " + escHtml(t("obs.conflict_hint"));
      }
      let changesHtml = "";
      if (row.inTree && f.card === "stale" && (f.changes || []).length) {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='9' class='dim'>" +
          "⚠ " + f.changes.map(escHtml).join("<br>⚠ ") +
          "</td></tr>";
      } else if (row.inTree && f.card === "stale") {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='9' class='dim'>⚠ " + escHtml(t("obs.has_changes")) + "</td></tr>";
      } else if (row.inTree && f.card === "conflict" && f.adoptable) {
        changesHtml =
          "<tr class='obs-changes-row'><td></td><td colspan='9' class='dim'>" +
          escHtml(t("obs.conflict_hint")) +
          "</td></tr>";
      }
      const pad = row.rel ? obsDepth(row.rel) * 16 : 0;
      const files = row.inTree ? f.files : "—";
      const subs = row.inTree ? f.subfolders : "—";
      const checked = obsChecked[row.rel] ? " checked" : "";
      const kids = !!obsKidRels[row.rel];
      const collapsed = !!obsCollapsed[row.rel];
      const icon = kids ? (collapsed ? "📁 " : "📂 ") : "📁 ";
      const toggle = kids ?
        "<button class='obs-toggle' data-obs-toggle='" + escHtml(row.rel) + "'>" +
        (collapsed ? "▸" : "▾") + "</button>" : "";
      const hidden = obsIsHidden(row.rel) ? " style='display:none'" : "";
      return "<tr data-rel='" + escHtml(row.rel) + "'" + hidden + ">" +
        "<td>" + toggle + "</td><td><input type='checkbox' data-obs-check='" +
        escHtml(row.rel) + "'" + checked + "></td>" +
        "<td class='mono'><span style='display:inline-block;width:" + pad + "px'></span>" +
        icon + escHtml(row.rel || "·") + obsBadge(row.excluded ? row.kind : null) + "</td>" +
        "<td>" + files + "</td>" +
        "<td>" + (cc.drafting || 0) + "</td>" +
        "<td>" + (cc.tables || 0) + "</td>" +
        "<td>" + (cc.docs || 0) + "</td>" +
        "<td>" + (cc.images || 0) + "</td>" +
        "<td>" + (cc.other || 0) + "</td>" +
        "<td>" + subs + "</td>" +
        "<td class='" + st[1] + "'>" + statusHtml + extra + "</td></tr>" + changesHtml;
    }).join("");
    html += "<table class='obs-table'><thead><tr><th></th><th></th>" +
      "<th>" + t("obs.th_folder") + "</th><th>" + t("obs.th_files") + "</th><th>📐</th><th>📊</th><th>📄</th><th>🖼️</th><th>📦</th><th>" + t("obs.th_subfolders") + "</th><th>" + t("obs.th_card") + "</th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>";
    box.innerHTML = html + "<div id='obsCtx' class='hidden' style='position:fixed;z-index:50'></div>";
    // Delegated handlers (one per box, reassigned on every render — no
    // per-row listener buildup on large trees). Behaviour matches the
    // previous per-row bindings bit-for-bit.
    box.onchange = function (e) {
      const cb = e.target && e.target.closest ? e.target.closest("[data-obs-check]") : null;
      if (!cb || !box.contains(cb)) return;
      obsChecked[cb.getAttribute("data-obs-check")] = cb.checked;
      renderObsTable();
    };
    box.onclick = function (e) {
      const un = e.target && e.target.closest ? e.target.closest("[data-obs-unexclude]") : null;
      if (un && box.contains(un)) {
        obsRemoveByName(un.getAttribute("data-obs-unexclude"));
        return;
      }
      const tg = e.target && e.target.closest ? e.target.closest("[data-obs-toggle]") : null;
      if (tg && box.contains(tg)) {
        const trel = tg.getAttribute("data-obs-toggle");
        obsCollapsed[trel] = !obsCollapsed[trel];
        renderObsTable();
        return;
      }
      if (e.target && e.target.tagName === "INPUT") return;
      const tr = e.target && e.target.closest ? e.target.closest("tr[data-rel]") : null;
      if (!tr || !box.contains(tr)) return;
      box.querySelectorAll("tr[data-rel]").forEach(function (o) { o.classList.remove("sel"); });
      tr.classList.add("sel");
    };
    box.oncontextmenu = function (e) {
      const tr = e.target && e.target.closest ? e.target.closest("tr[data-rel]") : null;
      if (!tr || !box.contains(tr)) return;
      e.preventDefault();
      obsShowCtx(e.clientX, e.clientY, tr.getAttribute("data-rel"));
    };
    const bind = function (id, fn) { const b = $(id); if (b) b.addEventListener("click", fn); };
    bind("obsSelAll", function () { obsSetAll(true); });
    bind("obsSelNone", function () { obsSetAll(false); });
    bind("obsSelInv", obsInvert);
    bind("obsFoldAll", function () { obsCollapseAll(true); });
    bind("obsUnfoldAll", function () { obsCollapseAll(false); });
    document.removeEventListener("click", obsHideCtx);
    document.addEventListener("click", obsHideCtx);
    const cc2 = obsCounts();
    const runBtn = $("btnObsRun");
    if (runBtn) {
      runBtn.textContent = "✨ " + t("obs.run_n", { n: cc2.selected });
      runBtn.disabled = cc2.selected === 0;
      runBtn.title = cc2.selected === 0 ? t("obs.zero_selection") : t("obs.run_title");
    }
  }

  function renderObsScan(r) {
    obsBusyStop();
    const box = $("obsResult");
    $("obsResultSection").classList.remove("hidden");
    $("obsSplitter").classList.remove("hidden");
    applyObsResultHeight();
    if (!r || r.ok === false) {
      box.innerHTML = "";
      obsRows = []; obsChecked = {}; obsCollapsed = {}; obsRules = null; obsScannedRoot = "";
      setStatus(((r && r.error) || t("status.scan_error")), "err");
      return;
    }
    if (!r.folders || (r.folders.length === 0 && !(r.excluded_dirs || []).length)) {
      box.innerHTML = '<p class="dim"></p>';
      box.firstChild.textContent = t("obs.no_files");
      obsRows = []; obsChecked = {}; obsCollapsed = {}; obsRules = null; obsScannedRoot = "";
      setStatus(t("status.obs_empty", { root: r.root }), "warn");
      return;
    }
    obsMergeScan(r);
    renderObsTable();
    const cc = obsCounts();
    setStatus("Obsidianize: " + t("obs.counters", { found: cc.found, selected: cc.selected, excluded: cc.excluded }) + " · " + r.root, "ok");
  }

  // Busy indicator for the tab-1 scan: status animation + locked buttons
  // while obs_scan/render run. Stopped on every outcome (renderObsScan
  // stops it first thing; the catch below covers rejects/throws).
  let obsBusyTimer = null;

  function obsBusyStart(path) {
    obsBusyStop();
    const base = t("status.obs_scanning");
    setStatus(base, null);
    renderLog(base + " " + path);
    let dots = 0;
    obsBusyTimer = setInterval(function () {
      dots = (dots + 1) % 4;
      setStatus(base + ".".repeat(dots), null);
    }, 400);
    $("btnObsScan").disabled = true;
    const rb = $("btnObsRun");
    if (rb) rb.disabled = true;
  }

  function obsBusyStop() {
    if (obsBusyTimer) { clearInterval(obsBusyTimer); obsBusyTimer = null; }
    $("btnObsScan").disabled = false;
    // btnObsRun state is re-applied by renderObsTable on success; on
    // failure paths below the table never runs, so unlock explicitly.
    const rb = $("btnObsRun");
    if (rb) rb.disabled = false;
  }

  function runObsScan() {
    if (!api) return;
    const path = $("obsDir").value.trim();
    if (!path) { setStatus(t("status.pick_scan_folder"), "err"); return; }
    obsBusyStart(path);
    api.obs_scan(path).then(renderObsScan).catch(function (err) {
      obsBusyStop();
      const msg = t("status.scan_error") + ((err && err.message) || String(err || "") || "—");
      setStatus(msg, "err");
      renderLog(msg, "err");
    });
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
    applySplitterHeight("obsResult", "obsResultH");
  }

  // Generic vertical splitter: drag resizes boxId (maxHeight), the size
  // persists in localStorage under storageKey. opts: {splitterId, boxId,
  // sectionId, storageKey, minH}. Behaviour mirrors the original tab-1
  // splitter bit-for-bit.
  function initSplitter(opts) {
    const sp = $(opts.splitterId);
    if (!sp) return;
    const boxId = opts.boxId;
    const sectionId = opts.sectionId;
    const storageKey = opts.storageKey;
    const minH = opts.minH || 120;
    sp.addEventListener("pointerdown", function (e) {
      const box = $(boxId);
      const section = sectionId ? $(sectionId) : null;
      if (!box || (section && section.classList.contains("hidden"))) return;
      e.preventDefault();
      sp.setPointerCapture(e.pointerId);
      sp.classList.add("dragging");
      document.body.classList.add("dragging-splitter");
    });
    sp.addEventListener("pointermove", function (e) {
      if (!sp.classList.contains("dragging")) return;
      const box = $(boxId);
      if (!box) return;
      // absolute cursor position relative to the box top — immune to
      // container scrolling / scroll anchoring while dragging
      const top = box.getBoundingClientRect().top;
      const max = Math.max(window.innerHeight * 0.8, 240);
      const h = Math.min(Math.max(e.clientY - top, minH), max);
      box.style.maxHeight = h + "px";
    });
    function endDrag() {
      if (!sp.classList.contains("dragging")) return;
      sp.classList.remove("dragging");
      document.body.classList.remove("dragging-splitter");
      const box = $(boxId);
      if (box) {
        const h = box.getBoundingClientRect().height;
        if (h > 0) localStorage.setItem(storageKey, Math.round(h) + "px");
      }
    }
    sp.addEventListener("pointerup", endDrag);
    sp.addEventListener("pointercancel", endDrag);
  }

  function applySplitterHeight(boxId, storageKey) {
    const box = $(boxId);
    if (!box) return;
    const saved = localStorage.getItem(storageKey);
    if (saved) box.style.maxHeight = saved;
  }

  function initObsSplitter() {
    initSplitter({
      splitterId: "obsSplitter",
      boxId: "obsResult",
      sectionId: "obsResultSection",
      storageKey: "obsResultH",
    });
    initSplitter({
      splitterId: "detFilesSplitter",
      boxId: "scanFileList",
      sectionId: "sub-detected",
      storageKey: "detFilesH",
    });
    initSplitter({
      splitterId: "detTreeSplitter",
      boxId: "detectedTree",
      sectionId: "sub-detected",
      storageKey: "detTreeH",
    });
  }


  function runObs() {
    if (!api) return;
    const path = $("obsDir").value.trim();
    if (!path) { setStatus(t("status.need_obs_folder"), "err"); return; }
    // Stale guard: the checked set belongs to the last scanned root only.
    // A changed folder path without a rescan falls back to the legacy
    // full-tree run instead of silently applying foreign rels.
    const fresh = obsSelectionFresh(path);
    const selection = fresh ? obsRows.filter(function (row) { return obsChecked[row.rel]; })
      .map(function (row) { return row.rel; }) : [];
    if (fresh && selection.length === 0) {
      setStatus(t("obs.zero_selection"), "warn");
      return;
    }
    $("obsResultSection").classList.add("hidden");
    $("obsSplitter").classList.add("hidden");
    setStatus(t("status.obs_starting"), null);
    api.obs_obsidianize({
      path: path,
      recursive: $("obsRecursive").checked,
      gallery: $("obsGallery").checked,
      adopt: $("obsAdopt").checked,
      rebuild: $("obsRebuild").checked,
      vault_root: $("obsVaultRoot").value.trim(),
      gallery_prefix: $("obsGalleryPrefix").value.trim(),
      template: $("obsTemplate").value,
      selection: selection,
      selection_provided: fresh,
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

  function showSubTab(sub) {
    document.querySelectorAll(".subtab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.sub === sub);
    });
    document.querySelectorAll(".subtab-page").forEach(function (p) {
      p.classList.toggle("active", p.id === "sub-" + sub);
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
    // TICKET-006: filtered review folder list (exclusions applied);
    // frozen obs_scan stays for tab 1.
    const scan = (api.review_scan || api.obs_scan).bind(api);
    scan(path).then(renderReviewFolders);
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

  // ── Structural overview (TICKET-014, no LLM) ─────────────────────────────

  function runOverviewBuild() {
    if (!api) return;
    const box = $("overviewResult");
    const srcPath = $("source").value.trim();
    // Frontend stale-guard: the sel_tree choice belongs to the last scanned
    // root only (the backend re-checks with stale_scan regardless).
    if (!detSelectionFresh(srcPath)) {
      const stale = t("proc.overview_stale");
      setStatus(stale, "err");
      renderLog(stale, "err");
      return;
    }
    const sel = getTreeSelection();
    if (!sel.selection_provided) {
      setStatus(t("proc.overview_need_scan"), "err");
      return;
    }
    // An explicitly emptied tree falls back to the whole index (with a note);
    // the backend contract stays strict (empty + provided => empty_selection).
    const payload = (sel.selection.length === 0)
      ? { selection: [], selection_provided: false }
      : sel;
    if (sel.selection.length === 0) renderLog(t("proc.overview_no_selection"));
    setStatus(t("status.overview_started"), null);
    api.overview_build(payload).then(function (r) {
      if (!r || r.ok === false) {
        const err = (r && r.error) || "";
        const key = err === "no_scan" ? "proc.overview_need_scan"
          : err === "stale_scan" ? "proc.overview_stale" : null;
        const msg = key ? t(key) : t("proc.overview_failed", { error: err });
        setStatus(msg, "err");
        renderLog(msg, "err");
        return;
      }
      const done = t("proc.overview_done", { file: r.file });
      setStatus(done, "ok");
      renderLog("✓ " + done, "ok");
      if (box) {
        box.innerHTML = "";
        const line = document.createElement("div");
        line.className = "log-line ok";
        line.textContent = "✓ " + r.file + " · " + t("proc.det_selected", { n: (r.rels || []).length });
        box.appendChild(line);
      }
    }).catch(function (err) {
      const msg = t("proc.overview_failed", { error: (err && err.message) || String(err || "") });
      setStatus(msg, "err");
      renderLog(msg, "err");
    });
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

  // ── Update check (GitHub Releases) ──────────────────────────────────────

  var _lastUpdate = null;

  function checkUpdate() {
    if (!api || !api.check_update) return;
    api.check_update().then(function (res) {
      _lastUpdate = res;
      if (res && res.available) {
        var dot = $("updateDot");
        dot.classList.remove("hidden");
        dot.title = window.t("update.title") + ": v" + res.latest;
      }
    }).catch(function () {});
  }

  function showUpdateModal() {
    if (!_lastUpdate || !_lastUpdate.available) return;
    $("updCurrent").textContent = "v" + _lastUpdate.current;
    $("updLatest").textContent = _lastUpdate.latest;
    $("updateModal").classList.remove("hidden");
  }

  function closeUpdate() {
    $("updateModal").classList.add("hidden");
  }

  function openUpdateRelease() {
    if (_lastUpdate && _lastUpdate.url) api.open_external(_lastUpdate.url);
    closeUpdate();
  }

  function init() {
    api = window.pywebview.api;
    api.defaults().then(function (d) {
      $("version").textContent = "v" + d.version;
      $("source").value = d.source;
      $("target").value = d.target;
      $("enriched").value = d.enriched || "";
      $("model").value = d.model;
      $("excludeDirs").value = (d.exclude_dirs || []).join(", ");
      $("excludeMasks").value = (d.exclude_masks || []).join(", ");
      $("aiReviewModel").textContent = d.model || "–";
      $("ai").checked = !!d.llm_enabled;
      $("dry").checked = !!d.dry_run;
      $("prune").checked = !!d.prune;
      $("pruneAi").checked = !!d.prune_enriched;
      $("chatOnly").checked = d.chat_only !== false;
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
    $("tabNavProc").addEventListener("click", function () { showTab("proc"); });
    document.querySelectorAll(".subtab").forEach(function (t) {
      t.addEventListener("click", function () { showSubTab(t.dataset.sub); });
    });
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
    $("dry").addEventListener("change", pushFlags);
    $("prune").addEventListener("change", pushFlags);
    $("pruneAi").addEventListener("change", pushFlags);
    $("chatOnly").addEventListener("change", pushFlags);
    $("btnRun").addEventListener("click", run);
    $("btnStop").addEventListener("click", stop);
    $("btnAi").addEventListener("click", runAi);
    $("btnOpen").addEventListener("click", openTarget);
    $("btnOpenEnriched").addEventListener("click", openEnriched);
    $("btnScan").addEventListener("click", doScan);
    $("selectAllChats").addEventListener("click", selectAllChats);
    $("deselectAll").addEventListener("click", deselectAll);
    $("scanFileList").addEventListener("change", updateScanSummary);
    // TICKET-014: sel_tree controls (independent from the import selection).
    $("detSelectAll").addEventListener("click", function () { detSetAll(true); });
    $("detDeselectAll").addEventListener("click", function () { detSetAll(false); });
    $("detInvert").addEventListener("click", detInvert);
    $("btnOverviewBuild").addEventListener("click", runOverviewBuild);
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
    $("tabFolders").addEventListener("click", function () { showPromptTab("folders_prompt"); });
    $("tabChat").addEventListener("click", function () { showPromptTab("chat_prompt"); });
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

    $("updateDot").addEventListener("click", showUpdateModal);
    $("btnUpdateClose").addEventListener("click", closeUpdate);
    $("btnUpdateSkip").addEventListener("click", closeUpdate);
    $("btnUpdateOpen").addEventListener("click", openUpdateRelease);
    $("updateModal").addEventListener("click", function (e) {
      if (e.target === $("updateModal")) closeUpdate();
    });

    makeDraggable("promptModal");
    makeDraggable("topicModal");
    makeDraggable("topicManageModal");
    makeDraggable("updateModal");

    setTimeout(checkUpdate, 2000);
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