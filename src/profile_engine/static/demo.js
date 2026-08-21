const appState = {
  code: "", actor: "系统管理员", people: [], person: null, profile: null,
  conversations: [], conversationId: "", messages: [], version: 1,
  lastEngine: null, busy: false, nearBottom: true, audit: null,
  expertReference: null, modelProvider: "deepseek", modelOptions: []
};
const ACCESS_CODE_KEY = "profile-engine-access-code";
const MODEL_PROVIDER_KEY = "profile-engine-model-provider";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[c]));
const traitLabels = {
  extroversion: "外向性", social_warmth: "社交温度", assertiveness: "果断性", impulsivity: "冲动性",
  openness: "开放性", creativity: "创造力", depth_of_thought: "思考深度", thinking_ratio: "理性决策",
  empathy: "共情能力", risk_tolerance: "风险容忍度", structure_pref: "结构化偏好", discipline: "自律性",
  adaptability: "适应性", persistence: "坚持度", confidence: "自信度", optimism: "乐观度",
  romantic_orientation: "关系投入"
};
const categoryLabels = {
  energy_mode: "能量模式", cognition_mode: "认知模式", decision_mode: "决策模式",
  action_mode: "行动模式", self_perception: "自我感受", relationship_mode: "关系方式",
  self_system: "自我感受", emotion_relation_mode: "情绪与关系"
};
const scenarioLabels = {
  task_received: "接到任务", task_progress: "推进任务", obstacle: "遇到阻碍", decision: "做决定",
  being_urged: "被催促", after_error: "出错之后", facing_change: "面对变化",
  first_meeting: "初次见面", familiar_relationship: "熟悉关系", helping_others: "帮助他人",
  being_needed: "被需要", being_misunderstood: "被误解", conflict: "发生冲突",
  romantic_interaction: "亲密互动", confidence_state: "自信状态", optimism_state: "看待未来",
  stress_response: "压力反应", energy_source: "精力恢复"
};
const preferenceLabels = {
  response_length: "回答长度", directness: "表达直接程度", empathy_first: "倾听与建议顺序",
  question_load: "追问密度", humor_level: "幽默程度"
};
const stateLabels = {emotion: "当前情绪", stress_level: "当前压力", energy_level: "当前精力"};
const evidenceSourceLabels = {
  explicit_self_report: "本人明确表达", repeated_behavior: "跨轮次重复观察",
  single_behavior_inference: "单次行为观察", explicit_correction: "本人更正",
  manual_expert_override: "人工确认"
};
const auditActionLabels = {
  "profile.init": "建立画像", "message.ingest": "整理本轮对话",
  "profile.correct": "本人更正", "profile.manual_override": "人工确认",
  "profile.enneagram.set": "更新授权参考", "profile.forget.memory": "删除记忆",
  "profile.forget.evidence": "撤回依据", "profile.forget.birth_inference": "清除出生信息先验",
  "profile.forget.enneagram": "清除类型参考", "profile.forget.all_profile": "清除全部画像"
};
const predicateLabels = {
  socializing_requires_solitude_recovery: "社交后需要独处恢复", likes_social_gathering: "喜欢社交聚会",
  prefers_planning: "偏好制定计划", uses_data_for_decisions: "用数据辅助决定",
  needs_empathy_before_advice: "希望先共情再建议", prefers_short_responses: "偏好简短回复",
  low_energy: "当前精力较低", high_stress: "当前压力较高", education_institution: "就读学校",
  name: "姓名", event: "重要事件"
};
const roleLabels = { admin: "管理员", reviewer: "审核人", expert: "画像专家", viewer: "只读成员" };

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2400);
}

async function api(path, options = {}) {
  const headers = {
    "X-Demo-Code": appState.code,
    ...(options.body ? {"Content-Type": "application/json"} : {}),
    ...(options.headers || {})
  };
  const response = await fetch(path, {...options, headers});
  let payload = {};
  try { payload = await response.json(); } catch {}
  if (!response.ok) {
    let message = payload.detail || payload.message || `请求失败（${response.status}）`;
    if (typeof message === "object") message = message.message || JSON.stringify(message);
    const error = new Error(message);
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function initials(name) {
  return String(name || "人").trim().slice(-1);
}

async function bootstrap() {
  const data = await api("/demo/api/workspace/bootstrap", {method: "POST", body: "{}"});
  sessionStorage.setItem(ACCESS_CODE_KEY, appState.code);
  appState.people = data.people;
  renderModelOptions(data.model_config);
  $("#actorName").textContent = data.actor.display_name;
  $("#actorRole").textContent = roleLabels[data.actor.role] || data.actor.role;
  $("#actorAvatar").textContent = initials(data.actor.display_name);
  renderPeople();
  $("#accessGate").classList.add("hidden");
  if (data.people.length) await selectPerson(data.people[0].user_id);
}

function renderModelOptions(config = {}) {
  appState.modelOptions = config.options || [];
  const saved = localStorage.getItem(MODEL_PROVIDER_KEY);
  const available = appState.modelOptions.filter(item => item.available);
  const selected = appState.modelOptions.find(item => item.provider === saved && item.available)
    || appState.modelOptions.find(item => item.provider === config.default_provider && item.available)
    || available[0]
    || appState.modelOptions.find(item => item.provider === config.default_provider)
    || appState.modelOptions[0];
  appState.modelProvider = selected?.provider || "deepseek";
  const select = $("#modelProviderSelect");
  select.innerHTML = appState.modelOptions.map(item =>
    `<option value="${esc(item.provider)}" ${item.provider === appState.modelProvider ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.label)} · ${esc(item.model)}${item.available ? "" : "（未配置）"}</option>`
  ).join("");
  select.title = selected ? `${selected.label} · ${selected.route} · ${selected.model}` : "没有可用模型";
}

function renderPeople(filter = "") {
  const term = filter.trim().toLowerCase();
  const items = appState.people.filter(person => !term || person.display_name.toLowerCase().includes(term));
  $("#peopleList").innerHTML = items.length ? items.map(person => `
    <button class="person-item ${appState.person?.user_id === person.user_id ? "active" : ""}" data-person-id="${esc(person.user_id)}">
      <span class="avatar">${esc(initials(person.display_name))}</span>
      <span><b>${esc(person.display_name)}</b><small>画像 v${person.profile_version} · ${person.conversation_count} 段对话</small></span>
    </button>`).join("") : `<div class="empty-panel"><span>⌕</span><b>没有找到人物</b><p>换个名字搜索试试。</p></div>`;
  $$(".person-item").forEach(button => button.onclick = () => selectPerson(button.dataset.personId));
}

async function refreshPeople(keepSelected = true) {
  const selected = keepSelected ? appState.person?.user_id : null;
  const data = await api("/demo/api/people");
  appState.people = data.people;
  renderPeople($("#peopleSearch").value);
  if (selected) {
    const latest = appState.people.find(person => person.user_id === selected);
    if (latest) appState.person = latest;
  }
}

async function selectPerson(userId) {
  if (appState.busy) return;
  const detail = await api(`/demo/api/people/${encodeURIComponent(userId)}`);
  appState.person = detail.person;
  appState.profile = detail.profile;
  appState.version = detail.profile_version;
  appState.conversations = detail.conversations;
  appState.audit = null;
  appState.expertReference = null;
  appState.lastEngine = null;
  $("#personName").textContent = detail.person.display_name;
  $("#personAvatar").textContent = initials(detail.person.display_name);
  $("#personMeta").textContent = `画像 v${detail.profile_version} · 证据${detail.profile.summary?.evidence_level || "待积累"}`;
  $("#inspectorSubtitle").textContent = `${detail.person.display_name} · 当前画像 v${detail.profile_version}`;
  $("#messageInput").disabled = false;
  $("#sendBtn").disabled = false;
  renderPeople($("#peopleSearch").value);
  renderConversationOptions();
  renderProfile(detail.manual_overrides || []);
  renderTurn();
  renderAuditPlaceholder();
  renderExpertPlaceholder();
  document.querySelector(".people-rail").classList.remove("open");
  if (detail.conversations.length) await selectConversation(detail.conversations[0].conversation_id);
  else await createConversation();
}

function renderConversationOptions() {
  const select = $("#conversationSelect");
  select.innerHTML = appState.conversations.map(item =>
    `<option value="${esc(item.conversation_id)}" ${item.conversation_id === appState.conversationId ? "selected" : ""}>${esc(item.title)}</option>`
  ).join("");
}

async function createConversation() {
  if (!appState.person) return toast("请先选择人物");
  const data = await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}/conversations`, {
    method: "POST", body: JSON.stringify({title: `新对话 · ${new Date().toLocaleDateString("zh-CN", {month: "numeric", day: "numeric"})}`})
  });
  appState.conversations.unshift(data.conversation);
  renderConversationOptions();
  await selectConversation(data.conversation.conversation_id);
  toast("已新建独立对话");
}

async function selectConversation(conversationId) {
  if (!appState.person || !conversationId) return;
  const data = await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}/conversations/${encodeURIComponent(conversationId)}/messages`);
  appState.conversationId = conversationId;
  appState.messages = data.messages.map(item => ({role: item.role, content: item.content, engine: item.engine_trace}));
  const matched = appState.conversations.find(item => item.conversation_id === conversationId);
  if (matched) $("#conversationSelect").value = conversationId;
  renderMessages();
  const lastEngineMessage = [...data.messages].reverse().find(item => item.engine_trace);
  appState.lastEngine = lastEngineMessage?.engine_trace || null;
  renderTurn();
}

function renderMessages() {
  const container = $("#messages");
  if (!appState.messages.length) {
    container.innerHTML = `<div class="conversation-empty"><span class="empty-orbit">◌</span><h2>从这一刻开始记录</h2><p>这段对话拥有独立的短期上下文，同时会继承 ${esc(appState.person?.display_name)} 已形成的长期画像。</p></div>`;
  } else {
    container.innerHTML = appState.messages.map(item => messageMarkup(item.role, item.content)).join("");
  }
  forceScrollBottom();
}

function messageMarkup(role, content, id = "") {
  return `<div class="message-row ${role}" ${id ? `id="${id}"` : ""}>${role === "assistant" ? `<span class="message-avatar">伴</span>` : ""}<div class="message-bubble">${esc(content)}</div></div>`;
}

function isNearBottom() {
  const node = $("#messages");
  return node.scrollHeight - node.scrollTop - node.clientHeight < 90;
}

function forceScrollBottom() {
  requestAnimationFrame(() => {
    const node = $("#messages");
    node.scrollTop = node.scrollHeight;
    appState.nearBottom = true;
    $("#jumpLatestBtn").classList.remove("show");
  });
}

function appendMessage(role, content, id = "") {
  const container = $("#messages");
  const shouldFollow = appState.nearBottom;
  $(".conversation-empty", container)?.remove();
  container.insertAdjacentHTML("beforeend", messageMarkup(role, content, id));
  if (shouldFollow) forceScrollBottom();
  else $("#jumpLatestBtn").classList.add("show");
}

function setTyping(show) {
  $("#typing")?.remove();
  if (show) {
    const shouldFollow = appState.nearBottom;
    $("#messages").insertAdjacentHTML("beforeend", `<div class="message-row assistant" id="typing"><span class="message-avatar">伴</span><div class="message-bubble"><div class="typing-dots"><i></i><i></i><i></i></div></div></div>`);
    if (shouldFollow) forceScrollBottom(); else $("#jumpLatestBtn").classList.add("show");
  }
}

async function sendMessage() {
  const input = $("#messageInput");
  const text = input.value.trim();
  if (!text || appState.busy || !appState.person || !appState.conversationId) return;
  appState.busy = true;
  $("#sendBtn").disabled = true;
  input.value = "";
  input.style.height = "auto";
  const messageId = crypto.randomUUID();
  appendMessage("user", text);
  appState.messages.push({role: "user", content: text});
  setTyping(true);
  renderTurn("processing");
  try {
    const history = appState.messages.slice(-12).map(({role, content}) => ({role, content}));
    const data = await api("/demo/api/chat", {
      method: "POST",
      body: JSON.stringify({
        user_id: appState.person.user_id,
        conversation_id: appState.conversationId,
        message_id: messageId,
        expected_profile_version: appState.version,
        text, history,
        model_provider: appState.modelProvider
      })
    });
    setTyping(false);
    appendMessage("assistant", data.assistant_reply);
    appState.messages.push({role: "assistant", content: data.assistant_reply, engine: data.engine});
    appState.version = data.engine.profile_version;
    appState.lastEngine = data.engine;
    renderTurn();
    await reloadProfile();
    await refreshPeople();
  } catch (error) {
    setTyping(false);
    if (error.payload?.code === "model_no_response") {
      const details = error.payload.details || {};
      appendMessage("system", error.message);
      appState.version = details.profile_version || appState.version;
      appState.lastEngine = details.engine || appState.lastEngine;
      renderTurn();
      await reloadProfile();
      await refreshPeople();
      toast("模型无返回");
    } else {
      appendMessage("system", `本轮处理失败：${error.message}`);
      toast(error.message);
    }
  } finally {
    appState.busy = false;
    $("#sendBtn").disabled = false;
    input.focus();
  }
}

async function reloadProfile() {
  if (!appState.person) return;
  const detail = await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}`);
  appState.person = detail.person;
  appState.profile = detail.profile;
  appState.version = detail.profile_version;
  appState.audit = null;
  appState.expertReference = null;
  $("#personMeta").textContent = `画像 v${detail.profile_version} · 证据${detail.profile.summary?.evidence_level || "待积累"}`;
  $("#inspectorSubtitle").textContent = `${detail.person.display_name} · 当前画像 v${detail.profile_version}`;
  renderProfile(detail.manual_overrides || []);
  renderExpertPlaceholder();
}

function certaintyLabel(value) {
  if (value >= .85) return "较明确";
  if (value >= .65) return "可参考";
  return "待核验";
}

function legacyUpdateSummary(engine) {
  const patches = engine.profile_patch || [];
  const operations = engine.runtime_operations || [];
  return {
    status: patches.length || operations.length ? "updated" : "unchanged",
    headline: patches.length || operations.length ? `本轮写入 ${patches.length + operations.length} 项变化` : "本轮未修改画像",
    change_count: patches.length + operations.length,
    items: [
      ...patches.map(item => ({
        label: traitLabels[item.field.split(".").pop()] || item.field,
        action: item.after >= item.before ? "小幅上调" : "小幅下调",
        why: "本轮候选通过了画像规则校验",
        how: "按照单轮变化上限小步写入。",
      })),
      ...operations.map(item => ({
        label: preferenceLabels[item.field] || stateLabels[item.field] || item.key || "重要信息",
        action: "已记录",
        why: "用户在本轮明确表达了这项信息",
        how: "按事实、互动偏好或短期状态分别保存。",
      })),
    ],
    rejected: [], maintenance: [], derived_effects: [],
    no_change_reason: "没有发现证据足够、需要长期保存的用户本人信息。",
    guardrail_note: "只依据用户本人原话更新。",
  };
}

function renderTurn(mode = "ready") {
  const view = $("#turnView");
  if (mode === "processing") {
    view.innerHTML = `<div class="flow-track"><span class="done">识别原话</span><i>→</i><span class="done">形成候选</span><i>→</i><span>证据校验</span><i>→</i><span>生成建议</span></div><div class="empty-panel"><span>◌</span><b>正在核对本轮画像建议</b><p>系统会分别判断事实、偏好、短期状态和稳定行为证据。</p></div>`;
    return;
  }
  const engine = appState.lastEngine;
  if (!engine) {
    view.innerHTML = `<div class="empty-panel"><span>◎</span><b>等待这一轮对话</b><p>对话结束后，这里会说明更新了什么、为什么更新，以及哪些候选没有写入。</p></div>`;
    return;
  }
  const frames = engine.semantic_frames || [];
  const hints = engine.reply_hints || {};
  const summary = engine.update_summary || legacyUpdateSummary(engine);
  const statusLabel = {updated: "已更新", observed: "已补充样本", unchanged: "保持不变"}[summary.status] || "已判断";
  view.innerHTML = `
    <div class="flow-track"><span class="done">识别原话</span><i>→</i><span class="done">形成候选</span><i>→</i><span class="done">证据校验</span><i>→</i><span class="done">生成建议</span></div>
    <div class="turn-summary ${esc(summary.status)}">
      <span class="turn-status-dot"></span>
      <div><small>本轮画像建议 · ${esc(statusLabel)}</small><b>${esc(summary.headline)}</b><p>${esc(summary.guardrail_note || "")}</p></div>
    </div>
    <div class="profile-overview compact-stats">
      <div class="profile-stat"><small>画像版本</small><b>v${engine.strategy_trace?.profile_version_used || engine.profile_version}</b></div>
      <div class="profile-stat"><small>实际写入</small><b>${summary.change_count || 0}</b></div>
      <div class="profile-stat"><small>通过候选</small><b>${engine.strategy_trace?.accepted_signals || 0}</b></div>
      <div class="profile-stat"><small>过滤候选</small><b>${(summary.rejected || []).length}</b></div>
    </div>
    <div class="section-heading"><b>当前更新了什么</b><small>${(summary.items || []).length} 项</small></div>
    ${(summary.items || []).length ? summary.items.map(item => `
      <article class="change-card">
        <div class="change-card-head"><b>${esc(item.label)}</b><span class="tag gold">${esc(item.action)}</span></div>
        ${item.evidence_quote ? `<div class="evidence-quote">“${esc(item.evidence_quote)}”</div>` : ""}
        <dl><dt>为什么</dt><dd>${esc(item.why)}</dd><dt>怎么更新</dt><dd>${esc(item.how)}</dd></dl>
      </article>`).join("") : `<div class="inspector-card no-change-card"><div class="inspector-card-title"><b>没有写入新的画像结论</b><span class="tag gray">审慎模式</span></div><p>${esc(summary.no_change_reason || "本轮证据不足。")}</p></div>`}
    ${(summary.maintenance || []).length ? `<div class="maintenance-note"><b>同时补充</b><span>${summary.maintenance.map(esc).join("、")}</span><small>观察样本会先积累，达到跨轮次门槛后才形成稳定结论。</small></div>` : ""}
    ${(summary.rejected || []).length ? `<details class="turn-details"><summary>查看未写入的候选 <span>${summary.rejected.length}</span></summary>${summary.rejected.map(item => `<div class="rejected-item"><b>${esc(item.label)}</b><p>${esc(item.why)}</p>${item.evidence_quote ? `<small>依据：“${esc(item.evidence_quote)}”</small>` : ""}</div>`).join("")}</details>` : ""}
    ${(summary.derived_effects || []).length ? `<div class="derived-note"><b>同步整理</b><span>${summary.derived_effects.map(esc).join("、")}</span></div>` : ""}
    <details class="turn-details"><summary>系统理解依据 <span>${frames.length}</span></summary>
      ${frames.length ? frames.map(frame => `<div class="inspector-card compact-card"><div class="inspector-card-title"><span><b>${esc(predicateLabels[frame.predicate] || frame.predicate)}</b><small>${frame.subject === "user" ? "用户本人" : esc(frame.subject)} · ${esc(frame.temporal_scope)}</small></span><span class="tag">${certaintyLabel(frame.extractor_confidence || 0)}</span></div><div class="evidence-quote">“${esc(frame.supporting_span)}”</div></div>`).join("") : `<p class="muted-copy">没有抽取需要维护的个人信息。</p>`}
    </details>
    <div class="section-heading"><b>本轮回答方式</b><small>${engine.strategy_trace?.consumed_by_chatbot ? "已应用" : "待应用"}</small></div>
    <div class="inspector-card"><div class="inspector-card-title"><b>${esc(hints.focus || "回应当前消息")}</b><span class="tag">${engine.strategy_trace?.consumed_by_chatbot ? "已应用" : "待应用"}</span></div>
      <div class="data-grid"><label>语气</label><span>${esc(hints.tone || "自然")}</span><label>长度</label><span>最多 ${hints.max_sentences || 4} 句</span><label>追问</label><span>${hints.question_count ?? 0} 个</span><label>组织方式</label><span>${({simple:"简洁",steps:"分步骤",flexible_options:"灵活选项"})[hints.structure_level] || "自然"}</span></div>
    </div>`;
}

function flattenTraits(profile) {
  return Object.entries(profile?.core_traits || {}).flatMap(([category, traits]) =>
    Object.entries(traits).map(([key, value]) => ({category, key, ...value}))
  );
}

function sourceSheetMarkup(name, rows) {
  const visibleRows = (rows || []).filter(row => row.some(value => value !== null && value !== ""));
  const width = Math.max(1, ...visibleRows.map(row => row.length));
  return `<details class="source-sheet">
    <summary>${esc(name)}<small>${visibleRows.length} 行原始内容</small></summary>
    <div class="source-table-wrap"><table>${visibleRows.map(row => {
      const values = [...row, ...Array(Math.max(0, width - row.length)).fill(null)];
      const nonempty = values.filter(value => value !== null && value !== "");
      if (nonempty.length === 1 && values[0] !== null) {
        return `<tr class="source-heading"><th colspan="${width}">${esc(values[0])}</th></tr>`;
      }
      return `<tr>${values.map(value => `<td>${esc(value ?? "")}</td>`).join("")}</tr>`;
    }).join("")}</table></div>
  </details>`;
}

function preferenceValueLabel(key, value) {
  if (key === "response_length") return value === "short" ? "偏好简短" : esc(value);
  if (key === "empathy_first") return Number(value) >= .67 ? "先倾听，再给建议" : "自然回应";
  if (key === "humor_level") return Number(value) <= .2 ? "减少幽默" : "可适度幽默";
  if (key === "directness") return value === "direct" ? "先说结论" : esc(value);
  if (key === "question_load") return value === "low" ? "减少追问" : esc(value);
  return esc(value);
}

function stateValueLabel(key, value) {
  if (key === "stress_level") return Number(value) >= .7 ? "较高" : "一般";
  if (key === "energy_level") return Number(value) <= .3 ? "较低" : "一般";
  return ({positive:"较好",low:"低落",angry:"生气"})[value] || String(value ?? "已记录");
}

function renderProfile(overrides = []) {
  const view = $("#profileView");
  const profile = appState.profile;
  if (!profile) {
    view.innerHTML = `<div class="empty-panel"><span>◌</span><b>尚未选择人物</b></div>`;
    return;
  }
  const locked = new Set(overrides.map(item => item.target_path));
  const categories = Object.entries(profile.stable_tendencies || {});
  const memories = profile.facts_and_memories || [];
  const prefs = profile.interaction?.preferences || {};
  const states = profile.interaction?.current_state || {};
  const scenarios = Object.entries(profile.scenario_observations || {}).flatMap(([group, items]) =>
    Object.entries(items).map(([key, value]) => ({group, key, ...value}))
  );
  const communication = profile.communication_observations || [];
  const summary = profile.summary || {};
  const hasInteraction = Object.keys(prefs).length || Object.keys(states).length;
  view.innerHTML = `
    <div class="profile-overview">
      <div class="profile-stat"><small>画像版本</small><b>v${profile.meta?.profile_version || appState.version}</b></div>
      <div class="profile-stat"><small>证据状态</small><b class="stat-word">${esc(summary.evidence_level || "待积累")}</b></div>
      <div class="profile-stat"><small>已有依据维度</small><b>${summary.observed_dimensions || 0}<em> / ${summary.total_dimensions || 17}</em></b></div>
      <div class="profile-stat"><small>事实与记忆</small><b>${memories.length}</b></div>
    </div>
    <section class="portrait-summary-card"><small>当前整体观察</small><p>${esc(summary.overall_observation || "对话证据仍在积累。")}</p><span>行为观察，不是诊断或固定人格标签</span></section>

    <div class="trait-section">
      <div class="section-heading"><b>当前互动建议</b><small>明确偏好优先 · 状态会过期</small></div>
      ${hasInteraction ? `<div class="inspector-card"><div class="data-grid public-data-grid">
        ${Object.entries(prefs).map(([key,value]) => `<label>${esc(preferenceLabels[key] || key)}</label><span>${preferenceValueLabel(key,value)} <i class="basis-badge confirmed">本人明确</i></span>`).join("")}
        ${Object.entries(states).map(([key,item]) => `<label>${esc(stateLabels[key] || key)}</label><span>${esc(stateValueLabel(key,item.value))} <i class="basis-badge emerging">短期</i><small>有效至 ${esc(item.expires_at?.slice(0,16).replace("T"," ") || "自动失效")}</small></span>`).join("")}
      </div></div>` : `<div class="inspector-card empty-evidence"><b>暂时没有特别的互动要求</b><small>用户明确提出长度、直接程度、倾听顺序或追问偏好后，会立即显示在这里。</small></div>`}
    </div>

    <div class="trait-section">
      <div class="section-heading"><b>稳定行为倾向</b><small>按证据充分度分层</small></div>
      ${categories.map(([category, traits]) => `<details class="profile-group" open><summary><b>${esc(categoryLabels[category] || category)}</b><span>${Object.values(traits).filter(item => item.evidence_grade !== "unverified").length} 项已有依据</span></summary>
        <div class="profile-group-body">${Object.entries(traits).map(([key,item]) => `
          <div class="public-trait-row ${esc(item.evidence_grade)}" title="${esc(item.evidence_count)} 条非先验证据">
            <span><b>${esc(item.label || traitLabels[key] || key)}</b><small>${esc(item.tendency)}</small></span>
            <div class="public-trait-track"><i class="midline"></i><span style="left:${item.position || 50}%"></span></div>
            <i class="basis-badge ${esc(item.evidence_grade)}">${esc(item.evidence_grade_label)}</i>
            <button class="edit-trait" data-path="${esc(item.editable_path)}" data-label="${esc(item.label || traitLabels[key] || key)}" aria-label="编辑${esc(item.label || traitLabels[key] || key)}">${locked.has(item.editable_path) ? "◆" : "✎"}</button>
          </div>`).join("")}</div>
      </details>`).join("")}
    </div>

    <div class="trait-section">
      <div class="section-heading"><b>场景表现</b><small>工作、关系与压力情境</small></div>
      ${scenarios.length ? scenarios.map(item => `<div class="observation-card"><div><b>${esc(scenarioLabels[item.key] || item.key)}</b><i class="basis-badge ${esc(item.evidence_grade)}">${esc(item.evidence_grade_label)}</i></div><p>${esc(item.observation)}</p><small>${esc(item.basis)}${item.evidence_count ? ` · ${item.evidence_count} 条直接依据` : ""}</small></div>`).join("") : `<div class="inspector-card empty-evidence"><b>场景证据仍在积累</b><small>只有直接观察或已有行为证据支持的场景才会出现在这里。</small></div>`}
    </div>

    <div class="trait-section">
      <div class="section-heading"><b>表达与沟通特点</b><small>至少3次同类样本后展示</small></div>
      ${communication.length ? communication.map(item => `<div class="observation-card"><div><b>${esc(item.label)}</b><i class="basis-badge ${esc(item.evidence_grade)}">${esc(item.evidence_grade_label)}</i></div><small>${item.sample_count} 次同类表达样本</small></div>`).join("") : `<div class="inspector-card empty-evidence"><b>暂未形成稳定表达观察</b><small>单次措辞不会被直接定性。</small></div>`}
    </div>

    <div class="trait-section">
      <div class="section-heading"><b>事实与重要记忆</b><small>${memories.length} 条</small></div>
      <div class="inspector-card"><div class="data-grid"><label>称呼</label><span>${esc(profile.identity?.display_name || "未填写")}</span><label>时区</label><span>${esc(profile.identity?.timezone || "未填写")}</span></div></div>
      ${memories.length ? memories.map(item => `<div class="memory-item"><b>${esc(item.key || item.type || "重要信息")}</b><p>${esc(item.value || item.summary || item.predicate || "")}</p><small>${item.type === "event" ? "事件记录" : "用户明确事实"}</small></div>`).join("") : `<div class="inspector-card empty-evidence"><small>尚未记录其他长期事实或重要事件。</small></div>`}
    </div>

    <div class="trait-section">
      <div class="section-heading"><b>依据与变化</b><small>可追溯 · 可更正</small></div>
      <div class="inspector-card evidence-policy"><p>默认画像只展示本人明确表达、对话观察和可更正事实。出生信息先验、类型框架和原始资料已移至“专家参考”，不会作为科学事实展示，也不会直接决定回答。</p><small>最近整理：${esc(profile.meta?.updated_at?.replace("T"," ").slice(0,19) || "—")}</small></div>
    </div>`;
  $$(".edit-trait", view).forEach(button => button.onclick = () =>
    openEdit(button.dataset.path, button.dataset.label).catch(error => toast(error.message))
  );
}

async function openEdit(path, label) {
  const reference = await loadExpertReference(false);
  const parts = path.split(".");
  let entry = reference.profile;
  for (const part of parts) entry = entry?.[part];
  const value = Number(entry?.value ?? .5);
  const form = $("#editForm");
  form.elements.target_path.value = path;
  form.elements.value.value = value;
  form.elements.reason.value = "";
  $("#editValueOutput").textContent = Number(value).toFixed(2);
  $("#editModalTitle").textContent = `调整${label}`;
  $("#editFieldDescription").textContent = `当前值 ${Number(value).toFixed(2)}。保存后生成新版本，并以人工更正优先于模型推断。`;
  $("#editModal").classList.remove("hidden");
}

async function openEnneagram() {
  const reference = await loadExpertReference(false);
  const form = $("#enneagramForm");
  const identity = reference.profile?.enneagram_profile?.identity || {};
  form.elements.core_type.value = identity.core_type || 7;
  form.elements.wing.value = identity.wing || "";
  form.elements.stack.value = identity.instinct_stack || "SX/SO";
  form.elements.source.value = reference.profile?.enneagram_profile?.source || "expert_confirmed";
  form.elements.reason.value = "";
  $("#enneagramModal").classList.remove("hidden");
}

function renderExpertPlaceholder() {
  $("#expertView").innerHTML = `<div class="empty-panel"><span>⌁</span><b>授权内部参考</b><p>这里单独存放类型框架、出生信息先验和原始资料。默认画像不会加载或展示这些内容。</p></div>`;
}

async function loadExpertReference(shouldRender = true) {
  if (!appState.person) throw new Error("请先选择人物");
  if (!appState.expertReference) {
    if (shouldRender) $("#expertView").innerHTML = `<div class="empty-panel"><span>◌</span><b>正在加载授权参考</b></div>`;
    appState.expertReference = await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}/expert-reference`);
  }
  if (shouldRender) renderExpert();
  return appState.expertReference;
}

function renderExpert() {
  const data = appState.expertReference;
  if (!data) return renderExpertPlaceholder();
  const profile = data.profile || {};
  const digital = profile.digital_code_profile || {status:"unassigned",domains:{}};
  const enneagram = profile.enneagram_profile || {status:"unassigned",identity:{},layers:{},interaction_strategy:{}};
  const mbti = profile.mbti_dimensions || {};
  const birth = profile.birth_analysis || {};
  const source = profile.source_profile_document;
  const mbtiDimensions = [
    ["ei", "社交启动与独处恢复"], ["sn", "具体经验与可能性探索"],
    ["tf", "事实权衡与价值感受"], ["jp", "计划结构与灵活调整"],
  ];
  $("#expertView").innerHTML = `
    <div class="expert-warning"><b>仅限授权内部核验</b><p>${esc(data.usage_policy?.note || "参考模型不进入默认画像。")}</p><small>不可作为科学诊断、准确率或对外人物结论；没有独立对话证据时不得决定回答。</small></div>
    <div class="trait-section">
      <div class="section-heading"><b>类型框架参考</b><small>非诊断 · 对外隐藏</small></div>
      <div class="inspector-card"><div class="data-grid"><label>内部类型标签</label><span>${esc(mbti.type_label || "未形成")}</span>${mbtiDimensions.map(([key,label]) => `<label>${esc(label)}</label><span>${mbti[key] ? `${mbti[key].value < .45 ? "偏左侧" : (mbti[key].value > .55 ? "偏右侧" : "接近平衡")}` : "待观察"}</span>`).join("")}</div><small class="card-footnote">四字母标签只用于内部兼容，不在默认画像中展示。</small></div>
    </div>
    <div class="trait-section">
      <div class="section-heading"><b>本人／专家提供的九型参考</b><small>明确来源 · 不自动分类</small><button class="button soft edit-enneagram">设置/更新</button></div>
      ${enneagram.status === "confirmed" ? `<div class="inspector-card"><div class="data-grid">
        <label>内部参考编码</label><span>${esc(enneagram.identity?.code)}</span>
        <label>提供来源</label><span>${esc(({user_supplied:"用户声明",external_assessment:"授权测评",expert_confirmed:"专家确认"})[enneagram.source] || enneagram.source)}</span>
        <label>可能更重视</label><span>${esc(enneagram.layers?.motivation?.core_drive || "待核验")}</span>
        <label>沟通假设</label><span>${esc(enneagram.interaction_strategy?.communication?.response_pattern || "待核验")}</span>
      </div><small class="card-footnote">上述内容是待验证假设，不直接进入聊天策略。</small></div>` : `<div class="inspector-card empty-evidence"><b>尚未提供类型参考</b><small>系统不会从生日、MBTI或普通对话自动推断。</small></div>`}
    </div>
    <div class="trait-section">
      <div class="section-heading"><b>出生信息先验</b><small>内部待验证 · 对外隐藏</small></div>
      ${digital.status === "derived" ? `<div class="inspector-card"><div class="data-grid"><label>内部规则代码</label><span>${esc(digital.code)}</span><label>规则版本</label><span>${esc(digital.algorithm_version)}</span><label>用途限制</label><span>只能提出校准问题</span></div></div>
        ${Object.values(digital.domains || {}).map(domain => `<details class="source-sheet"><summary>${esc(domain.label)}的待验证摘要<small>内部来源</small></summary><div class="expert-reference-copy">${esc(domain.summary || "暂无")}</div></details>`).join("")}` : `<div class="inspector-card empty-evidence"><small>未启用出生信息先验，或只把生日作为事实保存。</small></div>`}
    </div>
    <div class="trait-section">
      <div class="section-heading"><b>出生信息规则结果</b><small>非实证结论</small></div>
      <div class="inspector-card"><div class="data-grid"><label>生日事实</label><span>${esc(profile.identity?.birth_date || "未填写")}</span><label>原始规则文本</label><span>${esc(birth.bazi_text || "未生成")}</span><label>格局标签</label><span>${esc(birth.pattern_name || "未生成")}</span></div></div>
    </div>
    ${source ? `<div class="trait-section"><div class="section-heading"><b>原始来源资料</b><small>未经对话验证 · ${esc(source.source_file)}</small></div><div class="source-document">${Object.entries(source.sheets || {}).map(([name,rows]) => sourceSheetMarkup(name,rows)).join("")}</div></div>` : ""}`;
  const button = $(".edit-enneagram", $("#expertView"));
  if (button) button.onclick = () => openEnneagram().catch(error => toast(error.message));
}

async function loadAudit() {
  if (!appState.person || appState.audit) return;
  appState.audit = await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}/profile-explain`);
  renderAudit();
}

function renderAuditPlaceholder() {
  $("#auditView").innerHTML = `<div class="empty-panel"><span>↺</span><b>证据、反证与人工审计</b><p>打开此页时会加载完整的证据与版本历史。</p></div>`;
}

function renderAudit() {
  const data = appState.audit;
  if (!data) return renderAuditPlaceholder();
  const evidence = [...data.supporting_evidence, ...data.counter_evidence].slice(-30).reverse();
  $("#auditView").innerHTML = `
    <div class="profile-overview"><div class="profile-stat"><small>支持证据</small><b>${data.supporting_evidence.length}</b></div><div class="profile-stat"><small>反向证据</small><b>${data.counter_evidence.length}</b></div><div class="profile-stat"><small>失效证据</small><b>${data.invalidated_evidence.length}</b></div><div class="profile-stat"><small>画像版本</small><b>${data.version_history.length}</b></div></div>
    ${data.hidden_reference_evidence_count ? `<div class="inspector-card evidence-policy"><b>默认证据已净化</b><p>${esc(data.evidence_visibility_note)}</p><small>${data.hidden_reference_evidence_count} 条内部参考依据未在此展示</small></div>` : ""}
    <div class="trait-section"><div class="section-heading"><b>最近证据</b><small>含支持与反证</small></div>
      ${evidence.map(item => { const key = item.target_path.split(".").pop(); return `<div class="inspector-card"><div class="inspector-card-title"><span><b>${esc(traitLabels[key] || predicateLabels[key] || key)}</b><small>${esc(evidenceSourceLabels[item.source_type] || "可追溯对话依据")}</small></span><span class="tag ${item.direction < 0 ? "rose" : ""}">${item.direction < 0 ? "反向依据" : "支持依据"}</span></div><p style="font-size:10px;margin:0">${esc(item.reason)}</p><div class="evidence-quote">${item.source_message_id ? `来源消息 ${esc(item.source_message_id)}` : "来源可追溯"} · 可撤回或更正</div></div>`; }).join("") || `<div class="inspector-card"><small>暂无对话证据。</small></div>`}
    </div>
    <div class="trait-section"><div class="section-heading"><b>版本历史</b><small>每次有效变化生成一版</small></div>${data.version_history.slice().reverse().map(item => `<div class="audit-item"><b>画像 v${item.version}</b><p>已保存可回溯快照</p><small>${esc(item.created_at.replace("T"," ").slice(0,19))}</small></div>`).join("")}</div>
    <div class="trait-section"><div class="section-heading"><b>人工与系统审计</b><small>最近 ${data.audit_log.length} 条</small></div>${data.audit_log.map(item => `<div class="audit-item"><b>${esc(auditActionLabels[item.action] || item.action)}</b><p>操作者：${esc(item.actor || "api")}</p><small>${esc(item.created_at.replace("T"," ").slice(0,19))}</small></div>`).join("")}</div>`;
}

function switchInspector(name) {
  $$(".inspector-tabs button").forEach(button => button.classList.toggle("active", button.dataset.inspectorTab === name));
  $$(".inspector-view").forEach(view => view.classList.toggle("active", view.id === `${name}View`));
  if (name === "audit") loadAudit().catch(error => toast(error.message));
  if (name === "expert") loadExpertReference().catch(error => {
    $("#expertView").innerHTML = `<div class="empty-panel"><span>!</span><b>无法加载专家参考</b><p>${esc(error.message)}</p></div>`;
  });
}

$("#accessForm").onsubmit = async event => {
  event.preventDefault();
  const errorNode = $("#gateError");
  errorNode.classList.remove("show");
  appState.code = $("#gateCode").value.trim();
  if (!appState.code) {
    errorNode.textContent = "请输入访问密码";
    errorNode.classList.add("show");
    return;
  }
  try { await bootstrap(); }
  catch (error) { errorNode.textContent = error.message; errorNode.classList.add("show"); }
};

$("#peopleSearch").oninput = event => renderPeople(event.target.value);
$("#addPersonBtn").onclick = () => $("#personModal").classList.remove("hidden");
$("#newConversationBtn").onclick = createConversation;
$("#conversationSelect").onchange = event => selectConversation(event.target.value).catch(error => toast(error.message));
$("#modelProviderSelect").onchange = event => {
  appState.modelProvider = event.target.value;
  localStorage.setItem(MODEL_PROVIDER_KEY, appState.modelProvider);
  const selected = appState.modelOptions.find(item => item.provider === appState.modelProvider);
  event.target.title = selected ? `${selected.label} · ${selected.route} · ${selected.model}` : "";
  toast(`已切换为 ${selected?.label || appState.modelProvider}`);
};
$("#openProfileBtn").onclick = () => switchInspector("profile");
$("#mobilePeopleBtn").onclick = () => document.querySelector(".people-rail").classList.add("open");
$("#railCollapse").onclick = () => document.querySelector(".people-rail").classList.remove("open");
$("#jumpLatestBtn").onclick = forceScrollBottom;
$("#logoutBtn").onclick = () => {
  sessionStorage.removeItem(ACCESS_CODE_KEY);
  location.reload();
};

$("#messages").addEventListener("scroll", () => {
  appState.nearBottom = isNearBottom();
  $("#jumpLatestBtn").classList.toggle("show", !appState.nearBottom);
}, {passive: true});
$("#messages").addEventListener("wheel", () => {}, {passive: true});
$("#messages").addEventListener("touchmove", () => {}, {passive: true});

$("#sendBtn").onclick = sendMessage;
$("#messageInput").onkeydown = event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
};
$("#messageInput").oninput = event => {
  event.target.style.height = "auto";
  event.target.style.height = `${Math.min(event.target.scrollHeight, 128)}px`;
};

$$(".inspector-tabs button").forEach(button => button.onclick = () => switchInspector(button.dataset.inspectorTab));
$$("[data-close-modal]").forEach(button => button.onclick = () => $(`#${button.dataset.closeModal}`).classList.add("hidden"));
$(".form-field input[type=range]", $("#editForm")).oninput = event => $("#editValueOutput").textContent = Number(event.target.value).toFixed(2);

$("#personForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("/demo/api/people", {
      method: "POST", body: JSON.stringify({
        display_name: form.get("display_name"),
        birth_date: form.get("birth_date") || null,
        notes: form.get("notes") || null
      })
    });
    $("#personModal").classList.add("hidden");
    event.target.reset();
    await refreshPeople(false);
    await selectPerson(data.person.user_id);
    toast("人物空间已创建");
  } catch (error) { toast(error.message); }
};

$("#enneagramForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const stack = String(form.get("stack")).split("/");
  const source = String(form.get("source"));
  const confidence = source === "expert_confirmed" ? 0.95 : (source === "external_assessment" ? 0.85 : 0.8);
  try {
    await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}/enneagram`, {
      method: "POST",
      body: JSON.stringify({
        expected_profile_version: appState.version,
        enneagram: {
          core_type: Number(form.get("core_type")),
          wing: form.get("wing") ? Number(form.get("wing")) : null,
          primary_instinct: stack[0],
          secondary_instinct: stack[1],
          source,
          confidence
        },
        reason: form.get("reason")
      })
    });
    $("#enneagramModal").classList.add("hidden");
    await reloadProfile();
    toast("内部类型参考已更新");
  } catch (error) { toast(error.message); }
};

$("#editForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api(`/demo/api/people/${encodeURIComponent(appState.person.user_id)}/manual-edit`, {
      method: "POST",
      body: JSON.stringify({
        expected_profile_version: appState.version,
        target_path: form.get("target_path"),
        value: Number(form.get("value")),
        reason: form.get("reason")
      })
    });
    $("#editModal").classList.add("hidden");
    await reloadProfile();
    await refreshPeople();
    switchInspector("profile");
    toast("人工更正已保存并锁定");
  } catch (error) { toast(error.message); }
};

window.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    $$(".modal-backdrop").forEach(modal => modal.classList.add("hidden"));
    document.querySelector(".people-rail").classList.remove("open");
  }
});

async function restoreAccess() {
  const savedCode = sessionStorage.getItem(ACCESS_CODE_KEY);
  if (!savedCode) return;
  appState.code = savedCode;
  try {
    await bootstrap();
  } catch {
    sessionStorage.removeItem(ACCESS_CODE_KEY);
    appState.code = "";
  }
}

restoreAccess();
