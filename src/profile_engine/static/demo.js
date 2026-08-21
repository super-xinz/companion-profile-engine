const appState = {
  code: "", actor: "系统管理员", people: [], person: null, profile: null,
  conversations: [], conversationId: "", messages: [], version: 1,
  lastEngine: null, busy: false, nearBottom: true, audit: null,
  modelProvider: "deepseek", modelOptions: []
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
  action_mode: "行动模式", self_system: "自我系统", emotion_relation_mode: "情绪与关系"
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
      <span><b>${esc(person.display_name)}</b><small>${esc(person.mbti || "XXXX")} · 画像 v${person.profile_version} · ${person.conversation_count} 段对话</small></span>
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
  appState.lastEngine = null;
  $("#personName").textContent = detail.person.display_name;
  $("#personAvatar").textContent = initials(detail.person.display_name);
  $("#personMeta").textContent = `${detail.person.mbti} · 画像 v${detail.profile_version} · 置信度 ${Math.round(detail.person.overall_confidence * 100)}%`;
  $("#inspectorSubtitle").textContent = `${detail.person.display_name} · 当前画像 v${detail.profile_version}`;
  $("#messageInput").disabled = false;
  $("#sendBtn").disabled = false;
  renderPeople($("#peopleSearch").value);
  renderConversationOptions();
  renderProfile(detail.manual_overrides || []);
  renderTurn();
  renderAuditPlaceholder();
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
  $("#personMeta").textContent = `${detail.person.mbti} · 画像 v${detail.profile_version} · 置信度 ${Math.round(detail.person.overall_confidence * 100)}%`;
  $("#inspectorSubtitle").textContent = `${detail.person.display_name} · 当前画像 v${detail.profile_version}`;
  renderProfile(detail.manual_overrides || []);
}

function renderTurn(mode = "ready") {
  const view = $("#turnView");
  if (mode === "processing") {
    view.innerHTML = `<div class="flow-track"><span class="done">用户消息</span><i>→</i><span class="done">语义理解</span><i>→</i><span>规则判断</span><i>→</i><span>回答策略</span></div><div class="empty-panel"><span>◌</span><b>画像引擎正在处理</b><p>模型提出候选，规则与证据门槛决定是否写入。</p></div>`;
    return;
  }
  const engine = appState.lastEngine;
  if (!engine) {
    view.innerHTML = `<div class="empty-panel"><span>◎</span><b>等待这一轮对话</b><p>这里会展示模型理解、命中规则、画像变化和回答策略。</p></div>`;
    return;
  }
  const frames = engine.semantic_frames || [];
  const patches = engine.profile_patch || [];
  const operations = engine.runtime_operations || [];
  const hints = engine.reply_hints || {};
  view.innerHTML = `
    <div class="flow-track"><span class="done">用户消息</span><i>→</i><span class="done">语义理解</span><i>→</i><span class="done">规则判断</span><i>→</i><span class="done">回答策略</span></div>
    <div class="profile-overview">
      <div class="profile-stat"><small>本轮使用画像</small><b>v${engine.strategy_trace?.profile_version_used || engine.profile_version}</b></div>
      <div class="profile-stat"><small>已接受候选</small><b>${engine.strategy_trace?.accepted_signals || 0}</b></div>
    </div>
    <div class="section-heading"><b>模型理解了什么</b><small>${frames.length} 个语义帧</small></div>
    ${frames.length ? frames.map(frame => `
      <div class="inspector-card">
        <div class="inspector-card-title"><span><b>${esc(predicateLabels[frame.predicate] || frame.predicate)}</b><small>${esc(frame.semantic_domain)} · ${esc(frame.temporal_scope)}</small></span><span class="tag">${Math.round((frame.extractor_confidence || 0) * 100)}%</span></div>
        <div class="data-grid"><label>主体</label><span>${frame.subject === "user" ? "用户本人" : esc(frame.subject)}</span><label>提取值</label><span>${esc(frame.object || "—")}</span></div>
        <div class="evidence-quote">“${esc(frame.supporting_span)}”</div>
      </div>`).join("") : `<div class="inspector-card"><b>没有形成长期画像候选</b><small>普通知识问题和他人信息不会被强行归入用户画像。</small></div>`}
    <div class="section-heading"><b>画像怎样变化</b><small>${patches.length + operations.length} 项</small></div>
    ${patches.map(patch => `
      <div class="inspector-card"><div class="inspector-card-title"><b>${esc(traitLabels[patch.field.split(".").pop()] || patch.field)}</b><span class="tag gold">${patch.after >= patch.before ? "+" : ""}${(patch.after - patch.before).toFixed(3)}</span></div>
      <div class="mbti-row"><span>${Math.round(patch.before * 100)}</span><div class="trait-meter"><i style="width:${patch.after * 100}%"></i></div><b>${Math.round(patch.after * 100)}</b></div></div>`).join("")}
    ${operations.map(operation => `<div class="inspector-card"><div class="inspector-card-title"><b>${esc(operation.operation)}</b><span class="tag">${esc(operation.field || operation.key || "记忆")}</span></div><small>${esc(operation.value ?? operation.memory_id ?? "")}</small></div>`).join("")}
    ${!patches.length && !operations.length ? `<div class="inspector-card"><div class="inspector-card-title"><b>本轮保持画像不变</b><span class="tag gray">审慎模式</span></div><small>证据不足或字段已被人工锁定时，系统不会更新长期画像。</small></div>` : ""}
    <div class="section-heading"><b>回答策略如何变化</b><small>已被 Chatbot 消费</small></div>
    <div class="inspector-card"><div class="inspector-card-title"><b>${esc(hints.focus || "回应当前消息")}</b><span class="tag">${engine.strategy_trace?.consumed_by_chatbot ? "已消费" : "待消费"}</span></div>
      <div class="data-grid"><label>语气</label><span>${esc(hints.tone || "自然")}</span><label>最长</label><span>${hints.max_sentences || 4} 句</span><label>提问</label><span>${hints.question_count ?? 0} 个</span><label>结构</label><span>${esc(hints.structure_level || "simple")}</span></div>
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

function renderProfile(overrides = []) {
  const view = $("#profileView");
  const profile = appState.profile;
  if (!profile) {
    view.innerHTML = `<div class="empty-panel"><span>◌</span><b>尚未选择人物</b></div>`;
    return;
  }
  const locked = new Set(overrides.map(item => item.target_path));
  const categories = Object.entries(profile.core_traits || {});
  const memories = profile.runtime?.memories || [];
  const prefs = profile.runtime?.interaction_preferences || {};
  const states = profile.runtime?.current_state || {};
  const sourcePortrait = profile.source_portrait || {};
  const sourceDocument = profile.source_profile_document;
  const enneagram = profile.enneagram_profile || {status: "unassigned", identity: {}, layers: {}, interaction_strategy: {}};
  const digitalCode = profile.digital_code_profile || {status: "unassigned", domains: {}};
  const tableView = profile.table_view || null;
  const portraitLabels = {
    essence: "本质", strengths: "优势", weaknesses: "弱点",
    core_tension: "核心矛盾", suitable_roles: "适合角色"
  };
  view.innerHTML = `
    <div class="profile-overview">
      <div class="profile-stat"><small>画像版本</small><b>v${profile.meta.profile_version}</b></div>
      <div class="profile-stat"><small>总体置信度</small><b>${Math.round(profile.meta.overall_confidence * 100)}%</b></div>
      <div class="profile-stat"><small>MBTI 推导</small><b>${esc(profile.mbti_dimensions?.type_label || "XXXX")}</b></div>
      <div class="profile-stat"><small>长期记忆</small><b>${memories.length}</b></div>
    </div>
    ${tableView ? `
    <div class="trait-section">
      <div class="section-heading"><b>统一画像视图</b><small>面向交付的一体化出口</small></div>
      <div class="inspector-card"><div class="data-grid">
        <label>数字密码</label><span>${esc(tableView.digital_code_profile?.code || "未生成")}</span>
        <label>九型人格</label><span>${esc(tableView.enneagram_profile?.identity?.code || "未确认")}</span>
        <label>核心维度</label><span>${Object.keys(tableView.core_traits || {}).length} 组</span>
        <label>行为画像</label><span>${Object.keys(tableView.behavior_style || {}).length} 组</span>
        <label>语言画像</label><span>${Object.keys(tableView.language_style || {}).length} 组</span>
        <label>人物画像</label><span>${Object.keys(tableView.portrait || {}).length} 项</span>
      </div></div>
      <div class="inspector-card source-portrait"><b>整合摘要</b><p>${esc(tableView.portrait?.essence?.content || digitalCode.provenance?.source_file || "暂无摘要")}</p></div>
    </div>` : ""}
    <div class="trait-section">
      <div class="section-heading"><b>九型互动画像</b><small>明确输入后派生，不从普通对话自动判断</small><button class="button soft edit-enneagram">设置/更新</button></div>
      ${enneagram.status === "confirmed" ? `
        <div class="inspector-card">
          <div class="data-grid">
            <label>人格编码</label><span>${esc(enneagram.identity.code)}</span>
            <label>主型</label><span>${esc(enneagram.identity.core_type_name)}</span>
            <label>来源</label><span>${esc(enneagram.source)}</span>
            <label>置信度</label><span>${Math.round((enneagram.confidence || 0) * 100)}%</span>
            <label>核心驱动力</label><span>${esc(enneagram.layers?.motivation?.core_drive)}</span>
            <label>注意力方向</label><span>${esc((enneagram.layers?.attention?.instinct_focus || []).join("、"))}</span>
            <label>沟通策略</label><span>${esc(enneagram.interaction_strategy?.communication?.response_pattern)}</span>
            <label>成长方向</label><span>${esc(enneagram.interaction_strategy?.companionship?.growth_direction)}</span>
          </div>
        </div>` : `<div class="inspector-card"><small>尚未设置九型人格。系统不会根据 MBTI、生日或单轮对话自动推断。</small></div>`}
    </div>
    <div class="trait-section">
      <div class="section-heading"><b>数字密码画像</b><small>生日归约 · 低置信度冷启动</small></div>
      ${digitalCode.status === "derived" ? `
        <div class="inspector-card"><div class="data-grid">
          <label>数字密码</label><span>${esc(digitalCode.code)}</span>
          <label>模型置信度</label><span>${Math.round((digitalCode.confidence || 0) * 100)}%</span>
          <label>算法版本</label><span>${esc(digitalCode.algorithm_version)}</span>
          <label>来源</label><span>${esc(digitalCode.provenance?.source_file)}</span>
        </div></div>
        ${Object.values(digitalCode.domains || {}).map(domain => `
          <div class="inspector-card source-portrait"><b>${esc(domain.label)}</b><p>${esc(domain.summary)}</p>
            <details class="source-sheet"><summary>查看 ${domain.components?.length || 0} 个加权成分</summary>
              <div class="source-table-wrap"><table><tr><th>特质</th><th>权重</th><th>内容</th></tr>${(domain.components || []).map(item => `<tr><td>${esc(item.label)}</td><td>${Math.round(item.weight * 100)}%</td><td>${esc(item.text)}</td></tr>`).join("")}</table></div>
            </details>
          </div>`).join("")}` : `<div class="inspector-card"><small>未提供生日、未授权生日推断，或日期超出当前规则库范围。</small></div>`}
    </div>
    ${sourceDocument ? `<div class="trait-section">
      <div class="section-heading"><b>原始完整画像</b><small>${esc(sourceDocument.source_file)}</small></div>
      ${Object.entries(sourcePortrait).map(([key, item]) => `<div class="inspector-card source-portrait"><b>${esc(portraitLabels[key] || key)}</b><p>${esc(item.content)}</p></div>`).join("")}
      <div class="source-document">
        ${Object.entries(sourceDocument.sheets || {}).map(([name, rows]) => sourceSheetMarkup(name, rows)).join("")}
      </div>
    </div>` : ""}
    <div class="trait-section"><div class="section-heading"><b>MBTI 连续维度</b><small>由底层画像派生</small></div>
      ${[["ei","I — E"],["sn","S — N"],["tf","F — T"],["jp","P — J"]].map(([key,label]) => {
        const item = profile.mbti_dimensions?.[key] || {value:.5, confidence:0};
        return `<div class="mbti-row"><span>${label}</span><div class="trait-meter" title="置信度 ${Math.round(item.confidence*100)}%"><i style="width:${item.value*100}%"></i></div><b>${Math.round(item.value*100)}</b></div>`;
      }).join("")}
    </div>
    ${categories.map(([category, traits]) => `
      <div class="trait-section">
        <div class="section-heading"><b>${esc(categoryLabels[category] || category)}</b><small>${Object.keys(traits).length} 个维度</small></div>
        ${Object.entries(traits).map(([key, item]) => {
          const path = `core_traits.${category}.${key}`;
          return `<div class="trait-row" title="${esc(item.interpretation)}&#10;更新时间：${esc(item.updated_at)}&#10;证据：${item.evidence_refs?.length || 0} 条">
            <span>${esc(traitLabels[key] || key)} ${locked.has(path) ? `<span class="locked-mark">◆</span>` : ""}</span>
            <div class="trait-meter"><i style="width:${item.value*100}%"></i></div><b>${Math.round(item.value*100)}</b>
            <button class="edit-trait" data-path="${esc(path)}" data-value="${item.value}" data-label="${esc(traitLabels[key] || key)}" aria-label="编辑${esc(traitLabels[key] || key)}">✎</button>
          </div>`;
        }).join("")}
      </div>`).join("")}
    <div class="trait-section"><div class="section-heading"><b>身份事实</b><small>可人工更正</small></div>
      <div class="inspector-card"><div class="data-grid"><label>姓名</label><span>${esc(profile.identity?.display_name || "未填写")}</span><label>生日</label><span>${esc(profile.identity?.birth_date || "未填写")}</span><label>时区</label><span>${esc(profile.identity?.timezone || "未填写")}</span></div></div>
    </div>
    <div class="trait-section"><div class="section-heading"><b>沟通偏好与当前状态</b><small>短期状态带有效期</small></div>
      <div class="inspector-card"><div class="data-grid">${Object.entries(prefs).map(([k,v]) => `<label>${esc(k)}</label><span>${esc(v)}</span>`).join("") || `<label>偏好</label><span>尚未形成</span>`}${Object.entries(states).map(([k,v]) => `<label>${esc(k)}</label><span>${esc(v.value)} · 至 ${esc(v.expires_at?.slice(0,16).replace("T"," "))}</span>`).join("")}</div></div>
    </div>
    <div class="trait-section"><div class="section-heading"><b>长期事实与重要事件</b><small>${memories.length} 条</small></div>
      ${memories.length ? memories.map(item => `<div class="memory-item"><b>${esc(item.key || item.type || "记忆")}</b><p>${esc(item.value || item.summary || item.predicate || "")}</p><small>记录 ${esc(item.memory_id)}</small></div>`).join("") : `<div class="inspector-card"><small>尚未记录长期事实或重要事件。</small></div>`}
    </div>`;
  $$(".edit-trait", view).forEach(button => button.onclick = () => openEdit(button.dataset.path, Number(button.dataset.value), button.dataset.label));
  const editEnneagram = $(".edit-enneagram", view);
  if (editEnneagram) editEnneagram.onclick = openEnneagram;
}

function openEdit(path, value, label) {
  const form = $("#editForm");
  form.elements.target_path.value = path;
  form.elements.value.value = value;
  form.elements.reason.value = "";
  $("#editValueOutput").textContent = Number(value).toFixed(2);
  $("#editModalTitle").textContent = `调整${label}`;
  $("#editFieldDescription").textContent = `当前值 ${Number(value).toFixed(2)}。保存后生成新版本，并以人工更正优先于模型推断。`;
  $("#editModal").classList.remove("hidden");
}

function openEnneagram() {
  const form = $("#enneagramForm");
  const identity = appState.profile?.enneagram_profile?.identity || {};
  form.elements.core_type.value = identity.core_type || 7;
  form.elements.wing.value = identity.wing || "";
  form.elements.stack.value = identity.instinct_stack || "SX/SO";
  form.elements.source.value = appState.profile?.enneagram_profile?.source || "expert_confirmed";
  form.elements.reason.value = "";
  $("#enneagramModal").classList.remove("hidden");
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
  $("#auditView").innerHTML = `
    <div class="profile-overview"><div class="profile-stat"><small>支持证据</small><b>${data.supporting_evidence.length}</b></div><div class="profile-stat"><small>反向证据</small><b>${data.counter_evidence.length}</b></div><div class="profile-stat"><small>失效证据</small><b>${data.invalidated_evidence.length}</b></div><div class="profile-stat"><small>画像版本</small><b>${data.version_history.length}</b></div></div>
    <div class="trait-section"><div class="section-heading"><b>最近证据</b><small>含支持与反证</small></div>
      ${[...data.supporting_evidence, ...data.counter_evidence].slice(-30).reverse().map(item => `<div class="inspector-card"><div class="inspector-card-title"><span><b>${esc(item.target_path.split(".").pop())}</b><small>${esc(item.rule_id)}</small></span><span class="tag ${item.direction < 0 ? "rose" : ""}">${item.direction < 0 ? "反证" : "支持"}</span></div><p style="font-size:10px;margin:0">${esc(item.reason)}</p><div class="evidence-quote">影响 ${Number(item.impact).toFixed(3)} · ${esc(item.source_type)}</div></div>`).join("") || `<div class="inspector-card"><small>暂无对话证据。</small></div>`}
    </div>
    <div class="trait-section"><div class="section-heading"><b>版本历史</b><small>不可变快照</small></div>${data.version_history.slice().reverse().map(item => `<div class="audit-item"><b>画像 v${item.version}</b><p>总体置信度 ${Math.round(item.overall_confidence * 100)}%</p><small>${esc(item.created_at.replace("T"," ").slice(0,19))}</small></div>`).join("")}</div>
    <div class="trait-section"><div class="section-heading"><b>人工与系统审计</b><small>最近 ${data.audit_log.length} 条</small></div>${data.audit_log.map(item => `<div class="audit-item"><b>${esc(item.action)}</b><p>操作者：${esc(item.actor || "api")}</p><small>${esc(item.created_at.replace("T"," ").slice(0,19))}</small></div>`).join("")}</div>`;
}

function switchInspector(name) {
  $$(".inspector-tabs button").forEach(button => button.classList.toggle("active", button.dataset.inspectorTab === name));
  $$(".inspector-view").forEach(view => view.classList.toggle("active", view.id === `${name}View`));
  if (name === "audit") loadAudit().catch(error => toast(error.message));
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
  const coreType = form.get("enneagram_core_type");
  const stack = String(form.get("enneagram_stack") || "SX/SO").split("/");
  const enneagram = coreType ? {
    core_type: Number(coreType),
    wing: form.get("enneagram_wing") ? Number(form.get("enneagram_wing")) : null,
    primary_instinct: stack[0],
    secondary_instinct: stack[1],
    source: "expert_confirmed",
    confidence: 0.95
  } : null;
  try {
    const data = await api("/demo/api/people", {
      method: "POST", body: JSON.stringify({
        display_name: form.get("display_name"),
        birth_date: form.get("birth_date") || null,
        enneagram,
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
    toast("九型互动画像已更新");
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
