"use strict";

const state = {
  sessions: [],
  goals: [],
  learningStatuses: [],
  currentSession: null,
  currentSources: [],
};

const el = Object.fromEntries([
  "errorBanner", "refreshSessionsBtn", "newSessionForm", "newSessionTitle", "sessionList",
  "currentSessionTitle", "rewriteStatus", "renameSessionBtn", "archiveSessionBtn", "messageList",
  "questionForm", "questionInput", "questionMode", "submitQuestionBtn", "sessionSettingsForm",
  "goalSelect", "learningStatusSelect", "sourceScopeSelect", "sourcePrefixInput", "memoryEnabled", "saveSessionSettingsBtn",
  "learningStatusForm", "manualStatus", "masteryLevel", "manualImportance", "currentProblem",
  "projectRelevance", "importanceDisplay", "saveLearningStatusBtn", "sourceCards", "linkNotesBtn",
  "generateReviewBtn", "reviewBox"
].map((id) => [id, document.getElementById(id)]));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function showError(message) {
  el.errorBanner.hidden = !message;
  el.errorBanner.textContent = message || "";
}

async function requestJson(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
    } catch (_) { /* response was not JSON */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function setBusy(button, busy) {
  button.disabled = busy || !state.currentSession;
}

function sourceTypesForScope(scope) {
  if (scope === "uploaded_document") return ["uploaded_document"];
  if (scope === "obsidian_note") return ["obsidian_note"];
  return ["obsidian_note", "uploaded_document"];
}

function scopeForSourceTypes(types) {
  if (types.length === 1 && types[0] === "uploaded_document") return "uploaded_document";
  if (types.length === 1 && types[0] === "obsidian_note") return "obsidian_note";
  return "all";
}

async function loadReferenceData() {
  const [goals, statuses] = await Promise.all([requestJson("/goals"), requestJson("/learning-status")]);
  state.goals = goals.items || [];
  state.learningStatuses = statuses.items || [];
  el.goalSelect.innerHTML = '<option value="">不关联目标</option>' + state.goals.map((item) =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}（优先级 ${item.priority}）</option>`
  ).join("");
  el.learningStatusSelect.innerHTML = '<option value="">不关联学习状态</option>' + state.learningStatuses.map((item) =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.topic)}（掌握 ${item.mastery_level}/5）</option>`
  ).join("");
}

async function loadSessions(selectId = null) {
  const data = await requestJson("/learning/sessions?status=active&limit=10");
  state.sessions = data.items || [];
  el.sessionList.innerHTML = state.sessions.length ? state.sessions.map((session) => `
    <button type="button" class="session-item ${state.currentSession?.id === session.id ? "active" : ""}" data-session-id="${escapeHtml(session.id)}">
      <strong>${escapeHtml(session.title)}</strong>
      <span>${new Date(session.last_activity_at).toLocaleString()} · ${escapeHtml(session.source_types.join(" + "))}</span>
    </button>`).join("") : '<p class="empty">还没有活动会话。</p>';
  const remembered = selectId || localStorage.getItem("learningSessionId");
  const candidate = state.sessions.find((item) => item.id === remembered);
  if (candidate) await selectSession(candidate.id);
  else if (!state.currentSession && state.sessions[0]) await selectSession(state.sessions[0].id);
}

async function createSession(event) {
  event.preventDefault();
  showError("");
  try {
    const session = await requestJson("/learning/sessions", {
      method: "POST",
      body: JSON.stringify({ title: el.newSessionTitle.value.trim() || "新学习会话" })
    });
    el.newSessionTitle.value = "新学习会话";
    await loadSessions(session.id);
  } catch (error) { showError(error.message); }
}

async function selectSession(sessionId) {
  showError("");
  const session = await requestJson(`/learning/sessions/${encodeURIComponent(sessionId)}`);
  state.currentSession = session;
  localStorage.setItem("learningSessionId", session.id);
  el.currentSessionTitle.textContent = session.title;
  el.goalSelect.value = session.goal_id || "";
  el.learningStatusSelect.value = session.learning_status_id || "";
  el.sourceScopeSelect.value = scopeForSourceTypes(session.source_types || []);
  el.sourcePrefixInput.value = (session.source_path_prefixes || []).join(", ");
  el.memoryEnabled.checked = session.memory_enabled !== false;
  [el.submitQuestionBtn, el.renameSessionBtn, el.archiveSessionBtn, el.saveSessionSettingsBtn, el.generateReviewBtn]
    .forEach((button) => { button.disabled = false; });
  renderSelectedLearningStatus();
  await Promise.all([loadMessages(), loadReview(false)]);
  renderSessionListSelection();
}

function renderSessionListSelection() {
  document.querySelectorAll(".session-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.sessionId === state.currentSession?.id);
  });
}

async function renameSession() {
  if (!state.currentSession) return;
  const title = window.prompt("新的会话标题", state.currentSession.title);
  if (title === null) return;
  try {
    state.currentSession = await requestJson(`/learning/sessions/${encodeURIComponent(state.currentSession.id)}`, {
      method: "PATCH", body: JSON.stringify({ title })
    });
    await loadSessions(state.currentSession.id);
  } catch (error) { showError(error.message); }
}

async function archiveSession() {
  if (!state.currentSession || !window.confirm("归档当前会话？问答历史不会被删除。")) return;
  try {
    await requestJson(`/learning/sessions/${encodeURIComponent(state.currentSession.id)}/archive`, { method: "POST" });
    localStorage.removeItem("learningSessionId");
    state.currentSession = null;
    state.currentSources = [];
    el.currentSessionTitle.textContent = "请选择或新建会话";
    el.messageList.innerHTML = '<p class="empty">当前会话已归档。</p>';
    renderSources([]);
    await loadSessions();
  } catch (error) { showError(error.message); }
}

function parsePrefixes() {
  return el.sourcePrefixInput.value.split(",").map((item) => item.trim()).filter(Boolean);
}

async function saveSessionSettings(event) {
  event.preventDefault();
  if (!state.currentSession) return;
  try {
    const sourceTypes = sourceTypesForScope(el.sourceScopeSelect.value);
    const prefixes = parsePrefixes();
    if (prefixes.length && !sourceTypes.includes("obsidian_note")) throw new Error("路径前缀只能用于 Obsidian 来源。");
    state.currentSession = await requestJson(`/learning/sessions/${encodeURIComponent(state.currentSession.id)}`, {
      method: "PATCH",
      body: JSON.stringify({
        goal_id: el.goalSelect.value || null,
        learning_status_id: el.learningStatusSelect.value || null,
        source_types: sourceTypes,
        source_path_prefixes: prefixes,
        memory_enabled: el.memoryEnabled.checked
      })
    });
    renderSelectedLearningStatus();
    el.rewriteStatus.textContent = "会话设置已保存；下次问答会使用这些显式选择。";
    await loadSessions(state.currentSession.id);
  } catch (error) { showError(error.message); }
}

function citationHtml(answer) {
  return escapeHtml(answer).replace(/\[来源(\d+)]/g, (_, number) =>
    `<a class="citation-link" data-citation-index="${number}">[来源${number}]</a>`
  );
}

async function loadMessages() {
  if (!state.currentSession) return;
  const data = await requestJson(`/learning/sessions/${encodeURIComponent(state.currentSession.id)}/messages`);
  const items = data.items || [];
  el.messageList.innerHTML = items.length ? items.map((item) => `
    <article class="message user"><div class="message-label">你的问题</div><div class="message-body">${escapeHtml(item.question)}</div></article>
    <article class="message assistant"><div class="message-label">知识库回答</div><div class="message-body">${citationHtml(item.answer)}</div></article>
  `).join("") : '<p class="empty">这个会话还没有问答记录。</p>';
  if (items.length) {
    state.currentSources = items[items.length - 1].source_chunks || [];
    renderSources(state.currentSources);
  } else renderSources([]);
  el.messageList.scrollTop = el.messageList.scrollHeight;
}

async function submitQuestion(event) {
  event.preventDefault();
  if (!state.currentSession) return;
  const question = el.questionInput.value.trim();
  if (!question) return;
  setBusy(el.submitQuestionBtn, true);
  showError("");
  try {
    const mode = el.questionMode.value;
    const response = await requestJson(`/rag/${mode === "ask" ? "ask" : "retrieve"}`, {
      method: "POST", body: JSON.stringify({ question, session_id: state.currentSession.id })
    });
    if (mode === "ask") {
      const memoryTurns = response.context_manifest?.session_memory?.turns || 0;
      const truncated = Object.values(response.context_manifest || {}).some((item) => item && item.truncated === true);
      el.rewriteStatus.textContent = `Run ${response.run_id || "-"} · 用户偏好已加载 · Session Memory ${memoryTurns} 轮 · Context ${truncated ? "已截断" : "未截断"}`;
      el.questionInput.value = "";
      await loadMessages();
      await loadSessions(state.currentSession.id);
    } else {
      const memoryTurns = response.context_manifest?.session_memory?.turns || 0;
      el.rewriteStatus.textContent = `仅检索完成 · Run ${response.run_id || "-"} · Memory ${memoryTurns} 轮 · Rewrite ${response.rewrite_applied ? "已触发" : "未触发"} · ${response.rewrite_reason || "-"}`;
      state.currentSources = response.chunks || [];
      renderSources(state.currentSources);
      el.messageList.insertAdjacentHTML("beforeend", `<article class="message user"><div class="message-label">仅检索</div><div class="message-body">${escapeHtml(question)}</div></article><article class="message assistant"><div class="message-body">检索到 ${state.currentSources.length} 个 Chunk；未生成答案，也未写问答历史。</div></article>`);
    }
  } catch (error) { showError(error.message); }
  finally { setBusy(el.submitQuestionBtn, false); }
}

function renderSources(sources) {
  state.currentSources = sources || [];
  const vaultName = document.body.dataset.obsidianVaultName || "";
  el.sourceCards.innerHTML = state.currentSources.length ? state.currentSources.map((source, index) => {
    const sourceIndex = index + 1;
    const isObsidian = source.source_type === "obsidian_note";
    const uri = isObsidian && vaultName && source.source_path
      ? `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(source.source_path)}` : "";
    return `<article id="source-${sourceIndex}" class="source-card" data-source-index="${sourceIndex}">
      <strong>[来源${sourceIndex}] ${escapeHtml(source.source_name || source.filename || "未命名来源")}</strong>
      <div class="source-meta">
        <span>类型：${escapeHtml(source.source_type || "uploaded_document")}</span>
        ${source.source_path ? `<span>相对路径：${escapeHtml(source.source_path)}</span>` : ""}
        <span>标题层级：${escapeHtml((source.heading_path || []).join(" › ") || source.section_title || "-")}</span>
        <span>行号：${escapeHtml(source.line_start ?? "-")}～${escapeHtml(source.line_end ?? "-")}</span>
        <span>Tags：${escapeHtml((source.tags || []).join(", ") || "-")}</span>
        <span>相似度：${escapeHtml(source.score ?? "-")} · Rerank：${escapeHtml(source.rerank_score ?? "-")}</span>
      </div>
      ${uri ? `<a class="button secondary" href="${escapeHtml(uri)}">在 Obsidian 中打开</a>` : ""}
      <div class="source-text">${escapeHtml(source.text || "")}</div>
    </article>`;
  }).join("") : '<p class="empty">完成问答或检索后显示 Source Card。</p>';
  updateManualButtons();
}

function focusCitation(index) {
  const card = document.getElementById(`source-${index}`);
  if (!card) return;
  document.querySelectorAll(".source-card").forEach((item) => item.classList.remove("highlight"));
  card.classList.add("highlight");
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function selectedLearningStatus() {
  return state.learningStatuses.find((item) => item.id === state.currentSession?.learning_status_id) || null;
}

function renderSelectedLearningStatus() {
  const item = selectedLearningStatus();
  const disabled = !item;
  el.saveLearningStatusBtn.disabled = disabled;
  el.linkNotesBtn.disabled = disabled || !state.currentSources.some((source) => source.source_type === "obsidian_note");
  if (!item) {
    el.learningStatusForm.reset();
    el.importanceDisplay.textContent = "重要指数：-（当前会话未关联学习状态）";
    return;
  }
  el.manualStatus.value = item.status;
  el.masteryLevel.value = item.mastery_level;
  el.manualImportance.value = item.manual_importance;
  el.currentProblem.value = item.current_problem || "";
  el.projectRelevance.checked = item.project_relevance;
  el.importanceDisplay.textContent = `重要指数：${item.importance_score}（${"★".repeat(item.importance_stars)}）`;
}

function updateManualButtons() {
  renderSelectedLearningStatus();
}

async function saveLearningStatus(event) {
  event.preventDefault();
  const item = selectedLearningStatus();
  if (!item) return;
  try {
    const updated = await requestJson(`/learning-status/${encodeURIComponent(item.id)}`, {
      method: "PATCH", body: JSON.stringify({
        status: el.manualStatus.value,
        mastery_level: Number(el.masteryLevel.value),
        manual_importance: Number(el.manualImportance.value),
        current_problem: el.currentProblem.value.trim() || null,
        project_relevance: el.projectRelevance.checked
      })
    });
    state.learningStatuses = state.learningStatuses.map((value) => value.id === updated.id ? updated : value);
    renderSelectedLearningStatus();
    el.rewriteStatus.textContent = "学习状态已通过显式 PATCH 保存。";
  } catch (error) { showError(error.message); }
}

async function linkCurrentNotes() {
  const item = selectedLearningStatus();
  if (!item) return;
  const refs = [...new Set(state.currentSources.filter((source) => source.source_type === "obsidian_note" && source.source_path).map((source) => source.source_path))];
  if (!refs.length) return;
  try {
    const updated = await requestJson(`/learning-status/${encodeURIComponent(item.id)}`, {
      method: "PATCH", body: JSON.stringify({ related_note_refs: [...new Set([...(item.related_note_refs || []), ...refs])] })
    });
    state.learningStatuses = state.learningStatuses.map((value) => value.id === updated.id ? updated : value);
    el.rewriteStatus.textContent = `已手动关联 ${refs.length} 个 Obsidian 相对路径；未修改 Markdown。`;
  } catch (error) { showError(error.message); }
}

function renderReview(review) {
  if (!review) {
    el.reviewBox.innerHTML = '<p class="empty">暂无复盘。</p>';
    return;
  }
  const list = (items) => `<ul>${(items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>无</li>"}</ul>`;
  el.reviewBox.innerHTML = `<h4>知识总结</h4><div class="message-body">${escapeHtml(review.knowledge_summary)}</div>
    <h4>已覆盖主题</h4>${list(review.covered_topics)}<h4>待解决问题</h4>${list(review.unresolved_questions)}
    <h4>下一步行动</h4>${list(review.next_steps)}`;
}

async function loadReview(showMissing = true) {
  if (!state.currentSession) return;
  try {
    renderReview(await requestJson(`/learning/sessions/${encodeURIComponent(state.currentSession.id)}/review`));
  } catch (error) {
    if (showMissing || !String(error.message).startsWith("Learning session review not found")) renderReview(null);
    else renderReview(null);
  }
}

async function generateReview() {
  if (!state.currentSession) return;
  const hasReview = !el.reviewBox.querySelector(".empty");
  if (hasReview && !window.confirm("再次生成会更新当前复盘内容，是否继续？")) return;
  setBusy(el.generateReviewBtn, true);
  try {
    const review = await requestJson(`/learning/sessions/${encodeURIComponent(state.currentSession.id)}/review`, { method: "POST" });
    renderReview(review);
  } catch (error) { showError(error.message); }
  finally { setBusy(el.generateReviewBtn, false); }
}

el.newSessionForm.addEventListener("submit", createSession);
el.refreshSessionsBtn.addEventListener("click", () => loadSessions(state.currentSession?.id).catch((error) => showError(error.message)));
el.sessionList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-session-id]");
  if (button) selectSession(button.dataset.sessionId).catch((error) => showError(error.message));
});
el.renameSessionBtn.addEventListener("click", renameSession);
el.archiveSessionBtn.addEventListener("click", archiveSession);
el.sessionSettingsForm.addEventListener("submit", saveSessionSettings);
el.learningStatusSelect.addEventListener("change", () => {
  if (state.currentSession) state.currentSession.learning_status_id = el.learningStatusSelect.value || null;
  renderSelectedLearningStatus();
});
el.questionForm.addEventListener("submit", submitQuestion);
el.messageList.addEventListener("click", (event) => {
  const link = event.target.closest("[data-citation-index]");
  if (link) focusCitation(link.dataset.citationIndex);
});
el.learningStatusForm.addEventListener("submit", saveLearningStatus);
el.linkNotesBtn.addEventListener("click", linkCurrentNotes);
el.generateReviewBtn.addEventListener("click", generateReview);

(async function initializeWorkspace() {
  try {
    await loadReferenceData();
    await loadSessions();
  } catch (error) { showError(error.message); }
})();
