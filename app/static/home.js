"use strict";

const statusLabels = {
  healthy: "正常",
  degraded: "降级",
  unavailable: "不可用",
  disabled: "未启用",
  not_loaded: "未加载",
  loading: "加载中",
  ready: "就绪",
  error: "错误",
};

function componentStatus(value) {
  if (value && typeof value === "object") return value.status || "unavailable";
  return value ? "healthy" : "unavailable";
}

async function loadSystemStatus() {
  const summary = document.getElementById("systemStatusSummary");
  const root = document.getElementById("systemStatusItems");
  try {
    const response = await fetch("/system/status", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const items = [
      ["应用", componentStatus(data.application)],
      ["MySQL", componentStatus(data.database)],
      ["Qdrant", componentStatus(data.qdrant)],
      ["Vault", componentStatus(data.vault)],
      ["Embedding", componentStatus(data.embedding)],
      ["Reranker", componentStatus(data.reranker)],
    ];
    root.replaceChildren(...items.map(([label, status]) => {
      const item = document.createElement("div");
      item.className = `system-status-item ${status}`;
      const name = document.createElement("span");
      name.textContent = label;
      const value = document.createElement("strong");
      value.textContent = statusLabels[status] || status;
      item.append(name, value);
      return item;
    }));
    summary.textContent = `Active RAG Profile：${data.active_rag_profile || "未设置"}`;
  } catch (error) {
    summary.textContent = "详细状态暂时不可用；应用页面仍可继续使用。";
    root.replaceChildren();
  }
}

loadSystemStatus();
