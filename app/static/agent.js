"use strict";

const el = Object.fromEntries([
  "agentMessage","preferencesForm","preferredLanguage","answerDepth","answerStyle","citationMode","customInstructions",
  "previewSession","previewGoal","previewStatus","previewContextBtn","contextPreviewOutput","refreshRunsBtn","runsBody",
  "refreshObservabilityBtn","observabilityStatus",
  "refreshToolsBtn","toolsBody","toolTestForm","toolName","toolInput","toolOutput","refreshAuditsBtn","auditsBody",
  "refreshMcpServersBtn","mcpServersBody","refreshMcpToolsBtn","mcpToolsBody","mcpToolTestForm","mcpToolName","mcpToolInput","mcpToolOutput",
  "cleanupDays","cleanupPreviewBtn","cleanupApplyBtn","cleanupOutput"
].map((id) => [id, document.getElementById(id)]));

let lastCleanupPreview = null;
let tools = [];
let mcpTools = [];

function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]); }
function short(value, length=10) { const text=String(value ?? "-"); return text.length > length ? `${text.slice(0,length)}…` : text; }
function showMessage(message, ok=false) { el.agentMessage.hidden=!message; el.agentMessage.textContent=message || ""; el.agentMessage.classList.toggle("ok",ok); }
async function requestJson(url, options={}) {
  const headers={...(options.headers||{})}; if (options.body) headers["Content-Type"]="application/json";
  const response=await fetch(url,{...options,headers}); let body=null; try { body=await response.json(); } catch (_) { body=null; }
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail || body || `${response.status} ${response.statusText}`));
  return body;
}

async function loadPreferences() {
  const data=await requestJson("/agent/preferences");
  el.preferredLanguage.value=data.preferred_language; el.answerDepth.value=data.answer_depth; el.answerStyle.value=data.answer_style;
  el.citationMode.value=data.citation_mode; el.customInstructions.value=data.custom_instructions || "";
}
async function savePreferences(event) {
  event.preventDefault();
  const data=await requestJson("/agent/preferences",{method:"PATCH",body:JSON.stringify({preferred_language:el.preferredLanguage.value,answer_depth:el.answerDepth.value,answer_style:el.answerStyle.value,citation_mode:el.citationMode.value,custom_instructions:el.customInstructions.value.trim() || null})});
  showMessage(`偏好已显式保存：${data.updated_at || "default"}`,true);
}
async function loadSelectors() {
  const [sessions,goals,statuses]=await Promise.all([requestJson("/learning/sessions?status=active&limit=50"),requestJson("/goals"),requestJson("/learning-status")]);
  el.previewSession.innerHTML='<option value="">不选择</option>'+sessions.items.map((x)=>`<option value="${escapeHtml(x.id)}">${escapeHtml(x.title)}</option>`).join("");
  el.previewGoal.innerHTML='<option value="">不选择</option>'+goals.items.map((x)=>`<option value="${escapeHtml(x.id)}">${escapeHtml(x.name)}</option>`).join("");
  el.previewStatus.innerHTML='<option value="">不选择</option>'+statuses.items.map((x)=>`<option value="${escapeHtml(x.id)}">${escapeHtml(x.topic)}</option>`).join("");
}
async function previewContext() {
  const data=await requestJson("/agent/context/preview",{method:"POST",body:JSON.stringify({session_id:el.previewSession.value||null,goal_id:el.previewGoal.value||null,learning_status_id:el.previewStatus.value||null})});
  el.contextPreviewOutput.textContent=JSON.stringify(data,null,2);
}
async function loadObservability() {
  const data=await requestJson("/agent/observability/status");
  el.observabilityStatus.textContent=JSON.stringify(data,null,2);
}
async function loadRuns() {
  const data=await requestJson("/agent/runs?limit=20");
  el.runsBody.innerHTML=data.items.length ? data.items.map((x)=>`<tr><td title="${escapeHtml(x.id)}">${escapeHtml(short(x.id))}</td><td>${escapeHtml(x.run_type)}</td><td class="${x.status==='succeeded'?'ok':x.status==='failed'?'bad':''}">${escapeHtml(x.status)}</td><td>${escapeHtml(short(x.session_id))}</td><td>${x.rewrite_applied?'是':'否'}</td><td>${x.retrieved_count}/${x.source_count}</td><td>${x.model_call_count}/${x.tool_call_count}</td><td>${escapeHtml(x.observability_provider||'-')}</td><td title="${escapeHtml(x.external_trace_id||'')}">${escapeHtml(short(x.external_trace_id,12))}</td><td>${escapeHtml(x.observability_status||'-')}</td><td>${x.duration_ms ?? '-'} ms</td><td>${escapeHtml(x.error_code||'-')}</td><td>${new Date(x.created_at).toLocaleString()}</td></tr>`).join("") : '<tr><td colspan="13" class="muted">暂无运行记录。</td></tr>';
}
async function loadTools() {
  tools=await requestJson("/agent/tools");
  el.toolsBody.innerHTML=tools.map((x)=>`<tr><td>${escapeHtml(x.name)}</td><td>${escapeHtml(x.description)}</td><td>${escapeHtml(x.risk_level)}</td><td>${x.enabled?'是':'否'}</td><td>${x.idempotent?'是':'否'}</td><td>${escapeHtml(x.allowed_callers.join(', '))}</td></tr>`).join("");
  el.toolName.innerHTML=tools.filter((x)=>x.enabled&&x.risk_level==='READ_ONLY').map((x)=>`<option value="${escapeHtml(x.name)}">${escapeHtml(x.name)}</option>`).join("");
}
async function executeTool(event) {
  event.preventDefault(); let input; try { input=JSON.parse(el.toolInput.value||"{}"); } catch (_) { throw new Error("输入必须是有效 JSON 对象。"); }
  const data=await requestJson(`/agent/tools/${encodeURIComponent(el.toolName.value)}/execute`,{method:"POST",body:JSON.stringify({input})});
  el.toolOutput.textContent=JSON.stringify(data,null,2); await loadAudits();
}
async function loadMcpServers() {
  const data=await requestJson("/agent/mcp/servers");
  el.mcpServersBody.innerHTML=data.servers.length ? data.servers.map((x)=>`<tr><td>${escapeHtml(x.name)}</td><td>${escapeHtml(x.transport)}</td><td>${x.enabled?'是':'否'}</td><td class="${x.status==='connected'?'ok':x.status==='error'?'bad':''}">${escapeHtml(x.status)}</td><td>${x.tool_count}</td><td>${x.allowed_tool_count}</td><td>${escapeHtml(x.last_error_code||'-')}</td><td class="actions"><button type="button" data-mcp-action="connect" data-server="${escapeHtml(x.name)}">连接</button><button type="button" data-mcp-action="refresh-tools" data-server="${escapeHtml(x.name)}">刷新工具</button><button type="button" class="secondary" data-mcp-action="disconnect" data-server="${escapeHtml(x.name)}">断开</button></td></tr>`).join("") : `<tr><td colspan="8" class="muted">MCP ${escapeHtml(data.configuration_status)}；暂无已配置 Server。</td></tr>`;
}
async function mcpServerAction(event) {
  const button=event.target.closest("button[data-mcp-action]"); if (!button) return;
  await requestJson(`/agent/mcp/servers/${encodeURIComponent(button.dataset.server)}/${button.dataset.mcpAction}`,{method:"POST"});
  await Promise.all([loadMcpServers(),loadMcpTools(),loadTools()]);
}
async function loadMcpTools() {
  const data=await requestJson("/agent/mcp/tools"); mcpTools=data.items;
  el.mcpToolsBody.innerHTML=mcpTools.length ? mcpTools.map((x)=>`<tr><td>${escapeHtml(x.namespaced_name)}</td><td>${escapeHtml(x.remote_tool_name)}</td><td>${escapeHtml(x.server_name)}</td><td>${escapeHtml(x.description)}</td><td>${escapeHtml(x.risk_level)}</td><td>${x.allowed?'是':'否'}</td><td>${x.connected?'是':'否'}</td><td title="${escapeHtml(x.schema_hash)}">${escapeHtml(short(x.schema_hash,12))}</td></tr>`).join("") : '<tr><td colspan="8" class="muted">尚未发现 MCP Tool。</td></tr>';
  el.mcpToolName.innerHTML=mcpTools.filter((x)=>x.allowed&&x.connected&&x.risk_level==='READ_ONLY').map((x)=>`<option value="${escapeHtml(x.namespaced_name)}">${escapeHtml(x.namespaced_name)}</option>`).join("");
}
async function executeMcpTool(event) {
  event.preventDefault(); let input; try { input=JSON.parse(el.mcpToolInput.value||"{}"); } catch (_) { throw new Error("输入必须是有效 JSON 对象。"); }
  if (!el.mcpToolName.value) throw new Error("没有已连接且允许的 MCP Tool。");
  const data=await requestJson(`/agent/tools/${encodeURIComponent(el.mcpToolName.value)}/execute`,{method:"POST",body:JSON.stringify({input})});
  el.mcpToolOutput.textContent=JSON.stringify(data,null,2); await loadAudits();
}
async function loadAudits() {
  const data=await requestJson("/agent/tool-executions?limit=20");
  el.auditsBody.innerHTML=data.items.length ? data.items.map((x)=>`<tr><td>${escapeHtml(x.tool_name)}</td><td>${escapeHtml(x.tool_source)}</td><td>${escapeHtml(x.provider_name||'-')}</td><td>${escapeHtml(x.remote_tool_name||'-')}</td><td>${escapeHtml(x.risk_level||'-')}</td><td>${escapeHtml(x.decision)}</td><td>${escapeHtml(x.status)}</td><td title="${escapeHtml(x.input_hash)}">${escapeHtml(short(x.input_hash,12))}</td><td><code>${escapeHtml(JSON.stringify(x.input_summary))}</code></td><td>${x.duration_ms ?? '-'} ms</td><td>${escapeHtml(x.error_code||'-')}</td></tr>`).join("") : '<tr><td colspan="11" class="muted">暂无工具审计。</td></tr>';
}
async function cleanup(dryRun) {
  const days=Number(el.cleanupDays.value); if (!Number.isInteger(days)||days<1) throw new Error("清理天数不得小于 1。");
  if (!dryRun && (!lastCleanupPreview || lastCleanupPreview.older_than_days!==days)) throw new Error("请先用相同天数执行 Dry Run。");
  const data=await requestJson("/agent/maintenance/cleanup-history",{method:"POST",body:JSON.stringify({dry_run:dryRun,older_than_days:days})});
  el.cleanupOutput.textContent=JSON.stringify(data,null,2); lastCleanupPreview=dryRun?data:null; el.cleanupApplyBtn.disabled=!dryRun;
  if (!dryRun) { el.cleanupApplyBtn.disabled=true; await Promise.all([loadRuns(),loadAudits()]); }
}
async function guarded(fn) { try { showMessage(""); await fn(); } catch (error) { showMessage(error.message); } }

el.preferencesForm.addEventListener("submit",(event)=>guarded(()=>savePreferences(event)));
el.previewContextBtn.addEventListener("click",()=>guarded(previewContext)); el.refreshRunsBtn.addEventListener("click",()=>guarded(loadRuns));
el.refreshObservabilityBtn.addEventListener("click",()=>guarded(loadObservability));
el.refreshToolsBtn.addEventListener("click",()=>guarded(loadTools)); el.toolTestForm.addEventListener("submit",(event)=>guarded(()=>executeTool(event)));
el.refreshMcpServersBtn.addEventListener("click",()=>guarded(loadMcpServers)); el.mcpServersBody.addEventListener("click",(event)=>guarded(()=>mcpServerAction(event)));
el.refreshMcpToolsBtn.addEventListener("click",()=>guarded(loadMcpTools)); el.mcpToolTestForm.addEventListener("submit",(event)=>guarded(()=>executeMcpTool(event)));
el.refreshAuditsBtn.addEventListener("click",()=>guarded(loadAudits)); el.cleanupPreviewBtn.addEventListener("click",()=>guarded(()=>cleanup(true)));
el.cleanupApplyBtn.addEventListener("click",()=>guarded(()=>cleanup(false)));

guarded(async()=>{ await Promise.all([loadPreferences(),loadSelectors(),loadObservability(),loadRuns(),loadTools(),loadMcpServers(),loadMcpTools(),loadAudits()]); });
