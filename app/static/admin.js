const state = {
  selectedDocumentId: null,
  selectedDocumentStatus: null,
  rerankEnabled: false,
  goals: [],
  learningStatuses: [],
};

const elements = {
  healthBadge: document.getElementById("healthBadge"),
  healthText: document.getElementById("healthText"),
  rerankEnabledText: document.getElementById("rerankEnabledText"),
  rerankModelText: document.getElementById("rerankModelText"),
  rerankTopNText: document.getElementById("rerankTopNText"),
  rerankHintText: document.getElementById("rerankHintText"),
  goalForm: document.getElementById("goalForm"),
  goalIdInput: document.getElementById("goalIdInput"),
  goalNameInput: document.getElementById("goalNameInput"),
  goalTypeSelect: document.getElementById("goalTypeSelect"),
  goalDescriptionInput: document.getElementById("goalDescriptionInput"),
  goalPriorityInput: document.getElementById("goalPriorityInput"),
  goalStatusSelect: document.getElementById("goalStatusSelect"),
  goalTopicsInput: document.getElementById("goalTopicsInput"),
  saveGoalBtn: document.getElementById("saveGoalBtn"),
  cancelGoalEditBtn: document.getElementById("cancelGoalEditBtn"),
  refreshGoalsBtn: document.getElementById("refreshGoalsBtn"),
  goalsTableBody: document.getElementById("goalsTableBody"),
  learningStatusForm: document.getElementById("learningStatusForm"),
  learningStatusIdInput: document.getElementById("learningStatusIdInput"),
  learningTopicInput: document.getElementById("learningTopicInput"),
  learningStatusSelect: document.getElementById("learningStatusSelect"),
  masteryLevelInput: document.getElementById("masteryLevelInput"),
  manualImportanceInput: document.getElementById("manualImportanceInput"),
  currentProblemInput: document.getElementById("currentProblemInput"),
  learningGoalSelect: document.getElementById("learningGoalSelect"),
  relatedNoteRefsInput: document.getElementById("relatedNoteRefsInput"),
  projectRelevanceInput: document.getElementById("projectRelevanceInput"),
  saveLearningStatusBtn: document.getElementById("saveLearningStatusBtn"),
  cancelLearningStatusEditBtn: document.getElementById("cancelLearningStatusEditBtn"),
  refreshLearningStatusBtn: document.getElementById("refreshLearningStatusBtn"),
  learningStatusTableBody: document.getElementById("learningStatusTableBody"),
  checkHealthBtn: document.getElementById("checkHealthBtn"),
  uploadForm: document.getElementById("uploadForm"),
  fileInput: document.getElementById("fileInput"),
  uploadBtn: document.getElementById("uploadBtn"),
  uploadResult: document.getElementById("uploadResult"),
  refreshDocumentsBtn: document.getElementById("refreshDocumentsBtn"),
  documentsTableBody: document.getElementById("documentsTableBody"),
  refreshDetailBtn: document.getElementById("refreshDetailBtn"),
  viewChunksDetailBtn: document.getElementById("viewChunksDetailBtn"),
  reindexDetailBtn: document.getElementById("reindexDetailBtn"),
  deleteDetailBtn: document.getElementById("deleteDetailBtn"),
  purgeDetailBtn: document.getElementById("purgeDetailBtn"),
  documentDetail: document.getElementById("documentDetail"),
  clearDocumentChunksBtn: document.getElementById("clearDocumentChunksBtn"),
  documentChunksBox: document.getElementById("documentChunksBox"),
  retrieveForm: document.getElementById("retrieveForm"),
  retrieveQuestionInput: document.getElementById("retrieveQuestionInput"),
  retrieveDocumentIdInput: document.getElementById("retrieveDocumentIdInput"),
  retrieveTopKInput: document.getElementById("retrieveTopKInput"),
  retrieveBtn: document.getElementById("retrieveBtn"),
  retrieveResultBox: document.getElementById("retrieveResultBox"),
  askForm: document.getElementById("askForm"),
  questionInput: document.getElementById("questionInput"),
  askDocumentIdInput: document.getElementById("askDocumentIdInput"),
  topKInput: document.getElementById("topKInput"),
  askBtn: document.getElementById("askBtn"),
  answerBox: document.getElementById("answerBox"),
  sourcesList: document.getElementById("sourcesList"),
  refreshHistoryBtn: document.getElementById("refreshHistoryBtn"),
  historyFilterForm: document.getElementById("historyFilterForm"),
  historyKeywordInput: document.getElementById("historyKeywordInput"),
  historyDocumentIdInput: document.getElementById("historyDocumentIdInput"),
  historyPageInput: document.getElementById("historyPageInput"),
  historyPageSizeInput: document.getElementById("historyPageSizeInput"),
  historyTableBody: document.getElementById("historyTableBody"),
  historyPager: document.getElementById("historyPager"),
  historyDetailBox: document.getElementById("historyDetailBox"),
  errorBox: document.getElementById("errorBox"),
  clearErrorsBtn: document.getElementById("clearErrorsBtn"),
};

function setLoading(element, isLoading) {
  if (!element) return;
  element.disabled = isLoading;
  element.classList.toggle("loading", isLoading);
}

function setError(message) {
  elements.errorBox.classList.remove("empty");
  elements.errorBox.textContent = message || "未知错误。";
}

function clearErrors() {
  elements.errorBox.classList.add("empty");
  elements.errorBox.textContent = "暂无错误。";
}

function escapeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return map[char];
  });
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  return escapeText(value);
}

function formatScore(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toFixed(4);
  }
  return displayValue(value);
}

function safeFilenameFromPath(value) {
  if (!value) return "-";
  const normalized = String(value).replaceAll("\\", "/");
  return escapeText(normalized.split("/").filter(Boolean).pop() || normalized);
}

function updateDetailButtons(status) {
  const hasDocument = Boolean(state.selectedDocumentId);
  const isDeleted = status === "deleted";
  const isPurged = status === "purged";
  elements.refreshDetailBtn.disabled = !hasDocument;
  elements.viewChunksDetailBtn.disabled = !hasDocument || isDeleted || isPurged;
  elements.deleteDetailBtn.disabled = !hasDocument || isDeleted || isPurged;
  elements.purgeDetailBtn.disabled = !hasDocument || isPurged;
  elements.reindexDetailBtn.disabled = !hasDocument || isDeleted || isPurged;
}

async function parseResponse(response) {
  const text = await response.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch (error) {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 300)}`);
      }
      throw new Error("后端返回内容不是合法 JSON。");
    }
  }

  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail) ? JSON.stringify(detail, null, 2) : detail || response.statusText;
    throw new Error(`请求失败（HTTP ${response.status}）：${message}`);
  }
  return body;
}

async function requestJson(url, options = {}) {
  try {
    const response = await fetch(url, options);
    return await parseResponse(response);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error(`网络错误：无法调用 ${url}，请确认后端服务正在运行。`);
    }
    throw error;
  }
}

function statusClass(status) {
  const value = String(status || "").toLowerCase();
  if (value === "completed") return "completed";
  if (value === "failed") return "failed";
  if (value === "processing") return "processing";
  if (value === "duplicate") return "processing";
  if (value === "deleted") return "deleted";
  if (value === "purged") return "deleted";
  return "";
}

async function checkHealth() {
  setLoading(elements.checkHealthBtn, true);
  try {
    const data = await requestJson("/health");
    const ok = data?.status === "ok";
    elements.healthText.textContent = ok ? "可访问" : `状态异常：${data?.status || "未知"}`;
    elements.healthBadge.textContent = ok ? "在线" : "异常";
    elements.healthBadge.className = `status-badge ${ok ? "ok" : "warn"}`;
  } catch (error) {
    elements.healthText.textContent = "不可访问";
    elements.healthBadge.textContent = "离线";
    elements.healthBadge.className = "status-badge error";
    setError(error.message);
  } finally {
    setLoading(elements.checkHealthBtn, false);
  }
}

async function loadAdminStatus() {
  try {
    const data = await requestJson("/admin/status");
    state.rerankEnabled = Boolean(data.rerank_enabled);
    elements.rerankEnabledText.textContent = state.rerankEnabled ? "已启用" : "未启用";
    elements.rerankModelText.textContent = data.rerank_model || "-";
    elements.rerankTopNText.textContent = displayValue(data.rerank_top_n);
    elements.rerankHintText.textContent = state.rerankEnabled
      ? "启用后会尝试 rerank；模型或依赖不可用时自动回退到 Qdrant 原始排序。"
      : "当前使用 Qdrant 原始排序。";
  } catch (error) {
    elements.rerankEnabledText.textContent = "加载失败";
    elements.rerankModelText.textContent = "-";
    elements.rerankTopNText.textContent = "-";
    elements.rerankHintText.textContent = "无法读取 rerank 状态。";
    setError(error.message);
  }
}

function renderUploadResult(data) {
  const hint = data.status === "processing"
    ? "<p>当前状态为 processing，可以刷新文档详情查看最终 completed 或 failed 状态。</p>"
    : data.status === "duplicate"
      ? "<p>检测到相同文件已存在，系统不会重复解析、向量化或写入 Qdrant。</p>"
    : "";
  elements.uploadResult.classList.remove("empty");
  elements.uploadResult.innerHTML = `
    <div><span class="field-label">文档 ID</span><code>${displayValue(data.document_id)}</code></div>
    <div><span class="field-label">文件名</span>${displayValue(data.filename)}</div>
    <div><span class="field-label">状态</span><span class="status ${statusClass(data.status)}">${displayValue(data.status)}</span></div>
    <div><span class="field-label">分块数</span>${displayValue(data.chunk_count)}</div>
    <div><span class="field-label">文件 SHA256</span><code>${displayValue(data.file_hash)}</code></div>
    <div><span class="field-label">提示</span>${displayValue(data.message)}</div>
    ${hint}
  `;
}

async function uploadDocument(event) {
  event.preventDefault();
  clearErrors();
  const file = elements.fileInput.files[0];
  if (!file) {
    setError("请先选择 PDF、DOCX、TXT 或 CSV 文件。");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  setLoading(elements.uploadBtn, true);
  elements.uploadResult.classList.remove("empty");
  elements.uploadResult.textContent = "正在上传...";

  try {
    const data = await requestJson("/documents/upload", {
      method: "POST",
      body: formData,
    });
    renderUploadResult(data);
    state.selectedDocumentId = data.document_id;
    state.selectedDocumentStatus = data.status;
    updateDetailButtons(data.status);
    await refreshDocuments();
    await loadDocumentDetail(data.document_id);
  } catch (error) {
    elements.uploadResult.classList.add("empty");
    elements.uploadResult.textContent = "上传失败。";
    setError(error.message);
  } finally {
    setLoading(elements.uploadBtn, false);
  }
}

function renderDocuments(documents) {
  if (!Array.isArray(documents) || documents.length === 0) {
    elements.documentsTableBody.innerHTML = '<tr><td colspan="9" class="empty-cell">没有文档记录。</td></tr>';
    return;
  }

  elements.documentsTableBody.innerHTML = documents.map((doc) => {
    const documentId = doc.id || doc.document_id;
    const isDeleted = doc.status === "deleted";
    const isPurged = doc.status === "purged";
    return `
      <tr>
        <td><code>${displayValue(documentId)}</code></td>
        <td>${displayValue(doc.filename)}</td>
        <td>${displayValue(doc.file_type)}</td>
        <td>${displayValue(doc.chunk_count)}</td>
        <td><span class="status ${statusClass(doc.status)}">${displayValue(doc.status)}</span></td>
        <td>${displayValue(doc.created_at)}</td>
        <td>${displayValue(doc.processed_at)}</td>
        <td>${displayValue(doc.error_message)}</td>
        <td>
          <div class="button-row compact">
            <button type="button" data-action="detail" data-document-id="${escapeText(documentId)}">详情</button>
            <button class="secondary" type="button" data-action="chunks" data-document-id="${escapeText(documentId)}" ${isDeleted || isPurged ? "disabled" : ""}>Chunks</button>
            <button class="secondary" type="button" data-action="reindex" data-document-id="${escapeText(documentId)}" ${isDeleted || isPurged ? "disabled" : ""}>重建</button>
            <button class="danger" type="button" data-action="delete" data-document-id="${escapeText(documentId)}" ${isDeleted || isPurged ? "disabled" : ""}>软删除</button>
            <button class="danger" type="button" data-action="purge" data-document-id="${escapeText(documentId)}" ${isPurged ? "disabled" : ""}>Purge</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

async function refreshDocuments() {
  clearErrors();
  setLoading(elements.refreshDocumentsBtn, true);
  try {
    const data = await requestJson("/documents");
    renderDocuments(data);
  } catch (error) {
    setError(error.message);
  } finally {
    setLoading(elements.refreshDocumentsBtn, false);
  }
}

function renderDocumentDetail(doc) {
  if (!doc) {
    elements.documentDetail.classList.add("empty");
    elements.documentDetail.textContent = "文档不存在。";
    updateDetailButtons(null);
    return;
  }

  const failed = String(doc.status || "").toLowerCase() === "failed";
  const processing = String(doc.status || "").toLowerCase() === "processing";
  const deleted = String(doc.status || "").toLowerCase() === "deleted";
  const purged = String(doc.status || "").toLowerCase() === "purged";
  state.selectedDocumentStatus = doc.status;
  updateDetailButtons(doc.status);
  elements.documentDetail.classList.remove("empty");
  elements.documentDetail.innerHTML = `
    <div><span class="field-label">文档 ID</span><code>${displayValue(doc.id || doc.document_id)}</code></div>
    <div><span class="field-label">文件名</span>${displayValue(doc.filename)}</div>
    <div><span class="field-label">类型</span>${displayValue(doc.file_type)}</div>
    <div><span class="field-label">保存文件</span>${safeFilenameFromPath(doc.file_path)}</div>
    <div><span class="field-label">文件 SHA256</span><code>${displayValue(doc.file_hash)}</code></div>
    <div><span class="field-label">分块数</span>${displayValue(doc.chunk_count)}</div>
    <div><span class="field-label">状态</span><span class="status ${statusClass(doc.status)}">${displayValue(doc.status)}</span></div>
    <div><span class="field-label">创建时间</span>${displayValue(doc.created_at)}</div>
    <div><span class="field-label">更新时间</span>${displayValue(doc.updated_at)}</div>
    <div><span class="field-label">处理时间</span>${displayValue(doc.processed_at)}</div>
    <div><span class="field-label">删除时间</span>${displayValue(doc.deleted_at)}</div>
    <div><span class="field-label">Purge 时间</span>${displayValue(doc.purged_at)}</div>
    ${processing ? '<p class="notice">文档正在处理中，可以稍后点击“刷新详情”。</p>' : ""}
    ${deleted ? '<p class="notice danger-text">文档已软删除，向量索引已按 document_id 清理，源文件和数据库记录默认保留。</p>' : ""}
    ${purged ? '<p class="notice danger-text">文档已 purge，源文件已尝试清理，数据库记录保留为 purged 状态。</p>' : ""}
    ${failed ? `<div><span class="field-label">错误</span>${displayValue(doc.error_message || "处理失败，请查看后端日志。")}</div>` : ""}
  `;
}

async function loadDocumentDetail(documentId) {
  if (!documentId) return;
  clearErrors();
  state.selectedDocumentId = documentId;
  updateDetailButtons(state.selectedDocumentStatus);
  elements.documentDetail.classList.remove("empty");
  elements.documentDetail.textContent = "正在加载详情...";

  try {
    const data = await requestJson(`/documents/${encodeURIComponent(documentId)}`);
    renderDocumentDetail(data);
  } catch (error) {
    setError(error.message);
    elements.documentDetail.classList.add("empty");
    elements.documentDetail.textContent = "加载文档详情失败。";
  }
}

function renderDocumentChunks(data) {
  if (!data) {
    elements.documentChunksBox.classList.add("empty");
    elements.documentChunksBox.textContent = "当前文档没有 chunks。";
    return;
  }

  const chunks = Array.isArray(data.chunks)
    ? [...data.chunks].sort((left, right) => Number(left.chunk_index || 0) - Number(right.chunk_index || 0))
    : [];

  elements.documentChunksBox.classList.remove("empty");
  const summary = `
    <div class="debug-summary">
      <div><span class="field-label">文档 ID</span><code>${displayValue(data.document_id)}</code></div>
      <div><span class="field-label">文件名</span>${displayValue(data.filename)}</div>
      <div><span class="field-label">状态</span><span class="status ${statusClass(data.status)}">${displayValue(data.status)}</span></div>
      <div><span class="field-label">分块数</span>${displayValue(data.chunk_count)}</div>
    </div>
  `;

  if (chunks.length === 0) {
    elements.documentChunksBox.innerHTML = `${summary}<p class="empty">当前文档没有 chunks。</p>`;
    return;
  }

  elements.documentChunksBox.innerHTML = summary + chunks.map((chunk, index) => `
    <article class="chunk-card">
      <div class="chunk-meta">
        <strong>Chunk ${index + 1}</strong>
        <span>chunk_id：<code>${displayValue(chunk.chunk_id)}</code></span>
        <span>chunk_index：${displayValue(chunk.chunk_index)}</span>
        <span>页码：${displayValue(chunk.page_no)}</span>
        <span>章节：${displayValue(chunk.section_title)}</span>
      </div>
      <details open>
        <summary>查看文本</summary>
        <div class="chunk-text">${displayValue(chunk.text)}</div>
      </details>
    </article>
  `).join("");
}

async function loadDocumentChunks(documentId) {
  if (!documentId) return;
  clearErrors();
  state.selectedDocumentId = documentId;
  elements.documentChunksBox.classList.remove("empty");
  elements.documentChunksBox.textContent = "正在加载文档 chunks...";

  try {
    const data = await requestJson(`/documents/${encodeURIComponent(documentId)}/chunks`);
    renderDocumentChunks(data);
  } catch (error) {
    elements.documentChunksBox.classList.add("empty");
    elements.documentChunksBox.textContent = "加载文档 chunks 失败。";
    setError(error.message);
  }
}

function clearDocumentChunks() {
  elements.documentChunksBox.classList.add("empty");
  elements.documentChunksBox.textContent = "请选择文档后点击“查看 chunks”。";
}

function renderRetrieveResult(data) {
  if (!data) {
    elements.retrieveResultBox.classList.add("empty");
    elements.retrieveResultBox.textContent = "没有检索到相关 chunk。";
    return;
  }

  const chunks = Array.isArray(data.chunks) ? data.chunks : [];
  elements.retrieveResultBox.classList.remove("empty");
  const summary = `
    <div class="debug-summary">
      <div><span class="field-label">原始 question</span>${displayValue(data.question)}</div>
      <div><span class="field-label">rewritten_query</span>${displayValue(data.rewritten_query)}</div>
      <div><span class="field-label">文档 ID</span>${displayValue(data.document_id)}</div>
      <div><span class="field-label">Top K</span>${displayValue(data.top_k)}</div>
      <div><span class="field-label">检索结果数</span>${displayValue(data.retrieved_count)}</div>
    </div>
  `;

  if (chunks.length === 0) {
    elements.retrieveResultBox.innerHTML = `${summary}<p class="empty">没有检索到相关 chunk。</p>`;
    return;
  }

  elements.retrieveResultBox.innerHTML = summary + chunks.map((chunk, index) => `
    <article class="chunk-card">
      <div class="chunk-meta">
        <strong>结果 ${index + 1}</strong>
        <span>chunk_id：<code>${displayValue(chunk.chunk_id)}</code></span>
        <span>document_id：<code>${displayValue(chunk.document_id)}</code></span>
        <span>文件名：${displayValue(chunk.filename)}</span>
        <span>类型：${displayValue(chunk.file_type)}</span>
        <span>页码：${displayValue(chunk.page_no)}</span>
        <span>章节：${displayValue(chunk.section_title)}</span>
        <span>chunk_index：${displayValue(chunk.chunk_index)}</span>
        <span>score：${formatScore(chunk.score)}</span>
        <span>rerank_score：${formatScore(chunk.rerank_score)}</span>
      </div>
      <details open>
        <summary>查看文本</summary>
        <div class="chunk-text">${displayValue(chunk.text)}</div>
      </details>
    </article>
  `).join("");
}

async function retrieveRag(event) {
  event.preventDefault();
  clearErrors();
  const question = elements.retrieveQuestionInput.value.trim();
  const documentId = elements.retrieveDocumentIdInput.value.trim();
  const topK = Number.parseInt(elements.retrieveTopKInput.value || "5", 10);

  if (!question) {
    setError("请输入检索问题。");
    return;
  }

  const payload = {
    question,
    top_k: Number.isFinite(topK) ? topK : 5,
  };
  if (documentId) {
    payload.document_id = documentId;
  }

  setLoading(elements.retrieveBtn, true);
  elements.retrieveResultBox.classList.remove("empty");
  elements.retrieveResultBox.textContent = "正在执行检索...";

  try {
    const data = await requestJson("/rag/retrieve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderRetrieveResult(data);
  } catch (error) {
    elements.retrieveResultBox.classList.add("empty");
    elements.retrieveResultBox.textContent = "检索调试失败。";
    setError(error.message);
  } finally {
    setLoading(elements.retrieveBtn, false);
  }
}

async function deleteDocument(documentId) {
  if (!documentId) return;
  const confirmed = window.confirm("确认软删除该文档吗？该操作会从向量库移除文档 chunks，但会保留源文件和数据库记录。");
  if (!confirmed) return;

  clearErrors();
  try {
    const data = await requestJson(`/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
    elements.documentDetail.classList.remove("empty");
    elements.documentDetail.innerHTML = `<p>文档已软删除：<code>${displayValue(data.document_id)}</code></p>`;
    state.selectedDocumentStatus = "deleted";
    updateDetailButtons("deleted");
    await refreshDocuments();
    await loadDocumentDetail(documentId);
  } catch (error) {
    setError(error.message);
  }
}

async function purgeDocument(documentId) {
  if (!documentId) return;
  const confirmed = window.confirm("确认 purge 该文档吗？purge 是危险操作，会从向量库移除 chunks，并物理删除 uploads 下源文件或标记为彻底清理。");
  if (!confirmed) return;

  clearErrors();
  try {
    const data = await requestJson(`/documents/${encodeURIComponent(documentId)}/purge`, { method: "DELETE" });
    elements.documentDetail.classList.remove("empty");
    elements.documentDetail.innerHTML = `<p>文档已 purge：<code>${displayValue(data.document_id)}</code></p>`;
    state.selectedDocumentStatus = "purged";
    updateDetailButtons("purged");
    await refreshDocuments();
    await loadDocumentDetail(documentId);
  } catch (error) {
    setError(error.message);
  }
}

async function reindexDocument(documentId) {
  if (!documentId) return;
  clearErrors();
  try {
    const data = await requestJson(`/documents/${encodeURIComponent(documentId)}/reindex`, { method: "POST" });
    elements.documentDetail.classList.remove("empty");
    elements.documentDetail.innerHTML = `<p>重建索引已开始：<code>${displayValue(data.document_id)}</code>，当前状态为 processing。</p>`;
    state.selectedDocumentStatus = "processing";
    updateDetailButtons("processing");
    await refreshDocuments();
    await loadDocumentDetail(documentId);
  } catch (error) {
    setError(error.message);
  }
}

function renderSources(sources) {
  if (!Array.isArray(sources) || sources.length === 0) {
    elements.sourcesList.classList.add("empty");
    elements.sourcesList.textContent = "没有返回引用来源。";
    return;
  }

  elements.sourcesList.classList.remove("empty");
  const hasRerankScore = sources.some((source) => typeof source.rerank_score === "number");
  const fallbackNotice = state.rerankEnabled && !hasRerankScore
    ? '<p class="notice">当前 rerank 未返回分数，可能已回退到 Qdrant 原始排序。</p>'
    : "";
  elements.sourcesList.innerHTML = sources.map((source, index) => {
    const score = typeof source.score === "number" ? source.score.toFixed(4) : source.score;
    const rerankScore = typeof source.rerank_score === "number" ? source.rerank_score.toFixed(4) : "-";
    return `
      <article class="source-card">
        <div class="source-meta">
          <strong>来源 ${index + 1}</strong>
          <span>文件名：${displayValue(source.filename)}</span>
          <span>页码：${displayValue(source.page_no)}</span>
          <span>分块：${displayValue(source.chunk_index)}</span>
          <span>分数：${displayValue(score)}</span>
          <span>Rerank 分数：${displayValue(rerankScore)}</span>
        </div>
        <div class="source-text">${displayValue(source.text)}</div>
      </article>
    `;
  }).join("") + fallbackNotice;
}

function renderHistoryList(data) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const page = Number(data?.page || 1);
  const pageSize = Number(data?.page_size || 20);
  const total = Number(data?.total || 0);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  elements.historyPager.classList.remove("empty");
  elements.historyPager.textContent = `共 ${total} 条，当前第 ${page} / ${totalPages} 页，每页 ${pageSize} 条。`;

  if (items.length === 0) {
    elements.historyTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">没有历史问答记录。</td></tr>';
    return;
  }

  elements.historyTableBody.innerHTML = items.map((item) => `
    <tr>
      <td><code>${displayValue(item.id)}</code></td>
      <td class="preview-cell">${displayValue(item.question)}</td>
      <td class="preview-cell">${displayValue(item.answer_preview)}</td>
      <td>${displayValue(item.source_count)}</td>
      <td>${displayValue(item.created_at)}</td>
      <td>
        <button type="button" data-action="history-detail" data-qa-id="${escapeText(item.id)}">详情</button>
      </td>
    </tr>
  `).join("");
}

async function refreshHistory(event) {
  if (event) event.preventDefault();
  clearErrors();
  setLoading(elements.refreshHistoryBtn, true);

  const page = Number.parseInt(elements.historyPageInput.value || "1", 10);
  const pageSize = Number.parseInt(elements.historyPageSizeInput.value || "20", 10);
  const keyword = elements.historyKeywordInput.value.trim();
  const documentId = elements.historyDocumentIdInput.value.trim();
  const params = new URLSearchParams({
    page: String(Number.isFinite(page) ? page : 1),
    page_size: String(Number.isFinite(pageSize) ? pageSize : 20),
  });
  if (keyword) params.set("keyword", keyword);
  if (documentId) params.set("document_id", documentId);

  try {
    const data = await requestJson(`/qa/history?${params.toString()}`);
    renderHistoryList(data);
  } catch (error) {
    elements.historyTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">加载历史问答失败。</td></tr>';
    elements.historyPager.classList.add("empty");
    elements.historyPager.textContent = "暂无分页信息。";
    setError(error.message);
  } finally {
    setLoading(elements.refreshHistoryBtn, false);
  }
}

function renderHistoryDetail(data) {
  if (!data) {
    elements.historyDetailBox.classList.add("empty");
    elements.historyDetailBox.textContent = "请选择一条历史问答查看详情。";
    return;
  }

  const sources = Array.isArray(data.source_chunks) ? data.source_chunks : [];
  const sourceHtml = sources.length === 0
    ? '<p class="empty">该历史问答没有保存 sources。</p>'
    : sources.map((source, index) => `
      <article class="source-card">
        <div class="source-meta">
          <strong>历史来源 ${index + 1}</strong>
          <span>文件名：${displayValue(source.filename)}</span>
          <span>页码：${displayValue(source.page_no)}</span>
          <span>分块：${displayValue(source.chunk_index)}</span>
          <span>score：${formatScore(source.score)}</span>
          <span>rerank_score：${formatScore(source.rerank_score)}</span>
        </div>
        <details open>
          <summary>查看来源文本</summary>
          <div class="source-text">${displayValue(source.text)}</div>
        </details>
      </article>
    `).join("");

  elements.historyDetailBox.classList.remove("empty");
  elements.historyDetailBox.innerHTML = `
    <div class="debug-summary">
      <div><span class="field-label">问答 ID</span><code>${displayValue(data.id)}</code></div>
      <div><span class="field-label">创建时间</span>${displayValue(data.created_at)}</div>
      <div><span class="field-label">原始 question</span>${displayValue(data.question)}</div>
      <div><span class="field-label">rewritten_query</span>${displayValue(data.rewritten_query)}</div>
    </div>
    <details open>
      <summary>查看答案</summary>
      <div class="history-answer">${displayValue(data.answer)}</div>
    </details>
    <h3>Sources</h3>
    ${sourceHtml}
  `;
}

async function loadHistoryDetail(qaId) {
  if (!qaId) return;
  clearErrors();
  elements.historyDetailBox.classList.remove("empty");
  elements.historyDetailBox.textContent = "正在加载历史问答详情...";

  try {
    const data = await requestJson(`/qa/history/${encodeURIComponent(qaId)}`);
    renderHistoryDetail(data);
  } catch (error) {
    elements.historyDetailBox.classList.add("empty");
    elements.historyDetailBox.textContent = "加载历史问答详情失败。";
    setError(error.message);
  }
}

async function askRag(event) {
  event.preventDefault();
  clearErrors();
  const question = elements.questionInput.value.trim();
  const documentId = elements.askDocumentIdInput.value.trim();
  const topK = Number.parseInt(elements.topKInput.value || "5", 10);

  if (!question) {
    setError("请输入问题。");
    return;
  }

  const payload = {
    question,
    top_k: Number.isFinite(topK) ? topK : 5,
  };
  if (documentId) {
    payload.document_id = documentId;
  }

  setLoading(elements.askBtn, true);
  elements.answerBox.classList.remove("empty");
  elements.answerBox.textContent = "正在提问...";
  renderSources([]);

  try {
    const data = await requestJson("/rag/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    elements.answerBox.textContent = data.answer || "后端没有返回答案。";
    renderSources(data.sources);
    await refreshHistory();
  } catch (error) {
    elements.answerBox.classList.add("empty");
    elements.answerBox.textContent = "提问失败。";
    setError(error.message);
  } finally {
    setLoading(elements.askBtn, false);
  }
}

const goalTypeLabels = {
  job: "求职目标",
  learning: "学习目标",
  project: "项目目标",
  skill: "技能目标",
};

const goalStatusLabels = {
  active: "进行中",
  paused: "已暂停",
  completed: "已完成",
};

const learningStatusLabels = {
  not_started: "未开始",
  learning: "学习中",
  practicing: "实践中",
  mastered: "已掌握",
  paused: "暂停",
};

function splitCommaValues(value) {
  return String(value || "").split(/[,，]/).map((item) => item.trim()).filter(Boolean);
}

function splitLineValues(value) {
  return String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function resetGoalForm() {
  elements.goalForm.reset();
  elements.goalIdInput.value = "";
  elements.goalPriorityInput.value = "3";
  elements.goalStatusSelect.value = "active";
  elements.saveGoalBtn.textContent = "新增目标";
}

function populateGoalOptions(selectedIds = []) {
  const selected = new Set(selectedIds);
  elements.learningGoalSelect.innerHTML = state.goals.map((goal) => `
    <option value="${escapeText(goal.id)}" ${selected.has(goal.id) ? "selected" : ""}>
      ${displayValue(goal.name)}（优先级 ${displayValue(goal.priority)}，${displayValue(goalStatusLabels[goal.status])}）
    </option>
  `).join("");
}

function renderGoals(goals) {
  if (!Array.isArray(goals) || goals.length === 0) {
    elements.goalsTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">没有学习目标。</td></tr>';
    return;
  }
  elements.goalsTableBody.innerHTML = goals.map((goal) => {
    const transitionButtons = goal.status === "paused"
      ? `<button type="button" data-goal-action="status" data-status="active" data-goal-id="${escapeText(goal.id)}">恢复</button>`
      : goal.status === "completed"
        ? `<button type="button" data-goal-action="status" data-status="active" data-goal-id="${escapeText(goal.id)}">恢复</button>`
        : `<button class="secondary" type="button" data-goal-action="status" data-status="paused" data-goal-id="${escapeText(goal.id)}">暂停</button>
           <button type="button" data-goal-action="status" data-status="completed" data-goal-id="${escapeText(goal.id)}">完成</button>`;
    return `
      <tr>
        <td><strong>${displayValue(goal.name)}</strong><br><span class="muted-text">${displayValue(goal.description)}</span></td>
        <td>${displayValue(goalTypeLabels[goal.goal_type] || goal.goal_type)}</td>
        <td>${displayValue(goal.priority)}</td>
        <td><span class="status ${statusClass(goal.status)}">${displayValue(goalStatusLabels[goal.status] || goal.status)}</span></td>
        <td>${displayValue((goal.related_topics || []).join("、"))}</td>
        <td><div class="button-row compact">
          <button class="secondary" type="button" data-goal-action="edit" data-goal-id="${escapeText(goal.id)}">编辑</button>
          ${transitionButtons}
          <button class="danger" type="button" data-goal-action="delete" data-goal-id="${escapeText(goal.id)}">删除</button>
        </div></td>
      </tr>
    `;
  }).join("");
}

async function refreshGoals() {
  clearErrors();
  setLoading(elements.refreshGoalsBtn, true);
  const selectedIds = Array.from(elements.learningGoalSelect.selectedOptions).map((option) => option.value);
  try {
    const data = await requestJson("/goals");
    state.goals = Array.isArray(data?.items) ? data.items : [];
    renderGoals(state.goals);
    populateGoalOptions(selectedIds);
  } catch (error) {
    elements.goalsTableBody.innerHTML = '<tr><td colspan="6" class="empty-cell">加载学习目标失败。</td></tr>';
    setError(error.message);
  } finally {
    setLoading(elements.refreshGoalsBtn, false);
  }
}

async function saveGoal(event) {
  event.preventDefault();
  clearErrors();
  const goalId = elements.goalIdInput.value;
  const payload = {
    name: elements.goalNameInput.value.trim(),
    goal_type: elements.goalTypeSelect.value,
    description: elements.goalDescriptionInput.value.trim() || null,
    priority: Number.parseInt(elements.goalPriorityInput.value, 10),
    status: elements.goalStatusSelect.value,
    related_topics: splitCommaValues(elements.goalTopicsInput.value),
  };
  setLoading(elements.saveGoalBtn, true);
  try {
    await requestJson(goalId ? `/goals/${encodeURIComponent(goalId)}` : "/goals", {
      method: goalId ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    resetGoalForm();
    await refreshGoals();
    await refreshLearningStatuses();
  } catch (error) {
    setError(error.message);
  } finally {
    setLoading(elements.saveGoalBtn, false);
  }
}

function editGoal(goalId) {
  const goal = state.goals.find((item) => item.id === goalId);
  if (!goal) return;
  elements.goalIdInput.value = goal.id;
  elements.goalNameInput.value = goal.name;
  elements.goalTypeSelect.value = goal.goal_type;
  elements.goalDescriptionInput.value = goal.description || "";
  elements.goalPriorityInput.value = String(goal.priority);
  elements.goalStatusSelect.value = goal.status;
  elements.goalTopicsInput.value = (goal.related_topics || []).join(", ");
  elements.saveGoalBtn.textContent = "保存目标修改";
  elements.goalNameInput.focus();
}

async function updateGoalStatus(goalId, status) {
  try {
    await requestJson(`/goals/${encodeURIComponent(goalId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    await refreshGoals();
    await refreshLearningStatuses();
  } catch (error) {
    setError(error.message);
  }
}

async function deleteGoal(goalId) {
  const goal = state.goals.find((item) => item.id === goalId);
  if (!window.confirm(`确定删除目标“${goal?.name || goalId}”吗？学习状态中的关联 ID 会同时清理。`)) return;
  try {
    await requestJson(`/goals/${encodeURIComponent(goalId)}`, { method: "DELETE" });
    resetGoalForm();
    await refreshGoals();
    await refreshLearningStatuses();
  } catch (error) {
    setError(error.message);
  }
}

function resetLearningStatusForm() {
  elements.learningStatusForm.reset();
  elements.learningStatusIdInput.value = "";
  elements.learningStatusSelect.value = "not_started";
  elements.masteryLevelInput.value = "0";
  elements.manualImportanceInput.value = "3";
  elements.saveLearningStatusBtn.textContent = "新增学习状态";
  populateGoalOptions();
}

function renderLearningStatuses(records) {
  if (!Array.isArray(records) || records.length === 0) {
    elements.learningStatusTableBody.innerHTML = '<tr><td colspan="7" class="empty-cell">没有学习状态。</td></tr>';
    return;
  }
  elements.learningStatusTableBody.innerHTML = records.map((record) => {
    const breakdown = record.importance_breakdown || {};
    return `
      <tr>
        <td><strong>${displayValue(record.topic)}</strong><br><span class="muted-text">目标数：${displayValue((record.related_goal_ids || []).length)}</span></td>
        <td>${displayValue(learningStatusLabels[record.status] || record.status)}</td>
        <td>${displayValue(record.mastery_level)}</td>
        <td>${displayValue(record.manual_importance)}</td>
        <td>
          <strong>${formatScore(record.importance_score)}</strong>
          <span class="importance-stars" aria-label="${displayValue(record.importance_stars)} 星">${"★".repeat(record.importance_stars || 0)}</span>
          <details><summary>评分分量</summary>
            <div class="breakdown-list">
              <span>目标相关度 G：${formatScore(breakdown.goal_relevance)}</span>
              <span>掌握缺口 D：${formatScore(breakdown.mastery_gap)}</span>
              <span>手动重要度 M：${formatScore(breakdown.manual_importance)}</span>
              <span>项目相关度 R：${formatScore(breakdown.project_relevance)}</span>
            </div>
          </details>
        </td>
        <td class="preview-cell">${displayValue(record.current_problem)}</td>
        <td><div class="button-row compact">
          <button class="secondary" type="button" data-learning-action="edit" data-status-id="${escapeText(record.id)}">编辑</button>
          <button class="danger" type="button" data-learning-action="delete" data-status-id="${escapeText(record.id)}">删除</button>
        </div></td>
      </tr>
    `;
  }).join("");
}

async function refreshLearningStatuses() {
  clearErrors();
  setLoading(elements.refreshLearningStatusBtn, true);
  try {
    const data = await requestJson("/learning-status");
    state.learningStatuses = Array.isArray(data?.items) ? data.items : [];
    renderLearningStatuses(state.learningStatuses);
  } catch (error) {
    elements.learningStatusTableBody.innerHTML = '<tr><td colspan="7" class="empty-cell">加载学习状态失败。</td></tr>';
    setError(error.message);
  } finally {
    setLoading(elements.refreshLearningStatusBtn, false);
  }
}

async function saveLearningStatus(event) {
  event.preventDefault();
  clearErrors();
  const statusId = elements.learningStatusIdInput.value;
  const payload = {
    topic: elements.learningTopicInput.value.trim(),
    status: elements.learningStatusSelect.value,
    mastery_level: Number.parseInt(elements.masteryLevelInput.value, 10),
    manual_importance: Number.parseInt(elements.manualImportanceInput.value, 10),
    current_problem: elements.currentProblemInput.value.trim() || null,
    related_goal_ids: Array.from(elements.learningGoalSelect.selectedOptions).map((option) => option.value),
    related_note_refs: splitLineValues(elements.relatedNoteRefsInput.value),
    project_relevance: elements.projectRelevanceInput.checked,
  };
  setLoading(elements.saveLearningStatusBtn, true);
  try {
    await requestJson(statusId ? `/learning-status/${encodeURIComponent(statusId)}` : "/learning-status", {
      method: statusId ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    resetLearningStatusForm();
    await refreshLearningStatuses();
  } catch (error) {
    setError(error.message);
  } finally {
    setLoading(elements.saveLearningStatusBtn, false);
  }
}

function editLearningStatus(statusId) {
  const record = state.learningStatuses.find((item) => item.id === statusId);
  if (!record) return;
  elements.learningStatusIdInput.value = record.id;
  elements.learningTopicInput.value = record.topic;
  elements.learningStatusSelect.value = record.status;
  elements.masteryLevelInput.value = String(record.mastery_level);
  elements.manualImportanceInput.value = String(record.manual_importance);
  elements.currentProblemInput.value = record.current_problem || "";
  elements.relatedNoteRefsInput.value = (record.related_note_refs || []).join("\n");
  elements.projectRelevanceInput.checked = Boolean(record.project_relevance);
  populateGoalOptions(record.related_goal_ids || []);
  elements.saveLearningStatusBtn.textContent = "保存学习状态修改";
  elements.learningTopicInput.focus();
}

async function deleteLearningStatus(statusId) {
  const record = state.learningStatuses.find((item) => item.id === statusId);
  if (!window.confirm(`确定删除学习状态“${record?.topic || statusId}”吗？`)) return;
  try {
    await requestJson(`/learning-status/${encodeURIComponent(statusId)}`, { method: "DELETE" });
    resetLearningStatusForm();
    await refreshLearningStatuses();
  } catch (error) {
    setError(error.message);
  }
}

elements.checkHealthBtn.addEventListener("click", checkHealth);
elements.goalForm.addEventListener("submit", saveGoal);
elements.cancelGoalEditBtn.addEventListener("click", resetGoalForm);
elements.refreshGoalsBtn.addEventListener("click", refreshGoals);
elements.learningStatusForm.addEventListener("submit", saveLearningStatus);
elements.cancelLearningStatusEditBtn.addEventListener("click", resetLearningStatusForm);
elements.refreshLearningStatusBtn.addEventListener("click", refreshLearningStatuses);
elements.uploadForm.addEventListener("submit", uploadDocument);
elements.refreshDocumentsBtn.addEventListener("click", refreshDocuments);
elements.refreshDetailBtn.addEventListener("click", () => loadDocumentDetail(state.selectedDocumentId));
elements.viewChunksDetailBtn.addEventListener("click", () => loadDocumentChunks(state.selectedDocumentId));
elements.reindexDetailBtn.addEventListener("click", () => reindexDocument(state.selectedDocumentId));
elements.deleteDetailBtn.addEventListener("click", () => deleteDocument(state.selectedDocumentId));
elements.purgeDetailBtn.addEventListener("click", () => purgeDocument(state.selectedDocumentId));
elements.clearDocumentChunksBtn.addEventListener("click", clearDocumentChunks);
elements.retrieveForm.addEventListener("submit", retrieveRag);
elements.askForm.addEventListener("submit", askRag);
elements.refreshHistoryBtn.addEventListener("click", refreshHistory);
elements.historyFilterForm.addEventListener("submit", refreshHistory);
elements.clearErrorsBtn.addEventListener("click", clearErrors);
elements.documentsTableBody.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-document-id]");
  if (!button) return;
  const documentId = button.dataset.documentId;
  const action = button.dataset.action;
  if (action === "detail") {
    loadDocumentDetail(documentId);
  } else if (action === "chunks") {
    loadDocumentChunks(documentId);
  } else if (action === "delete") {
    deleteDocument(documentId);
  } else if (action === "purge") {
    purgeDocument(documentId);
  } else if (action === "reindex") {
    reindexDocument(documentId);
  }
});
elements.historyTableBody.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-qa-id]");
  if (!button) return;
  if (button.dataset.action === "history-detail") {
    loadHistoryDetail(button.dataset.qaId);
  }
});
elements.goalsTableBody.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-goal-id]");
  if (!button) return;
  if (button.dataset.goalAction === "edit") editGoal(button.dataset.goalId);
  if (button.dataset.goalAction === "status") updateGoalStatus(button.dataset.goalId, button.dataset.status);
  if (button.dataset.goalAction === "delete") deleteGoal(button.dataset.goalId);
});
elements.learningStatusTableBody.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-status-id]");
  if (!button) return;
  if (button.dataset.learningAction === "edit") editLearningStatus(button.dataset.statusId);
  if (button.dataset.learningAction === "delete") deleteLearningStatus(button.dataset.statusId);
});

checkHealth();
loadAdminStatus();
refreshHistory();
refreshGoals().then(refreshLearningStatuses);
