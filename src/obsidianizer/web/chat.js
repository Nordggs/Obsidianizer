(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }

  let api = null;
  let contextRels = [];
  let chatFoundCards = [];
  let chatFoundVisible = 8;
  let chatFoundSelected = new Set();

  function copyText(text, btn) {
    function copied() {
      if (!btn) return;
      const prev = btn.textContent;
      btn.classList.add("copied");
      btn.textContent = "✓";
      setTimeout(function () {
        btn.classList.remove("copied");
        btn.textContent = prev;
      }, 1200);
    }
    function legacy() {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (_) {}
      document.body.removeChild(ta);
      copied();
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(copied, legacy);
    } else {
      legacy();
    }
  }

  function appendChatLine(who, text, cls) {
    const log = $("chatLog");
    const line = document.createElement("div");
    line.className = "chat-line" + (cls ? " " + cls : "");
    const whoEl = document.createElement("span");
    whoEl.className = "chat-who";
    whoEl.textContent = who;
    const textEl = document.createElement("span");
    textEl.className = "chat-text";
    textEl.textContent = text;
    const copyBtn = document.createElement("button");
    copyBtn.className = "chat-copy";
    copyBtn.textContent = "⧉";
    copyBtn.title = t("chat.copy");
    copyBtn.addEventListener("click", function () {
      copyText(text, copyBtn);
    });
    line.appendChild(whoEl);
    line.appendChild(textEl);
    line.appendChild(copyBtn);
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }

  function setChatBusy(value) {
    $("btnChatSend").disabled = value;
    $("chatInput").disabled = value;
    if (!value) $("chatInput").focus();
  }

  function renderChatContext() {
    const box = $("chatContext");
    box.textContent = "";
    contextRels.forEach(function (rel) {
      const chip = document.createElement("span");
      chip.className = "chat-chip";
      chip.textContent = rel;
      chip.title = t("chat.remove_context");
      chip.addEventListener("click", function () {
        api.set_chat_context(contextRels.filter(function (r) { return r !== rel; }))
          .then(function () { refreshContext(); });
      });
      box.appendChild(chip);
    });
  }

  function refreshContext() {
    api.chat_context().then(function (r) {
      contextRels = (r && r.ok !== false && r.rels) ? r.rels : [];
      renderChatContext();
    });
  }

  function sendChat() {
    if (!api) return;
    const text = $("chatInput").value.trim();
    if (!text) return;
    appendChatLine(t("chat.you"), text);
    $("chatInput").value = "";
    setChatBusy(true);
    api.chat_send({ message: text, context: contextRels }).then(function (r) {
      if (r && r.ok === false) {
        setChatBusy(false);
        appendChatLine(t("chat.system"), r.error || t("chat.send_failed"), "err");
        return;
      }
    });
  }

  function clearChat() {
    if (!api) return;
    api.chat_clear();
    $("chatLog").textContent = "";
    contextRels = [];
    renderChatContext();
    chatFoundCards = [];
    chatFoundSelected.clear();
    renderChatFound([]);
    appendChatLine(t("chat.system"), t("chat.cleared"), "dim");
  }

  function renderChatFound(cards) {
    chatFoundCards = cards || [];
    chatFoundSelected.clear();
    renderChatFoundList();
    if (!chatFoundCards.length) {
      $("chatFound").classList.add("hidden");
      return;
    }
    $("chatFoundTitle").textContent =
      t("chat.sources_n", { n: chatFoundCards.length }) +
      (chatFoundCards.some(function (c) { return c.partial; }) ? t("chat.partial") : "");
    $("chatFound").classList.remove("hidden");
  }

  function renderChatFoundList() {
    const list = $("chatFoundList");
    list.textContent = "";
    const visible = chatFoundCards.slice(0, chatFoundVisible);
    visible.forEach(function (card) {
      const row = document.createElement("div");
      row.className = "chat-found-item" +
        (chatFoundSelected.has(card.rel) ? " selected" : "");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = chatFoundSelected.has(card.rel);
      cb.addEventListener("change", function () {
        if (cb.checked) chatFoundSelected.add(card.rel);
        else chatFoundSelected.delete(card.rel);
        row.classList.toggle("selected", cb.checked);
        updateChatFoundThemeBtn();
      });
      const title = document.createElement("span");
      title.className = "chat-found-title";
      title.textContent = card.title || card.rel;
      title.title = card.rel + (card.score ? t("chat.relevance", { score: card.score }) : "");
      const rel = document.createElement("span");
      rel.className = "chat-found-rel dim";
      rel.textContent = card.rel;
      const openBtn = document.createElement("button");
      openBtn.className = "chat-found-open";
      openBtn.textContent = "↗";
      openBtn.title = t("chat.open_chat");
      openBtn.addEventListener("click", function () {
        if (api) api.open_note(card.rel);
      });
      row.appendChild(cb);
      row.appendChild(title);
      row.appendChild(rel);
      row.appendChild(openBtn);
      list.appendChild(row);
    });
    $("chatFoundMore").classList.toggle(
      "hidden", chatFoundCards.length <= chatFoundVisible);
    updateChatFoundThemeBtn();
  }

  function updateChatFoundThemeBtn() {
    $("btnChatFoundTheme").disabled = chatFoundSelected.size === 0;
    $("btnChatFoundTheme").textContent =
      t("chat.add_to_topic") + (chatFoundSelected.size ? " (" + chatFoundSelected.size + ")" : "");
  }

  function showMoreChatFound() {
    chatFoundVisible += 10;
    renderChatFoundList();
  }

  function chatFoundToTopic() {
    if (!chatFoundSelected.size) return;
    const rels = Array.from(chatFoundSelected);
    api.send_chat_topic_request(rels).then(function (r) {
      if (r && r.ok === false) {
        appendChatLine(t("chat.system"), r.error || t("chat.topic_failed"), "err");
        return;
      }
      chatFoundSelected.clear();
      renderChatFoundList();
      appendChatLine(t("chat.system"), t("chat.topic_opened"), "dim");
    });
  }

  window.pushEvent = function (ev) {
    const kind = ev.type;
    if (kind === "chat_reply") {
      appendChatLine("AI", ev.message || t("chat.empty_reply"), "ai");
      setChatBusy(false);
    } else if (kind === "chat_found") {
      let cards = [];
      try { cards = JSON.parse(ev.message || "[]") || []; } catch (e) { cards = []; }
      renderChatFound(cards);
    } else if (kind === "chat_error") {
      appendChatLine(t("chat.system"), ev.message || t("status.err_generic"), "err");
      setChatBusy(false);
    }
  };

  // The Python bridge calls this after ``set_chat_context`` / ``chat_clear``.
  window.chatContextChanged = function () {
    refreshContext();
  };

  function init() {
    api = window.pywebview.api;
    api.defaults().then(function (d) {
      window.initLang(d.lang_resolved === "en" ? "en" : "ru");
      window.applyI18n(document);
      $("chatModelLabel").textContent = d.chat_model || d.model || "";
    });
    api.chat_history().then(function (r) {
      if (!r || r.ok === false) return;
      const log = $("chatLog");
      log.textContent = "";
      (r.messages || []).forEach(function (m) {
        appendChatLine(m.role === "assistant" ? "AI" : t("chat.you"), m.content || "");
      });
      if (!(r.messages || []).length) {
        appendChatLine(t("chat.system"), t("chat.hello"), "dim");
      }
    });
    api.chat_found().then(function (r) {
      if (r && r.ok !== false && r.files) renderChatFound(r.files);
    });
    refreshContext();

    $("btnChatSend").addEventListener("click", sendChat);
    $("chatInput").addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChat();
      }
    });
    $("btnChatClear").addEventListener("click", clearChat);
    $("btnChatAttach").addEventListener("click", function () {
      if (api) api.send_chat_context_request();
    });
    $("btnChatFoundTheme").addEventListener("click", chatFoundToTopic);
    $("btnChatFoundMore").addEventListener("click", showMoreChatFound);
  }

  window.initLang("ru");
  window.applyI18n(document);

  if (window.pywebview) { init(); }
  else { window.addEventListener("pywebviewready", init); }
})();