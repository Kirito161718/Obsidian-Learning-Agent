const $ = (selector) => document.querySelector(selector);
let snapshot = { datasets: [], experiments: [], profiles: [] };

async function json(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败：${response.status}`);
  }
  return response.json();
}

function metric(value) {
  return typeof value === "number" ? value.toFixed(3) : "—";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function compactMap(value) {
  return Object.entries(value || {})
    .map(([key, count]) => `${escapeHtml(key)}:${escapeHtml(count)}`)
    .join(" · ") || "—";
}

function categoryMetricSummary(value) {
  return Object.entries(value || {})
    .map(
      ([category, metrics]) =>
        `${escapeHtml(category)} R:${metric(metrics["recall@10"])} N:${metric(metrics["ndcg@10"])}`,
    )
    .join(" · ") || "—";
}

async function validateDataset(id) {
  $("#status").textContent = "正在校验固定数据集…";
  await json(`/agent/rag-evaluation/datasets/${id}/validate`, { method: "POST" });
  await load();
}

async function runProfile(profile) {
  const dataset = snapshot.datasets.find((item) => item.status === "validated");
  if (!dataset) throw new Error("请先校验固定数据集。");
  const mode = $("#run-mode").value;
  const sequence = snapshot.experiments.filter(
    (item) =>
      item.retrieval_profile_version === profile.version &&
      item.experiment_type === mode,
  ).length + 1;
  const name = `ola-rag-${profile.version}-${mode}-${dataset.version}-ui-r${sequence}`;
  $("#status").textContent = `正在串行运行 ${profile.version}（${mode}）…`;
  await json("/agent/rag-evaluation/experiments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dataset_id: dataset.id,
      profile_id: profile.id,
      name,
      variant_key: profile.profile_key,
      experiment_type: mode,
      repeat_count: 1,
      seed: 7,
      publish_langsmith: true,
      run_now: true,
    }),
  });
  await load();
}

async function compareSelected() {
  const ids = [...document.querySelectorAll("[data-compare]:checked")].map(
    (item) => item.value,
  );
  if (ids.length < 2 || ids.length > 6) {
    throw new Error("请选择 2～6 个实验。");
  }
  const result = await json("/agent/rag-evaluation/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ experiment_ids: ids }),
  });
  const rows = result.experiments.map((item) => {
    const delta = item.delta_from_baseline;
    return [
      item.name,
      `Recall@10 Δ ${metric(delta["recall@10"])}`,
      `NDCG@10 Δ ${metric(delta["ndcg@10"])}`,
      `p95 Δ ${metric(delta.p95_latency)}`,
    ].join(" · ");
  });
  $("#comparison").textContent = rows.join("\n");
  $("#comparison").hidden = false;
}

async function activateProfile(profile) {
  if (!window.confirm(`确认激活 ${profile.version}？当前 Profile 将保留为回滚目标。`)) {
    return;
  }
  await json(`/agent/rag-evaluation/profiles/${profile.id}/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      confirm: true,
      expected_profile_version: profile.version,
    }),
  });
  await load();
}

function bindActions() {
  document.querySelectorAll("[data-validate]").forEach((button) => {
    button.addEventListener("click", () => {
      validateDataset(button.dataset.validate).catch((error) => {
        $("#status").textContent = error.message;
      });
    });
  });
  document.querySelectorAll("[data-run-profile]").forEach((button) => {
    button.addEventListener("click", () => {
      const profile = snapshot.profiles.find(
        (item) => item.id === button.dataset.runProfile,
      );
      runProfile(profile).catch((error) => {
        $("#status").textContent = error.message;
      });
    });
  });
  document.querySelectorAll("[data-activate-profile]").forEach((button) => {
    button.addEventListener("click", () => {
      const profile = snapshot.profiles.find(
        (item) => item.id === button.dataset.activateProfile,
      );
      activateProfile(profile).catch((error) => {
        $("#status").textContent = error.message;
      });
    });
  });
}

async function load() {
  $("#status").textContent = "正在读取评估状态…";
  try {
    const [datasets, experiments, profiles] = await Promise.all([
      json("/agent/rag-evaluation/datasets"),
      json("/agent/rag-evaluation/experiments"),
      json("/agent/rag-evaluation/profiles"),
    ]);
    snapshot = { datasets, experiments, profiles };
    const active = profiles.find((item) => item.is_active);
    const baseline = profiles.find((item) => item.is_baseline);
    $("#active-profile").textContent = active?.version || "未设置";
    $("#baseline-profile").textContent = baseline?.version || "未设置";
    $("#case-count").textContent = datasets.reduce((sum, item) => sum + item.case_count, 0);
    $("#running-count").textContent = experiments.filter(
      (item) => ["running", "cancel_requested"].includes(item.status),
    ).length;
    $("#datasets").innerHTML = datasets.map((item) => `
      <div class="card">
        <span class="tag">${escapeHtml(item.status)}</span>
        <h3>${escapeHtml(item.name)}</h3>
        <p class="meta">${escapeHtml(item.version)} · ${item.case_count} cases · ${item.stale_case_count} stale</p>
        <button data-validate="${escapeHtml(item.id)}">校验</button>
      </div>`).join("") || "<p>尚未注册本地固定数据集。</p>";
    $("#experiments").innerHTML = experiments.map((item) => `
      <tr>
        <td><input type="checkbox" data-compare value="${escapeHtml(item.id)}" aria-label="选择 ${escapeHtml(item.name)}"></td>
        <td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.retrieval_profile_version)}</td>
        <td><span class="tag">${escapeHtml(item.status)}</span></td>
        <td>${metric(item.metric_summary["recall@10"])}</td>
        <td>${metric(item.metric_summary["ndcg@10"])}</td>
        <td>${metric(item.metric_summary.p95_latency)}</td>
        <td>${metric(item.metric_summary.model_call_count)} / ${item.metric_summary.output_token_count ?? "—"}</td>
        <td><details><summary>${item.metric_summary.failed_case_runs ?? 0} case errors</summary>
          <small>类别：${compactMap(item.metric_summary.category_counts)}</small><br>
          <small>分类指标：${categoryMetricSummary(item.metric_summary.category_metrics)}</small><br>
          <small>失败：${compactMap(item.failure_summary.counts)}</small>
        </details></td>
        <td>${escapeHtml(item.metric_summary.langsmith_status || "—")}</td>
        <td><a class="button" href="/agent/rag-evaluation/experiments/${escapeHtml(item.id)}/report">查看</a></td>
      </tr>`).join("");
    $("#profiles").innerHTML = profiles.map((item) => `
      <div class="card">
        <span class="tag">${escapeHtml(item.is_active ? "active" : item.evaluation_status)}</span>
        <h3>${escapeHtml(item.version)}</h3>
        <p class="meta">${escapeHtml(item.configuration.chunking_version)} · ${escapeHtml(item.configuration.retrieval_mode)}</p>
        <p>${escapeHtml(item.description || "")}</p>
        <button data-run-profile="${escapeHtml(item.id)}">运行此 Profile</button>
        ${!item.is_active && (item.promotion_eligible || item.is_baseline) && item.evaluation_status === "completed"
          ? `<button data-activate-profile="${escapeHtml(item.id)}">激活</button>`
          : ""}
      </div>`).join("");
    bindActions();
    $("#status").textContent = "评估状态已更新。";
  } catch (error) {
    $("#status").textContent = error.message;
  }
}

$("#refresh").addEventListener("click", load);
$("#run-baseline").addEventListener("click", () => {
  const baseline = snapshot.profiles.find((item) => item.is_baseline);
  if (!baseline) {
    $("#status").textContent = "Baseline Profile 尚未建立。";
    return;
  }
  runProfile(baseline).catch((error) => {
    $("#status").textContent = error.message;
  });
});
$("#compare-selected").addEventListener("click", () => {
  compareSelected().catch((error) => {
    $("#status").textContent = error.message;
  });
});
load();
