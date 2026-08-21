const rulesState = {
  code: "", actor: "系统管理员", permissions: [], current: null, selected: null,
  revisions: [], members: [], asset: "cold_start", dirty: false,
  editorMode: "document", rawDirty: false
};
const ACCESS_CODE_KEY = "profile-engine-access-code";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>'"]/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
const statusLabels = {
  draft: "草稿", pending_review: "待审核", approved: "已通过", published: "生产中", superseded: "历史版本"
};
const roleLabels = {admin: "管理员", reviewer: "审核人", expert: "画像专家", viewer: "只读成员"};
const assetLabels = {
  cold_start: "冷启动规则", dialogue: "对话维护规则",
  schema: "画像结构", enneagram: "深层互动策略"
};
const assetDescriptions = {
  cold_start: "规定人物第一次创建时，如何从授权信息和原始规则库形成低置信度初始画像。",
  dialogue: "规定每轮对话如何理解证据、更新画像、处理冲突并调整回答策略。",
  schema: "规定一份完整画像必须包含哪些字段，以及每个字段的格式和边界。",
  enneagram: "规定多层互动参数、关注重点、组合策略、场景适配和维护边界。"
};
const keyLabels = {
  objective: "目标", non_goals: "不用于哪些事情", input_contract: "输入要求",
  execution_pipeline: "执行流程", feature_calculators: "特征计算",
  rule_bank_cleaning: "原始资料清理", dimension_aggregation: "17维度汇总",
  derived_profile_generation: "派生画像生成", output_requirements: "输出要求",
  golden_cases: "五个完整画像样例", expert_maintenance_contract: "专家维护边界",
  core_principles: "核心原则", turn_processing_pipeline: "每轮对话处理流程",
  semantic_frame_schema: "模型理解结果格式", routing_rules: "信息分类规则",
  evidence_types: "证据类型", language_understanding_rules: "语言理解规则",
  portrait_regeneration: "人物画像重建", runtime_state_and_memory: "状态与长期记忆",
  update_operators: "允许的更新动作", update_math: "更新幅度与置信度",
  conflict_and_versioning: "冲突与版本", reply_hint_generation: "回答策略生成",
  generalized_examples: "通用示例", coverage_manifest: "规则覆盖清单",
  expert_authoring_contract: "专家编辑边界", document_type: "文档类型",
  language: "语言", common_types: "通用字段类型", runtime_extensions: "运行时扩展",
  profile_instance_contract: "完整画像必备内容", required: "必填内容",
  optional: "选填内容", reject_when: "拒绝处理的情况", source_file: "原始文件",
  domains: "内容领域", requirements: "处理要求", formula: "计算方式",
  confidence: "置信度", warnings: "提醒", examples: "示例",
  semantic_signal_extraction: "语义信号提取", generalized_signal_dictionary: "通用信号词典",
  trait_mapping_rules: "17 维画像映射", behavior_scenario_maintenance: "行为场景维护",
  language_style_maintenance: "语言风格维护", evidence_model: "证据模型",
  update_policy: "更新策略", conflict_resolution: "冲突处理", rollback_policy: "回滚策略",
  canonical_profile: "完整画像结构", core_traits: "17 个核心维度", categories: "维度分类",
  fields: "字段", purpose: "用途", design_rules: "设计原则", source_rule_bank: "原始规则库",
  effects: "影响方向", cues: "语义线索", description: "说明", definition: "定义",
  chinese_name: "中文名", low_anchor: "低值锚点", high_anchor: "高值锚点",
  target_schema: "目标画像结构", status: "状态", rule_system_version: "规则版本",
  schema_version: "结构版本", dialogue: "对话", cold_start: "冷启动",
  identity: "基本身份", birth_analysis: "初始画像线索", mbti_dimensions: "偏好倾向维度",
  behavior_style: "行为风格", language_style: "语言风格", portrait: "人物画像",
  interaction_preferences: "沟通偏好", current_state: "当前短期状态", memories: "长期记忆",
  groups: "分组", scenarios: "场景", fixed_contexts: "固定情境",
  required_paths: "必须包含的内容", meta_fields: "版本与审计信息",
  core_types: "核心互动风格", wings: "辅助互动风格", instinct_stacks: "关注重点组合",
  scene_adaptation: "场景适配层", layer_model: "六层人格模型", weights: "继承权重",
  maintenance: "互动策略维护规则", boundaries: "模型边界", identity_schema: "互动风格结构"
};

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2400);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "X-Demo-Code": rulesState.code,
      ...(options.body ? {"Content-Type": "application/json"} : {}),
      ...(options.headers || {})
    }
  });
  let payload = {};
  try { payload = await response.json(); } catch {}
  if (!response.ok) {
    let detail = payload.detail || payload.message || `请求失败（${response.status}）`;
    if (typeof detail === "object") detail = detail.message || JSON.stringify(detail);
    throw new Error(detail);
  }
  return payload;
}

function initials(name) { return String(name || "人").trim().slice(-1); }
function niceLabel(key) {
  return keyLabels[key] || String(key).replaceAll("_", " ");
}
function can(permission) { return rulesState.permissions.includes(permission); }
function activeRevision() { return rulesState.selected || rulesState.current; }

async function bootstrap() {
  const data = await api("/demo/api/rules/workspace");
  sessionStorage.setItem(ACCESS_CODE_KEY, rulesState.code);
  rulesState.permissions = data.actor.permissions;
  rulesState.current = data.current;
  rulesState.selected = data.current;
  rulesState.revisions = data.revisions;
  rulesState.members = data.members;
  $("#actorName").textContent = data.actor.display_name;
  $("#actorRole").textContent = roleLabels[data.actor.role] || data.actor.role;
  $("#actorAvatar").textContent = initials(data.actor.display_name);
  $("#productionVersion").textContent = `r${data.current.revision_no} · ${data.current.title}`;
  $("#accessGate").classList.add("hidden");
  renderAll();
}

function renderAll() {
  renderDraftBanner();
  renderAsset();
  renderVersions();
  renderRevisionSelectors();
  renderMembers();
}

function renderDraftBanner() {
  const revision = activeRevision();
  const banner = $("#draftBanner");
  const status = revision.status;
  banner.classList.add("show");
  const controls = [];
  if (status === "draft" && can("rules.edit")) controls.push(`<button class="button soft" data-action="submit">提交审核</button>`);
  if (status === "pending_review" && can("rules.review")) controls.push(`<button class="button soft" data-action="approve">审核通过</button>`);
  if (status === "approved" && can("rules.publish")) controls.push(`<button class="button primary" data-action="publish">正式发布</button>`);
  banner.innerHTML = `<span><b>r${revision.revision_no} · ${esc(revision.title)}</b>　${statusLabels[status] || status}　·　${esc(revision.created_by)}</span><span>${controls.join("")}</span>`;
  $$("[data-action]", banner).forEach(button => button.onclick = () => revisionAction(button.dataset.action));
  $("#createDraftBtn").disabled = !can("rules.edit");
}

function renderAsset() {
  const revision = activeRevision();
  const content = revision.canonical_json?.[rulesState.asset] || {};
  const readOnly = revision.status !== "draft" || !can("rules.edit");
  $("#assetTitle").textContent = assetLabels[rulesState.asset];
  $("#assetMeta").textContent = `r${revision.revision_no} · ${statusLabels[revision.status] || revision.status} · ${Object.keys(content).length} 个章节${readOnly ? " · 只读" : " · 可直接编辑"}`;
  $("#saveDraftBtn").disabled = readOnly;
  $("#validateBtn").disabled = readOnly;
  $("#documentModeBtn").classList.toggle("active", rulesState.editorMode === "document");
  $("#sourceModeBtn").classList.toggle("active", rulesState.editorMode === "source");
  if (rulesState.editorMode === "source") {
    renderSourceDocument(content, readOnly);
  } else {
    renderReadableDocument(content, readOnly);
  }
  renderValidation(revision.validation_report);
}

function documentHeading(key, depth, itemCount) {
  const title = esc(niceLabel(key));
  const sourceKey = keyLabels[key] ? `<small>${esc(key)}</small>` : "";
  const count = itemCount == null ? "" : `<span>${itemCount} 项</span>`;
  const level = Math.min(4, depth + 2);
  return `<h${level} class="document-heading">${title}${sourceKey}${count}</h${level}>`;
}

function scalarDocument(value, path, label, readOnly) {
  const encodedPath = esc(JSON.stringify(path));
  const sourceKey = keyLabels[label] ? `<small>${esc(label)}</small>` : "";
  if (typeof value === "boolean") {
    return `<div class="document-field compact"><div class="document-label"><b>${esc(niceLabel(label))}</b>${sourceKey}</div>
      <select class="document-select" data-document-path="${encodedPath}" ${readOnly ? "disabled" : ""}>
        <option value="true" ${value ? "selected" : ""}>是</option>
        <option value="false" ${!value ? "selected" : ""}>否</option>
      </select></div>`;
  }
  const type = value === null ? "null" : typeof value;
  const text = value === null ? (readOnly ? "未提供" : "") : String(value);
  return `<div class="document-field ${text.length > 90 ? "long" : ""}">
    <div class="document-label"><b>${esc(niceLabel(label))}</b>${sourceKey}</div>
    <div class="document-value ${readOnly ? "readonly" : ""}" data-document-path="${encodedPath}" data-value-type="${type}"
      ${readOnly ? "" : 'contenteditable="true"'} data-placeholder="点击这里填写内容">${esc(text)}</div>
  </div>`;
}

function renderDocumentNode(value, path, depth, readOnly) {
  if (Array.isArray(value)) {
    const complex = value.some(item => item && typeof item === "object");
    const items = value.map((child, index) => {
      const childPath = [...path, index];
      if (child && typeof child === "object") {
        return `<article class="document-list-card">
          <div class="document-item-number">第 ${index + 1} 项</div>
          ${renderDocumentNode(child, childPath, depth + 1, readOnly)}
          ${readOnly ? "" : `<button class="document-delete" data-delete-path="${esc(JSON.stringify(childPath))}">删除这一项</button>`}
        </article>`;
      }
      return `<li class="document-list-item">
        <div class="document-value ${readOnly ? "readonly" : ""}" data-document-path="${esc(JSON.stringify(childPath))}"
          data-value-type="${child === null ? "null" : typeof child}" ${readOnly ? "" : 'contenteditable="true"'}
          data-placeholder="点击这里填写内容">${esc(child ?? "")}</div>
        ${readOnly ? "" : `<button class="document-delete icon" data-delete-path="${esc(JSON.stringify(childPath))}" aria-label="删除这一项">×</button>`}
      </li>`;
    }).join("");
    const addButton = readOnly ? "" : `<button class="document-add" data-add-array="${esc(JSON.stringify(path))}" data-complex="${complex}">＋ 添加一项</button>`;
    return complex ? `<div class="document-card-list">${items}</div>${addButton}` : `<ol class="document-list">${items}</ol>${addButton}`;
  }
  if (value && typeof value === "object") {
    return Object.entries(value).map(([key, child]) => {
      const childPath = [...path, key];
      if (child && typeof child === "object") {
        const count = Array.isArray(child) ? child.length : Object.keys(child).length;
        return `<section class="document-section depth-${Math.min(depth, 4)}">
          ${documentHeading(key, depth, count)}
          <div class="document-section-body">${renderDocumentNode(child, childPath, depth + 1, readOnly)}</div>
          ${readOnly || depth === 0 ? "" : `<button class="document-delete section-delete" data-delete-path="${esc(JSON.stringify(childPath))}">删除“${esc(niceLabel(key))}”</button>`}
        </section>`;
      }
      return scalarDocument(child, childPath, key, readOnly);
    }).join("") + (readOnly ? "" : `<button class="document-add subtle" data-add-object="${esc(JSON.stringify(path))}">＋ 新增文档字段</button>`);
  }
  return scalarDocument(value, path, "内容", readOnly);
}

function renderReadableDocument(content, readOnly) {
  const editor = $("#documentEditor");
  editor.innerHTML = `<article class="rule-document">
    <header class="document-cover">
      <span>共鸣画像引擎 · 规则资产</span>
      <h1>${esc(assetLabels[rulesState.asset])}</h1>
      <p>${esc(assetDescriptions[rulesState.asset])}</p>
      <div><b>${readOnly ? "当前为只读版本" : "浅绿色正文可直接修改"}</b><small>所有章节会作为一个完整文档统一保存和校验</small></div>
    </header>
    <div class="document-body">${renderDocumentNode(content, [rulesState.asset], 0, readOnly)}</div>
  </article>`;
  bindDocumentEvents();
}

async function renderSourceDocument(content, readOnly) {
  const editor = $("#documentEditor");
  editor.innerHTML = `<div class="source-loading">正在生成完整原始文件…</div>`;
  try {
    const data = await api("/demo/api/rules/documents/dump", {
      method: "POST", body: JSON.stringify({asset: rulesState.asset, content})
    });
    if (rulesState.editorMode !== "source") return;
    editor.innerHTML = `<div class="source-editor-shell">
      <div class="source-editor-note"><b>完整原始文件</b><span>适合整体复制、批量修改或交给工程人员处理。修改后保存时会自动检查文档格式。</span></div>
      <textarea id="sourceDocumentText" spellcheck="false" ${readOnly ? "readonly" : ""}>${esc(data.document_text)}</textarea>
    </div>`;
    const textarea = $("#sourceDocumentText");
    if (textarea && !readOnly) textarea.oninput = () => {
      rulesState.rawDirty = true;
      markDirty();
    };
  } catch (error) {
    editor.innerHTML = `<div class="source-loading error">${esc(error.message)}</div>`;
  }
}

function valueAt(path) {
  let cursor = activeRevision().canonical_json;
  for (const part of path) cursor = cursor[part];
  return cursor;
}

function setAt(path, value) {
  let cursor = activeRevision().canonical_json;
  path.slice(0, -1).forEach(part => cursor = cursor[part]);
  cursor[path.at(-1)] = value;
  markDirty();
}

function deleteAt(path) {
  let cursor = activeRevision().canonical_json;
  path.slice(0, -1).forEach(part => cursor = cursor[part]);
  const key = path.at(-1);
  if (Array.isArray(cursor)) cursor.splice(Number(key), 1); else delete cursor[key];
  markDirty();
  renderAsset();
}

function markDirty() {
  rulesState.dirty = true;
  $("#saveDraftBtn").textContent = "保存草稿 · 有未保存修改";
}

function documentNodeValue(node, previous) {
  if (node.tagName === "SELECT") return node.value === "true";
  const text = node.innerText.replace(/\r\n/g, "\n").trim();
  if (typeof previous === "number") {
    const parsed = Number(text);
    return Number.isFinite(parsed) ? parsed : previous;
  }
  if (previous === null && text === "") return null;
  return text;
}

function syncDocumentEdits() {
  if (rulesState.editorMode !== "document") return;
  $$("[data-document-path]", $("#documentEditor")).forEach(node => {
    const path = JSON.parse(node.dataset.documentPath);
    const previous = valueAt(path);
    const next = documentNodeValue(node, previous);
    if (JSON.stringify(previous) !== JSON.stringify(next)) setAt(path, next);
  });
}

function bindDocumentEvents() {
  $$("[data-document-path]", $("#documentEditor")).forEach(node => {
    node.oninput = markDirty;
    node.onchange = markDirty;
  });
  $$("[data-delete-path]", $("#documentEditor")).forEach(button => button.onclick = () => {
    if (confirm("确定删除这部分文档内容吗？保存草稿后才会生效。")) {
      syncDocumentEdits();
      deleteAt(JSON.parse(button.dataset.deletePath));
    }
  });
  $$("[data-add-array]", $("#documentEditor")).forEach(button => button.onclick = () => {
    syncDocumentEdits();
    const target = valueAt(JSON.parse(button.dataset.addArray));
    target.push(button.dataset.complex === "true" && target.length ? clone(target.at(-1)) : "");
    markDirty();
    renderAsset();
  });
  $$("[data-add-object]", $("#documentEditor")).forEach(button => button.onclick = () => {
    syncDocumentEdits();
    const key = prompt("请输入新字段标识（建议使用简短英文，例如 new_rule）");
    if (!key) return;
    const target = valueAt(JSON.parse(button.dataset.addObject));
    if (key in target) return toast("这个字段已经存在");
    target[key] = "";
    markDirty();
    renderAsset();
  });
}

async function applySourceDocument() {
  if (!rulesState.rawDirty) return;
  const textarea = $("#sourceDocumentText");
  if (!textarea) return;
  const data = await api("/demo/api/rules/documents/parse", {
    method: "POST",
    body: JSON.stringify({asset: rulesState.asset, document_text: textarea.value})
  });
  activeRevision().canonical_json[rulesState.asset] = data.content;
  rulesState.rawDirty = false;
  markDirty();
}

async function switchEditorMode(mode) {
  if (mode === rulesState.editorMode) return;
  try {
    if (rulesState.editorMode === "source") await applySourceDocument();
    else syncDocumentEdits();
    rulesState.editorMode = mode;
    renderAsset();
  } catch (error) {
    toast(error.message);
  }
}

function renderValidation(report) {
  const node = $("#validationReport");
  if (!report || (!report.valid && !report.errors)) {
    node.className = "validation-report";
    node.innerHTML = "";
    return;
  }
  const checks = report.checks || {};
  node.className = `validation-report show ${report.valid ? "valid" : "invalid"}`;
  node.innerHTML = report.valid
    ? `✓ 检查通过：${checks.trait_count ?? 17} 个画像维度、${checks.dialogue_mapping_count ?? 17} 个对话映射、互动策略覆盖完整、${checks.conflict_count ?? 0} 个冲突。`
    : `检查未通过：${(report.errors || []).map(esc).join("；")}`;
}

async function createDraft() {
  if (!can("rules.edit")) return toast("当前账号没有规则编辑权限");
  try {
    const data = await api("/demo/api/rules/drafts", {
      method: "POST", body: JSON.stringify({title: `专家协作草稿 · ${new Date().toLocaleDateString("zh-CN")}`, base_revision_id: rulesState.current.id})
    });
    rulesState.selected = data.revision;
    rulesState.revisions.unshift({...data.revision, canonical_json: undefined});
    rulesState.dirty = false;
    rulesState.rawDirty = false;
    rulesState.editorMode = "document";
    renderAll();
    toast("草稿已创建，可以直接修改完整文档");
  } catch (error) { toast(error.message); }
}

async function saveDraft(silent = false) {
  const revision = activeRevision();
  if (revision.status !== "draft") return;
  try {
    if (rulesState.editorMode === "source") await applySourceDocument();
    else syncDocumentEdits();
    const summary = rulesState.dirty ? `由 ${rulesState.actor} 在线编辑完整规则文档` : revision.change_summary;
    const data = await api(`/demo/api/rules/drafts/${revision.id}`, {
      method: "PUT", body: JSON.stringify({canonical_json: revision.canonical_json, change_summary: summary})
    });
    rulesState.selected = data.revision;
    rulesState.dirty = false;
    rulesState.rawDirty = false;
    $("#saveDraftBtn").textContent = "保存草稿";
    updateRevisionCache(data.revision);
    renderDraftBanner();
    renderValidation(data.revision.validation_report);
    if (!silent) toast("草稿已保存，并完成格式、字段、引用和冲突检查");
    return data.revision;
  } catch (error) { toast(error.message); throw error; }
}

function updateRevisionCache(revision) {
  const index = rulesState.revisions.findIndex(item => item.id === revision.id);
  const lightweight = {...revision};
  delete lightweight.canonical_json;
  if (index >= 0) rulesState.revisions[index] = lightweight; else rulesState.revisions.unshift(lightweight);
}

async function revisionAction(action, revisionId = activeRevision().id) {
  try {
    if (rulesState.dirty) await saveDraft(true);
    const notes = {
      submit: `由 ${rulesState.actor} 提交审核，已通过自动校验`,
      approve: `由 ${rulesState.actor} 审核通过`,
      publish: `由 ${rulesState.actor} 正式发布`,
      rollback: `由 ${rulesState.actor} 执行版本回滚`
    };
    const data = await api(`/demo/api/rules/revisions/${revisionId}/${action}`, {
      method: "POST", body: JSON.stringify({note: notes[action]})
    });
    rulesState.selected = data.revision;
    if (data.revision.status === "published") rulesState.current = data.revision;
    await refreshWorkspace();
    toast({submit:"已提交审核",approve:"审核已通过",publish:"规则已正式发布，画像引擎已切换",rollback:"已回滚并发布"}[action]);
  } catch (error) { toast(error.message); }
}

async function refreshWorkspace() {
  const data = await api("/demo/api/rules/workspace");
  rulesState.current = data.current;
  rulesState.revisions = data.revisions;
  rulesState.members = data.members;
  if (rulesState.selected) {
    const fresh = data.revisions.find(item => item.id === rulesState.selected.id);
    if (fresh) {
      const full = await api(`/demo/api/rules/revisions/${fresh.id}`);
      rulesState.selected = full.revision;
    } else rulesState.selected = data.current;
  }
  $("#productionVersion").textContent = `r${data.current.revision_no} · ${data.current.title}`;
  renderAll();
}

function renderRevisionSelectors() {
  const options = rulesState.revisions.map(item => `<option value="${item.id}">r${item.revision_no} · ${esc(item.title)} · ${statusLabels[item.status] || item.status}</option>`).join("");
  $("#testRevision").innerHTML = options;
  $("#compareLeft").innerHTML = options;
  $("#compareRight").innerHTML = options;
  if (rulesState.revisions[1]) $("#compareLeft").value = rulesState.revisions[1].id;
  if (rulesState.revisions[0]) $("#compareRight").value = rulesState.revisions[0].id;
}

function renderVersions() {
  $("#versionList").innerHTML = rulesState.revisions.map(item => {
    const actions = [];
    if (item.status === "draft" && can("rules.edit")) actions.push(`<button class="button soft" data-open-revision="${item.id}">继续编辑</button>`, `<button class="button soft" data-version-action="submit" data-id="${item.id}">提交审核</button>`);
    if (item.status === "pending_review" && can("rules.review")) actions.push(`<button class="button soft" data-version-action="approve" data-id="${item.id}">审核通过</button>`);
    if (item.status === "approved" && can("rules.publish")) actions.push(`<button class="button primary" data-version-action="publish" data-id="${item.id}">发布</button>`);
    if (item.status !== "draft" && item.status !== "pending_review" && can("rules.publish")) actions.push(`<button class="button soft" data-version-action="rollback" data-id="${item.id}">回滚至此</button>`);
    return `<div class="version-item"><div class="version-item-head"><b>r${item.revision_no} · ${esc(item.title)}</b><span class="tag ${item.status === "published" ? "" : "gray"}">${statusLabels[item.status] || item.status}</span><time>${esc((item.updated_at || "").slice(0,10))}</time></div><p>${esc(item.change_summary || "暂无变更摘要")} · 修改人 ${esc(item.created_by)}</p><div class="version-actions">${actions.join("")}</div></div>`;
  }).join("");
  $$("[data-version-action]").forEach(button => button.onclick = () => revisionAction(button.dataset.versionAction, button.dataset.id));
  $$("[data-open-revision]").forEach(button => button.onclick = async () => {
    const data = await api(`/demo/api/rules/revisions/${button.dataset.openRevision}`);
    rulesState.selected = data.revision;
    rulesState.dirty = false;
    rulesState.rawDirty = false;
    rulesState.editorMode = "document";
    switchView("assets");
    renderDraftBanner();
    renderAsset();
  });
}

async function compareVersions() {
  try {
    const left = $("#compareLeft").value, right = $("#compareRight").value;
    const data = await api(`/demo/api/rules/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`);
    $("#diffPanel").innerHTML = `<div class="section-heading"><b>字段级差异</b><small>${data.change_count} 项</small></div>${data.changes.length ? data.changes.map(item => `<div class="diff-item"><div class="diff-path">${esc(item.path)}</div><div class="diff-values"><span>之前：${esc(formatValue(item.before))}</span><span>之后：${esc(formatValue(item.after))}</span></div></div>`).join("") : `<div class="empty-state"><span>✓</span><b>两个版本没有差异</b></div>`}`;
  } catch (error) { toast(error.message); }
}

function formatValue(value) {
  if (value == null) return "空";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function runTest(event) {
  event.preventDefault();
  const button = $("button[type=submit]", event.target);
  button.disabled = true;
  button.textContent = "正在运行隔离测试…";
  try {
    const data = await api("/demo/api/rules/test", {
      method: "POST",
      body: JSON.stringify({text: $("#testText").value, revision_id: $("#testRevision").value})
    });
    const renderSide = result => `
      <div class="result-card"><h3>模型理解</h3><pre>${esc(result.understanding.map(item => `${item.subject} · ${item.predicate} · ${item.supporting_span}`).join("\n") || "没有提取到个人画像语义")}</pre></div>
      <div class="result-card"><h3>命中规则</h3><pre>${esc(result.rule_hits.map(item => `${item.rule}\n证据：${item.evidence}`).join("\n\n") || "没有规则命中")}</pre></div>
      <div class="result-card"><h3>画像变化</h3><pre>${esc(result.profile_changes.map(item => `${item.field}: ${item.before.toFixed(3)} → ${item.after.toFixed(3)}`).join("\n") || "画像保持不变")}</pre></div>
      <div class="result-card"><h3>回答策略</h3><pre>${esc(`重点：${result.reply_strategy.focus}\n语气：${result.reply_strategy.tone}\n最多 ${result.reply_strategy.max_sentences} 句 · ${result.reply_strategy.question_count} 个问题`)}</pre></div>`;
    $("#testResults").innerHTML = `
      <div class="comparison-head"><div><b>生产规则 · r${data.production.revision.revision_no}</b><small>${esc(data.production.revision.title)}</small></div><div><b>候选规则 · r${data.candidate.revision.revision_no}</b><small>${esc(data.candidate.revision.title)}</small></div></div>
      <div class="isolation-note">✓ 隔离验证通过：${data.production_profile_unchanged ? "真实画像没有新增版本或变化" : "检测到异常，请勿发布"}</div>
      <div class="result-columns"><div>${renderSide(data.production)}</div><div>${renderSide(data.candidate)}</div></div>`;
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "运行隔离测试"; }
}

function renderMembers() {
  $("#memberTable").innerHTML = `<div class="member-row header"><span>成员</span><span>账号</span><span>角色</span><span>权限</span></div>` + rulesState.members.map(item => `
    <div class="member-row"><span><b>${esc(item.display_name)}</b></span><span>${esc(item.account)}</span><span class="tag">${esc(roleLabels[item.role] || item.role)}</span><span>${esc((item.permissions || []).map(p => p.split(".").at(-1)).join(" · ") || "只读")}</span></div>`).join("");
  $("#addMemberBtn").disabled = !can("members.manage");
}

function switchView(name) {
  const titleMap = {assets:"规则资产",test:"隔离测试",versions:"版本与发布",members:"团队与权限"};
  $$(".rule-nav button").forEach(button => button.classList.toggle("active", button.dataset.ruleView === name));
  $$(".rule-view").forEach(view => view.classList.toggle("active", view.id === `${name}View`));
  $("#viewCrumb").textContent = titleMap[name];
  $("#rulesTitle").textContent = titleMap[name];
}

$("#accessForm").onsubmit = async event => {
  event.preventDefault();
  const error = $("#gateError");
  error.classList.remove("show");
  rulesState.code = $("#gateCode").value.trim();
  if (!rulesState.code) {
    error.textContent = "请输入访问密码";
    error.classList.add("show");
    return;
  }
  try { await bootstrap(); }
  catch (failure) { error.textContent = failure.message; error.classList.add("show"); }
};

$$(".rule-nav button").forEach(button => button.onclick = () => switchView(button.dataset.ruleView));
$$(".asset-nav button").forEach(button => button.onclick = async () => {
  if (button.dataset.asset === rulesState.asset) return;
  try {
    if (rulesState.editorMode === "source") await applySourceDocument();
    else syncDocumentEdits();
    rulesState.asset = button.dataset.asset;
    rulesState.rawDirty = false;
    $$(".asset-nav button").forEach(item => item.classList.toggle("active", item === button));
    renderAsset();
  } catch (error) {
    toast(error.message);
  }
});
$("#documentModeBtn").onclick = () => switchEditorMode("document");
$("#sourceModeBtn").onclick = () => switchEditorMode("source");
$("#createDraftBtn").onclick = createDraft;
$("#saveDraftBtn").onclick = () => saveDraft(false);
$("#validateBtn").onclick = async () => {
  const revision = await saveDraft(true);
  if (revision) {
    renderValidation(revision.validation_report);
    toast(revision.validation_report.valid ? "全部自动检查已通过" : "发现需要修正的问题");
  }
};
$("#compareBtn").onclick = compareVersions;
$("#ruleTestForm").onsubmit = runTest;
$("#addMemberBtn").onclick = () => $("#memberModal").classList.remove("hidden");
$$("[data-close-modal]").forEach(button => button.onclick = () => $(`#${button.dataset.closeModal}`).classList.add("hidden"));

$("#memberForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("/demo/api/members", {
      method: "POST",
      body: JSON.stringify({display_name: form.get("display_name"), account: form.get("account"), role: form.get("role")})
    });
    rulesState.members.push(data.member);
    renderMembers();
    $("#memberModal").classList.add("hidden");
    event.target.reset();
    toast("团队成员与权限已保存");
  } catch (error) { toast(error.message); }
};

window.addEventListener("beforeunload", event => {
  if (rulesState.dirty) { event.preventDefault(); event.returnValue = ""; }
});
window.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault();
    saveDraft(false);
  }
  if (event.key === "Escape") $$(".modal-backdrop").forEach(modal => modal.classList.add("hidden"));
});

async function restoreAccess() {
  const savedCode = sessionStorage.getItem(ACCESS_CODE_KEY);
  if (!savedCode) return;
  rulesState.code = savedCode;
  try {
    await bootstrap();
  } catch {
    sessionStorage.removeItem(ACCESS_CODE_KEY);
    rulesState.code = "";
  }
}

restoreAccess();
