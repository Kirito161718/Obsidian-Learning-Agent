const state = {
  selectedDocumentId: null,
  selectedDocumentStatus: null,
  rerankEnabled: false,
};

const elements = {
  healthBadge: document.getElementById("healthBadge"),
  healthText: document.getElementById("healthText"),
  rerankEnabledText: document.getElementById("rerankEnabledText"),
  rerankModelText: document.getElementById("rerankModelText"),
  rerankTopNText: document.getElementById("rerankTopNText"),
  rerankHintText: document.getElementById("rerankHintText"),
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

elements.checkHealthBtn.addEventListener("click", checkHealth);
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

checkHealth();
loadAdminStatus();
refreshHistory();
