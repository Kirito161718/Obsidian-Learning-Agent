const state = {
  sources: [],
  reminders: null,
  notes: [],
  proposals: [],
  selectedProposal: null,
  diffsLoaded: false,
  cleanupPreviewed: false,
  batchRunning: false,
};
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let body = {};
    try {
      body = await response.json();
    } catch {
      // The status code remains useful when the response has no JSON body.
    }
    const detail = body.detail || {};
    throw new Error(detail.message || detail.error_code || `HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function message(text, error = false, retry = null) {
  const root = $("message");
  root.hidden = false;
  root.className = `message ${error ? "error" : "success"}`;
  root.replaceChildren(document.createTextNode(text));
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "重试";
    button.addEventListener("click", retry, { once: true });
    root.append(document.createTextNode(" "), button);
  }
}

function option(value, label) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

function td(value) {
  const cell = document.createElement("td");
  cell.textContent = value ?? "—";
  return cell;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : "—";
}

const reminderLabels = {
  disabled: "提醒关闭",
  never_checked: "从未检查",
  current: "正常",
  due: "到期",
  overdue: "逾期",
  error: "上次检查失败",
};

function reminderById(sourceId) {
  return state.reminders?.sources.find((item) => item.id === sourceId) || null;
}

function reminderBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status ${status}`;
  badge.textContent = reminderLabels[status] || status;
  return badge;
}

async function loadSources() {
  const [sources, reminders] = await Promise.all([
    api("/official/sources"),
    api("/official/reminders/summary"),
  ]);
  state.sources = sources;
  state.reminders = reminders;
  renderReminderSummary();
  renderSources();
}

function renderReminderSummary() {
  const summary = state.reminders;
  if (!summary) return;
  $("reminderSummary").innerHTML = `
    <div><span>来源</span><strong>${summary.total_sources}</strong></div>
    <div><span>到期</span><strong>${summary.due_count + summary.never_checked_count}</strong></div>
    <div><span>逾期 / 失败</span><strong>${summary.overdue_count} / ${summary.error_count}</strong></div>
    <div><span>待审批</span><strong>${summary.pending_proposal_count}</strong></div>
  `;
  const candidates = dueCandidates();
  $("auditDueBtn").disabled = state.batchRunning || candidates.length === 0;
  $("auditDueBtn").textContent = candidates.length
    ? `一键检查到期来源（${Math.min(candidates.length, summary.batch_limit)}）`
    : "暂无到期来源";
  if (candidates.length > summary.batch_limit) {
    message(
      `共有 ${candidates.length} 个来源需要检查，本次最多顺序处理 ${summary.batch_limit} 个，请完成后再处理下一批。`,
    );
  }
}

function renderSources() {
  const body = $("sourcesBody");
  const sourceSelect = $("linkSource");
  const snapshotSelect = $("snapshotSource");
  body.replaceChildren();
  sourceSelect.replaceChildren(option("", "请选择"));
  snapshotSelect.replaceChildren(option("", "请选择"));

  for (const source of state.sources) {
    const reminder = reminderById(source.id);
    sourceSelect.append(option(source.id, source.name));
    snapshotSelect.append(option(source.id, source.name));

    const row = document.createElement("tr");
    const nameCell = td("");
    const title = document.createElement("strong");
    title.textContent = source.name;
    const meta = document.createElement("small");
    meta.textContent = `${source.allowed_host} · ${source.source_type}`;
    nameCell.append(title, document.createElement("br"), meta);

    const statusCell = document.createElement("td");
    statusCell.append(reminderBadge(reminder?.reminder_status || "disabled"));

    const nextCell = td(
      reminder?.next_suggested_check_at
        ? formatDate(reminder.next_suggested_check_at)
        : reminder?.reminder_status === "never_checked"
          ? "建议现在检查"
          : "—",
    );

    const settingsCell = document.createElement("td");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = source.reminder_enabled;
    enabled.setAttribute("aria-label", `${source.name} 提醒开关`);
    const interval = document.createElement("input");
    interval.type = "number";
    interval.min = "1";
    interval.max = "365";
    interval.value = String(source.check_interval_days);
    interval.className = "interval-input";
    interval.setAttribute("aria-label", `${source.name} 检查间隔天数`);
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "保存";
    save.addEventListener("click", () => saveReminder(source.id, enabled, interval, save));
    const reminderControls = document.createElement("div");
    reminderControls.className = "reminder-controls";
    const enabledLabel = document.createElement("label");
    enabledLabel.append(enabled, document.createTextNode(" 提醒"));
    reminderControls.append(enabledLabel, interval, document.createTextNode("天"), save);
    settingsCell.append(reminderControls);

    const lastCell = td(
      source.last_error_code
        ? `失败：${source.last_error_code}`
        : source.last_checked_at
          ? `成功：${formatDate(source.last_checked_at)}`
          : "尚未检查",
    );

    const actions = document.createElement("td");
    const auditButton = document.createElement("button");
    auditButton.type = "button";
    auditButton.textContent = "检查更新";
    auditButton.disabled = !source.enabled || state.batchRunning;
    auditButton.addEventListener("click", () => auditSingleSource(source, auditButton));
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "secondary";
    toggle.textContent = source.enabled ? "停用来源" : "启用来源";
    toggle.addEventListener("click", () =>
      toggleSource(source, toggle).catch((error) =>
        message(error.message, true, () => toggle.click()),
      ),
    );
    actions.append(auditButton, toggle);
    row.append(nameCell, statusCell, nextCell, settingsCell, lastCell, actions);
    body.append(row);
  }

  if (!state.sources.length) {
    const row = document.createElement("tr");
    const cell = td("尚未登记官方来源。");
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
  }
}

async function saveReminder(sourceId, enabledInput, intervalInput, button) {
  button.disabled = true;
  try {
    await api(`/official/sources/${sourceId}/reminder`, {
      method: "PATCH",
      body: JSON.stringify({
        reminder_enabled: enabledInput.checked,
        check_interval_days: Number(intervalInput.value),
      }),
    });
    message("提醒设置已保存。");
    await loadSources();
  } catch (error) {
    message(error.message, true, () =>
      saveReminder(sourceId, enabledInput, intervalInput, button),
    );
  } finally {
    button.disabled = false;
  }
}

async function toggleSource(source, button) {
  button.disabled = true;
  await api(`/official/sources/${source.id}/${source.enabled ? "disable" : "enable"}`, {
    method: "POST",
  });
  message(source.enabled ? "来源已停用。" : "来源已启用。");
  await loadSources();
}

function classifyAuditResult(result) {
  if (result.status === "pending_approval") return "待审批";
  if (result.status === "no_update_needed") return "无需更新";
  if (result.changed === false) return "没有变化";
  return "检查完成";
}

async function auditSingleSource(source, button) {
  button.disabled = true;
  button.textContent = "检查中…";
  try {
    const result = await api(`/official/sources/${source.id}/audit`, { method: "POST" });
    message(`${source.name}：${classifyAuditResult(result)}。`);
    await Promise.all([loadSources(), loadProposals()]);
  } catch (error) {
    message(`${source.name}：检查失败（${error.message}）。`, true, () =>
      auditSingleSource(source, button),
    );
  } finally {
    button.disabled = false;
    button.textContent = "检查更新";
  }
}

function dueCandidates() {
  const actionable = new Set(["never_checked", "due", "overdue", "error"]);
  return (state.reminders?.sources || []).filter((item) =>
    actionable.has(item.reminder_status),
  );
}

async function auditDueSources() {
  if (state.batchRunning) return;
  const candidates = dueCandidates();
  const limit = state.reminders?.batch_limit || 10;
  const batch = candidates.slice(0, limit);
  if (!batch.length) return;
  state.batchRunning = true;
  $("auditDueBtn").disabled = true;
  $("batchProgress").hidden = false;
  $("batchResults").replaceChildren();
  const totals = { "没有变化": 0, "无需更新": 0, "待审批": 0, "检查完成": 0, "失败": 0 };

  for (let index = 0; index < batch.length; index += 1) {
    const source = batch[index];
    const percent = Math.round((index / batch.length) * 100);
    $("batchProgressBar").style.width = `${percent}%`;
    $("batchProgressText").textContent = `正在检查 ${index + 1}/${batch.length}：${source.name}`;
    const line = document.createElement("p");
    try {
      const result = await api(`/official/sources/${source.id}/audit`, { method: "POST" });
      const outcome = classifyAuditResult(result);
      totals[outcome] += 1;
      line.className = outcome === "待审批" ? "result pending" : "result success";
      line.textContent = `${source.name}：${outcome}`;
    } catch (error) {
      totals["失败"] += 1;
      line.className = "result error";
      line.textContent = `${source.name}：失败（${error.message}）`;
    }
    $("batchResults").append(line);
  }

  $("batchProgressBar").style.width = "100%";
  $("batchProgressText").textContent =
    `完成 ${batch.length} 个：没有变化 ${totals["没有变化"]}，无需更新 ${totals["无需更新"]}，` +
    `待审批 ${totals["待审批"]}，其他成功 ${totals["检查完成"]}，失败 ${totals["失败"]}。`;
  state.batchRunning = false;
  await Promise.all([loadSources(), loadProposals()]);
}

async function loadNotes() {
  const result = await api("/obsidian/notes?status=indexed");
  state.notes = result.items || [];
  const select = $("linkNote");
  select.replaceChildren(option("", "请选择"));
  for (const note of state.notes) {
    select.append(option(note.id, note.title || note.file_name));
  }
}

async function loadLinks() {
  const id = $("linkSource").value;
  const root = $("linksOutput");
  if (!id) {
    root.textContent = "请选择 Source 后查看关联。";
    return;
  }
  const rows = await api(`/official/sources/${id}/note-links`);
  root.replaceChildren();
  for (const row of rows) {
    const line = document.createElement("div");
    line.textContent = `已关联笔记 · 标题范围 ${JSON.stringify(row.target_heading_paths)}`;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "删除关联";
    button.addEventListener("click", async () => {
      try {
        await api(`/official/sources/${id}/note-links/${row.id}`, { method: "DELETE" });
        await loadLinks();
      } catch (error) {
        message(error.message, true);
      }
    });
    line.append(document.createTextNode(" "), button);
    root.append(line);
  }
  if (!rows.length) root.textContent = "暂无关联。";
}

async function loadSnapshots() {
  const sourceId = $("snapshotSource").value;
  const root = $("snapshotsOutput");
  root.replaceChildren();
  if (!sourceId) {
    root.textContent = "请选择 Source。";
    return;
  }
  const rows = await api(`/official/sources/${sourceId}/snapshots`);
  for (const row of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${formatDate(row.created_at)} · ${row.content_type} · ${row.section_manifest.length} sections`;
    button.addEventListener("click", async () => {
      try {
        const result = await api(`/official/snapshots/${row.id}/preview?max_chars=4000`);
        $("snapshotPreview").textContent = result.preview;
      } catch (error) {
        message(error.message, true);
      }
    });
    root.append(button, document.createElement("br"));
  }
  if (!rows.length) root.textContent = "暂无 Snapshot。";
}

async function loadProposals() {
  state.proposals = await api("/official/proposals");
  const select = $("proposalSelect");
  select.replaceChildren(option("", "请选择"));
  for (const proposal of state.proposals) {
    select.append(option(proposal.id, `${proposal.status} — ${proposal.summary}`));
  }
  state.selectedProposal = null;
  state.diffsLoaded = false;
  updateApproval();
}

async function selectProposal() {
  const id = $("proposalSelect").value;
  if (!id) {
    state.selectedProposal = null;
    $("proposalMeta").textContent = "";
    $("diffs").replaceChildren();
    updateApproval();
    return;
  }
  const [proposal, diffs] = await Promise.all([
    api(`/official/proposals/${id}`),
    api(`/official/proposals/${id}/diff`),
  ]);
  state.selectedProposal = proposal;
  state.diffsLoaded = true;
  $("proposalMeta").textContent =
    `状态：${proposal.status}\n风险：HIGH_RISK\n范围：全部修改一次性决策\n摘要：${proposal.summary}`;
  const root = $("diffs");
  root.replaceChildren();
  for (const item of proposal.items) {
    const card = document.createElement("section");
    card.className = "diff-card";
    const title = document.createElement("h3");
    title.textContent = `${item.relative_path} · 修改比例 ${(item.change_ratio * 100).toFixed(2)}%`;
    const evidence = document.createElement("pre");
    evidence.textContent = `Evidence\n${JSON.stringify(item.evidence, null, 2)}`;
    const diff = document.createElement("pre");
    diff.textContent = diffs.find((row) => row.item_id === item.id)?.diff || "Diff 不可用";
    card.append(title, evidence, diff);
    root.append(card);
  }
  $("confirmationText").placeholder = `APPLY ${id.slice(0, 8)}`;
  updateApproval();
}

function updateApproval() {
  const proposal = state.selectedProposal;
  const pending = proposal && proposal.status === "pending_approval" && state.diffsLoaded;
  $("approveBtn").disabled = !(pending && $("confirmAll").checked);
  $("rejectBtn").disabled = !pending;
}

async function decide(decision) {
  const proposal = state.selectedProposal;
  if (!proposal) return;
  try {
    const path = `/official/proposals/${proposal.id}/${decision}`;
    const payload = decision === "approve"
      ? {
          payload_hash: proposal.payload_hash,
          confirm_all: $("confirmAll").checked,
          confirmation_text: $("confirmationText").value,
        }
      : {
          payload_hash: proposal.payload_hash,
          reason: $("rejectionReason").value || null,
        };
    const result = await api(path, { method: "POST", body: JSON.stringify(payload) });
    message(`决策完成：${result.status}`);
    await Promise.all([loadProposals(), loadVersions(), loadSources()]);
  } catch (error) {
    message(error.message, true);
  }
}

async function loadVersions() {
  const rows = await api("/official/note-versions");
  const root = $("versionsOutput");
  root.replaceChildren();
  for (const row of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${row.relative_path} · ${formatDate(row.created_at)}${row.pinned ? " · pinned" : ""}`;
    button.addEventListener("click", async () => {
      try {
        $("versionDiff").textContent = (
          await api(`/official/note-versions/${row.id}/diff`)
        ).diff;
      } catch (error) {
        message(error.message, true);
      }
    });
    root.append(button, document.createElement("br"));
  }
  if (!rows.length) root.textContent = "暂无版本。";
}

async function cleanup(dryRun) {
  try {
    const result = await api("/official/maintenance/cleanup", {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun }),
    });
    $("cleanupOutput").textContent = JSON.stringify(result, null, 2);
    state.cleanupPreviewed = dryRun;
    $("cleanupApply").disabled = !dryRun;
    message(dryRun ? "清理预览完成。" : "清理完成。");
  } catch (error) {
    message(error.message, true);
  }
}

$("sourceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/official/sources", {
      method: "POST",
      body: JSON.stringify({
        name: $("sourceName").value,
        url: $("sourceUrl").value,
        source_type: $("sourceType").value,
        content_selector: $("contentSelector").value || null,
      }),
    });
    event.target.reset();
    message("官方来源已登记。");
    await loadSources();
  } catch (error) {
    message(error.message, true);
  }
});

$("linkForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const path = $("headingPath").value.split("/").map((value) => value.trim()).filter(Boolean);
    await api(`/official/sources/${$("linkSource").value}/note-links`, {
      method: "POST",
      body: JSON.stringify({
        obsidian_note_id: $("linkNote").value,
        target_heading_paths: path.length ? [path] : [],
      }),
    });
    message("笔记关联已创建。");
    await loadLinks();
  } catch (error) {
    message(error.message, true);
  }
});

$("linkSource").addEventListener("change", () => loadLinks().catch((error) => message(error.message, true)));
$("snapshotSource").addEventListener("change", () => loadSnapshots().catch((error) => message(error.message, true)));
$("proposalSelect").addEventListener("change", () => selectProposal().catch((error) => message(error.message, true)));
$("confirmAll").addEventListener("change", updateApproval);
$("approveBtn").addEventListener("click", () => decide("approve"));
$("rejectBtn").addEventListener("click", () => decide("reject"));
$("refreshProposals").addEventListener("click", () => loadProposals().catch((error) => message(error.message, true)));
$("refreshVersions").addEventListener("click", () => loadVersions().catch((error) => message(error.message, true)));
$("refreshSnapshots").addEventListener("click", () => loadSnapshots().catch((error) => message(error.message, true)));
$("refreshReminders").addEventListener("click", () => loadSources().catch((error) => message(error.message, true)));
$("auditDueBtn").addEventListener("click", () => auditDueSources().catch((error) => {
  state.batchRunning = false;
  message(error.message, true, () => $("auditDueBtn").click());
}));
$("cleanupPreview").addEventListener("click", () => cleanup(true));
$("cleanupApply").addEventListener("click", () => {
  if (state.cleanupPreviewed && window.confirm("确认执行精确范围清理？")) cleanup(false);
});

Promise.all([loadSources(), loadNotes(), loadProposals(), loadVersions()]).catch((error) =>
  message(`页面数据加载失败：${error.message}`, true, () => window.location.reload()),
);
