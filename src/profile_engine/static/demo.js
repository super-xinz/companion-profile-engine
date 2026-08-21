"use strict";

const state = {
  code: "",
  people: [],
  current: null,
  summary: "",
  confidenceNote: "",
  dimensions: [],
  preferences: [],
  conversations: [],
  conversationId: "",
  messages: [],
  version: 1,
  busy: false,
  nearBottom: true
};

const accessStoreKey = "resonance-demo-access";
const byId = (id) => document.getElementById(id);

function createNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function initials(name) {
  const value = String(name || "人").trim();
  return value.slice(-1) || "人";
}

function asNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const cleaned = value.trim().replace("%", "");
  const parsed = Number(cleaned);
  if (!Number.isFinite(parsed)) return null;
  return value.includes("%") ? parsed / 100 : parsed;
}

function normalizedScore(value) {
  const parsed = asNumber(value);
  if (parsed === null) return null;
  return Math.max(0, Math.min(1, parsed > 1 ? parsed / 100 : parsed));
}

function percentage(value) {
  const score = normalizedScore(value);
  return score === null ? "逐步积累" : String(Math.round(score * 100)) + "%";
}

function levelText(value) {
  const score = normalizedScore(value);
  if (score === null) return String(value || "持续观察");
  if (score < 0.25) return "较低";
  if (score < 0.45) return "偏低";
  if (score < 0.65) return "适中";
  if (score < 0.82) return "偏高";
  return "较高";
}

function showToast(message) {
  const node = byId("toast");
  node.textContent = message;
  node.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => node.classList.remove("show"), 2600);
}

function friendlyError(status) {
  if (status === 401 || status === 403) return "访问口令不正确，请向负责人确认后再试。";
  if (status === 404) return "这项体验内容暂时不可用，请稍后再试。";
  if (status === 409) return "内容刚刚有变化，请重试一次。";
  if (status === 429) return "操作有些频繁，请稍等片刻再试。";
  if (status >= 500) return "服务暂时有些忙，请稍后再试。";
  return "请求未完成，请检查网络后重试。";
}

async function api(path, options) {
  const settings = options || {};
  const headers = Object.assign(
    {"X-Demo-Code": state.code},
    settings.body ? {"Content-Type": "application/json"} : {},
    settings.headers || {}
  );
  let response;
  try {
    response = await fetch(path, Object.assign({}, settings, {headers: headers}));
  } catch {
    const offline = new Error("当前网络不可用，请检查连接后重试。");
    offline.status = 0;
    throw offline;
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    const error = new Error(friendlyError(response.status));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function setGateError(message) {
  const node = byId("gateError");
  node.textContent = message || "";
  node.classList.toggle("show", Boolean(message));
}

function showGate(message) {
  state.code = "";
  sessionStorage.removeItem(accessStoreKey);
  byId("accessGate").classList.remove("hidden");
  byId("appShell").setAttribute("aria-busy", "false");
  setGateError(message || "");
  window.setTimeout(() => byId("gateCode").focus(), 30);
}

function hideGate() {
  setGateError("");
  byId("accessGate").classList.add("hidden");
  byId("appShell").setAttribute("aria-busy", "false");
}

function setServiceStatus(value) {
  const available = value !== false && value !== "unavailable";
  byId("serviceLabel").textContent = available ? "服务可用" : "稍后重试";
  byId("serviceDot").classList.toggle("warning", !available);
}

function personKey(person) {
  return person && person.public_id ? String(person.public_id) : "";
}

function personConfidence(person) {
  if (!person) return null;
  return person.confidence !== undefined ? person.confidence : person.overall_confidence;
}

function personTagline(person) {
  if (person && person.tagline) return String(person.tagline);
  const count = Number(person && person.conversation_count) || 0;
  if (count > 0) return "已进行 " + count + " 段互动";
  return "准备好开始一段新对话";
}

function renderCases() {
  const list = byId("caseList");
  list.replaceChildren();
  const people = state.people.slice(0, 5);
  if (!people.length) {
    const empty = createNode("div", "case-empty");
    empty.append(
      createNode("span", "", "◌"),
      createNode("b", "", "案例正在准备"),
      createNode("p", "", "请稍后刷新页面再试。")
    );
    list.append(empty);
    return;
  }
  people.forEach((person, index) => {
    const button = createNode("button", "case-item");
    button.type = "button";
    button.dataset.publicId = personKey(person);
    button.setAttribute("aria-pressed", String(personKey(state.current) === personKey(person)));
    if (personKey(state.current) === personKey(person)) button.classList.add("active");

    const avatar = createNode("span", "case-avatar", initials(person.display_name));
    avatar.setAttribute("aria-hidden", "true");
    const copy = createNode("span", "case-copy");
    copy.append(
      createNode("b", "", person.display_name || "案例 " + String(index + 1)),
      createNode("small", "", personTagline(person))
    );
    const confidence = createNode("span", "case-confidence", percentage(personConfidence(person)));
    confidence.title = "当前整体可信度";
    button.append(avatar, copy, confidence);
    button.addEventListener("click", () => selectPerson(personKey(person)));
    list.append(button);
  });
}

function setPersonHeader(person) {
  if (!person) return;
  byId("personAvatar").textContent = initials(person.display_name);
  byId("personName").textContent = person.display_name || "互动案例";
  byId("personTagline").textContent = personTagline(person);
  byId("insightSubtitle").textContent =
    (person.display_name || "当前案例") + " · 可信度 " + percentage(personConfidence(person));
}

function loadingMessages() {
  const holder = createNode("div", "message-loading");
  holder.setAttribute("role", "status");
  holder.append(
    createNode("i"),
    createNode("i"),
    createNode("i"),
    createNode("span", "", "正在打开对话…")
  );
  byId("messages").replaceChildren(holder);
}

function summaryText(value) {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) {
    const parts = value.filter((item) => typeof item === "string" && item.trim());
    if (parts.length) return parts.join(" ");
  }
  if (value && typeof value === "object") {
    for (const key of ["text", "summary", "description"]) {
      if (typeof value[key] === "string" && value[key].trim()) return value[key].trim();
    }
  }
  return "这份理解会随着更多真实互动继续丰富，目前适合用来调整回应的语气、节奏与提问方式。";
}

function applyDetail(data) {
  const person = data.person || {};
  const safeFallback = state.people.find((item) => personKey(item) === personKey(person)) || {};
  state.current = Object.assign({}, safeFallback, person);
  state.summary = summaryText(data.dynamic_summary !== undefined ? data.dynamic_summary : data.profile && data.profile.summary);
  const confidenceNote =
    data.confidence_explanation !== undefined
      ? data.confidence_explanation
      : state.current.confidence_explanation !== undefined
        ? state.current.confidence_explanation
        : data.profile && data.profile.confidence_explanation;
  state.confidenceNote =
    typeof confidenceNote === "string" && confidenceNote.trim()
      ? confidenceNote.trim()
      : "";
  state.dimensions = Array.isArray(data.metrics)
    ? data.metrics
    : data.profile && Array.isArray(data.profile.dimensions)
      ? data.profile.dimensions
      : [];
  state.preferences = Array.isArray(data.communication_preferences)
    ? data.communication_preferences
    : data.profile && Array.isArray(data.profile.communication_preferences)
      ? data.profile.communication_preferences
      : [];
  state.conversations = Array.isArray(data.conversations) ? data.conversations : [];
  state.version = Number(
    state.current.profile_version !== undefined
      ? state.current.profile_version
      : data.profile_version
  ) || 1;
  setPersonHeader(state.current);
  renderCases();
  renderInsights();
  renderConversationOptions();
}

function confidenceExplanation(value) {
  const score = normalizedScore(value);
  if (score === null) {
    return "可信度会结合互动是否充足、表达是否一致以及内容是否仍然适用来逐步积累。";
  }
  const amount = Math.round(score * 100);
  if (amount < 50) {
    return amount + "% 表示已经看见一些方向，但了解仍在积累。它适合帮助选择语气和提问顺序，不适合当成确定结论。";
  }
  if (amount < 75) {
    return amount + "% 表示多个互动片段支持当前理解，但遇到新情境时仍会继续调整。";
  }
  return amount + "% 表示当前理解得到了较充分且较一致的互动支持，仍会尊重新表达并持续更新。";
}

function preferenceText(item) {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return "";
  const name = item.name || item.label || "";
  const value = item.value;
  if (value === undefined || value === null || value === "") return String(name);
  if (typeof value === "boolean") {
    if (!name) return value ? "建议采用" : "暂不采用";
    return String(name) + "：" + (value ? "是" : "否");
  }
  if (typeof value === "number") {
    if (!name) return levelText(value);
    return String(name) + "：" + levelText(value);
  }
  if (!name) return String(value);
  return String(name) + "：" + String(value);
}

function renderInsights() {
  const root = byId("insightContent");
  root.replaceChildren();
  if (!state.current) {
    const empty = createNode("div", "empty-insight");
    empty.append(
      createNode("span", "", "◎"),
      createNode("b", "", "还没有选择案例"),
      createNode("p", "", "选择人物后，这里会展示互动理解摘要。")
    );
    root.append(empty);
    return;
  }

  const summaryCard = createNode("section", "insight-card summary-card");
  summaryCard.append(
    createNode("span", "section-label", "人物摘要"),
    createNode("p", "summary-copy", state.summary)
  );

  const confidenceCard = createNode("section", "insight-card confidence-card");
  const confidenceHead = createNode("div", "confidence-head");
  confidenceHead.append(
    createNode("span", "section-label", "整体可信度"),
    createNode("b", "", percentage(personConfidence(state.current)))
  );
  confidenceCard.append(
    confidenceHead,
    createNode(
      "p",
      "",
      state.confidenceNote || confidenceExplanation(personConfidence(state.current))
    )
  );

  const dimensionSection = createNode("section", "insight-section");
  const dimensionHead = createNode("div", "section-heading");
  dimensionHead.append(
    createNode("b", "", "核心维度"),
    createNode("small", "", "用于选择更合适的沟通方式")
  );
  dimensionSection.append(dimensionHead);
  if (state.dimensions.length) {
    const list = createNode("div", "dimension-list");
    state.dimensions.forEach((item) => {
      const row = createNode("div", "dimension-row");
      const top = createNode("div", "dimension-top");
      top.append(
        createNode("b", "", item.name || item.label || "互动维度"),
        createNode("span", "", levelText(item.value))
      );
      const score = normalizedScore(item.value);
      const meter = createNode("div", "dimension-meter");
      const fill = createNode("i");
      fill.style.width = String(Math.round((score === null ? 0.5 : score) * 100)) + "%";
      meter.append(fill);
      const note = createNode(
        "small",
        "",
        "该项可信度 " + percentage(item.confidence)
      );
      if (item.description) note.title = String(item.description);
      row.append(top, meter, note);
      list.append(row);
    });
    dimensionSection.append(list);
  } else {
    dimensionSection.append(createNode("p", "section-empty", "更多维度会在互动中逐步形成。"));
  }

  const preferenceSection = createNode("section", "insight-section");
  const preferenceHead = createNode("div", "section-heading");
  preferenceHead.append(
    createNode("b", "", "沟通偏好"),
    createNode("small", "", "建议优先用于回应策略")
  );
  preferenceSection.append(preferenceHead);
  const preferenceValues = state.preferences.map(preferenceText).filter(Boolean);
  if (preferenceValues.length) {
    const tags = createNode("div", "preference-list");
    preferenceValues.forEach((item) => tags.append(createNode("span", "", item)));
    preferenceSection.append(tags);
  } else {
    preferenceSection.append(createNode("p", "section-empty", "继续对话后会给出更具体的沟通建议。"));
  }

  root.append(summaryCard, confidenceCard, dimensionSection, preferenceSection);
}

function conversationId(item) {
  return item && item.conversation_id ? String(item.conversation_id) : "";
}

function renderConversationOptions() {
  const select = byId("conversationSelect");
  select.replaceChildren();
  state.conversations.forEach((item, index) => {
    const option = createNode("option");
    option.value = conversationId(item);
    option.textContent = item.title || "对话 " + String(index + 1);
    option.selected = option.value === state.conversationId;
    select.append(option);
  });
  if (!state.conversations.length) {
    const option = createNode("option", "", "尚未开始");
    option.value = "";
    select.append(option);
  }
  select.disabled = !state.current || state.busy || !state.conversations.length;
}

function messageNode(role, content) {
  const row = createNode("div", "message-row " + role);
  if (role === "assistant") {
    const avatar = createNode("span", "message-avatar", "伴");
    avatar.setAttribute("aria-hidden", "true");
    row.append(avatar);
  }
  row.append(createNode("div", "message-bubble", content));
  return row;
}

function renderMessages() {
  const root = byId("messages");
  root.replaceChildren();
  const visible = state.messages.filter(
    (item) => item && (item.role === "user" || item.role === "assistant") && typeof item.content === "string"
  );
  if (!visible.length) {
    const empty = createNode("div", "empty-chat");
    empty.append(
      createNode("span", "empty-symbol", "◌"),
      createNode("h1", "", "从一句真实的话开始"),
      createNode("p", "", "可以聊聊最近的状态、一个难做的决定，或希望对方怎样回应。")
    );
    root.append(empty);
  } else {
    visible.forEach((item) => root.append(messageNode(item.role, item.content)));
  }
  scrollToLatest();
}

function isNearBottom() {
  const root = byId("messages");
  return root.scrollHeight - root.scrollTop - root.clientHeight < 100;
}

function scrollToLatest() {
  window.requestAnimationFrame(() => {
    const root = byId("messages");
    root.scrollTop = root.scrollHeight;
    state.nearBottom = true;
    byId("jumpLatestBtn").classList.remove("show");
  });
}

function appendMessage(role, content) {
  const root = byId("messages");
  const follow = state.nearBottom;
  const empty = root.querySelector(".empty-chat");
  if (empty) empty.remove();
  root.append(messageNode(role, content));
  if (follow) scrollToLatest();
  else byId("jumpLatestBtn").classList.add("show");
}

function setThinking(active) {
  const old = byId("thinking");
  if (old) old.remove();
  if (!active) return;
  const row = createNode("div", "message-row assistant");
  row.id = "thinking";
  const avatar = createNode("span", "message-avatar", "伴");
  avatar.setAttribute("aria-hidden", "true");
  const bubble = createNode("div", "message-bubble");
  const dots = createNode("div", "thinking-dots");
  dots.setAttribute("aria-label", "正在组织回应");
  dots.append(createNode("i"), createNode("i"), createNode("i"));
  bubble.append(dots);
  row.append(avatar, bubble);
  byId("messages").append(row);
  if (state.nearBottom) scrollToLatest();
}

function setChatEnabled(enabled) {
  byId("messageInput").disabled = !enabled;
  byId("sendBtn").disabled = !enabled;
  byId("newConversationBtn").disabled = !state.current || state.busy;
  byId("conversationSelect").disabled = !state.current || state.busy || !state.conversations.length;
}

function closeMobilePanels() {
  byId("casePanel").classList.remove("open");
  byId("insightPanel").classList.remove("open");
  byId("drawerShade").classList.remove("show");
}

async function loadMessages(id) {
  if (!state.current || !id) {
    state.conversationId = "";
    state.messages = [];
    renderMessages();
    return;
  }
  loadingMessages();
  const path =
    "/demo/api/people/" + encodeURIComponent(personKey(state.current)) +
    "/conversations/" + encodeURIComponent(id) + "/messages";
  const data = await api(path);
  state.conversationId = id;
  state.messages = Array.isArray(data.messages) ? data.messages : [];
  renderConversationOptions();
  renderMessages();
}

async function createConversation(silent) {
  if (!state.current) return;
  const path =
    "/demo/api/people/" + encodeURIComponent(personKey(state.current)) + "/conversations";
  const data = await api(path, {
    method: "POST",
    body: JSON.stringify({title: "新的体验对话"})
  });
  const conversation = data.conversation || data;
  if (!conversationId(conversation)) throw new Error("暂时无法开始新对话，请稍后再试。");
  state.conversations.unshift(conversation);
  renderConversationOptions();
  await loadMessages(conversationId(conversation));
  if (!silent) showToast("已开始一段新的独立对话");
}

async function selectPerson(publicId) {
  if (!publicId || state.busy) return;
  state.busy = true;
  setChatEnabled(false);
  loadingMessages();
  byId("insightContent").replaceChildren(createNode("div", "panel-loading", "正在整理互动摘要…"));
  try {
    const data = await api("/demo/api/people/" + encodeURIComponent(publicId));
    applyDetail(data);
    state.conversationId = "";
    if (state.conversations.length) {
      await loadMessages(conversationId(state.conversations[0]));
    } else {
      await createConversation(true);
    }
    closeMobilePanels();
    byId("messageInput").focus();
  } catch (error) {
    if (error.status === 401 || error.status === 403) showGate(error.message);
    else {
      showToast(error.message);
      renderMessages();
      renderInsights();
    }
  } finally {
    state.busy = false;
    setChatEnabled(Boolean(state.current && state.conversationId));
    renderConversationOptions();
  }
}

async function refreshCurrentInsights() {
  if (!state.current) return;
  const selected = state.conversationId;
  const data = await api("/demo/api/people/" + encodeURIComponent(personKey(state.current)));
  applyDetail(data);
  state.conversationId = selected;
  renderConversationOptions();
  const index = state.people.findIndex((item) => personKey(item) === personKey(state.current));
  if (index >= 0) state.people[index] = Object.assign({}, state.people[index], state.current);
  renderCases();
}

function messageKey() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return "message-" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}

async function sendMessage() {
  const input = byId("messageInput");
  const text = input.value.trim();
  if (!text || !state.current || !state.conversationId || state.busy) return;
  const history = state.messages
    .filter((item) => item && (item.role === "user" || item.role === "assistant"))
    .slice(-12)
    .map((item) => ({role: item.role, content: String(item.content || "")}));
  const versionBeforeTurn = state.version;

  state.busy = true;
  state.nearBottom = isNearBottom();
  state.messages.push({role: "user", content: text});
  appendMessage("user", text);
  input.value = "";
  resizeComposer();
  setChatEnabled(false);
  setThinking(true);

  try {
    const data = await api("/demo/api/chat", {
      method: "POST",
      body: JSON.stringify({
        public_id: personKey(state.current),
        conversation_id: state.conversationId,
        message_id: messageKey(),
        expected_profile_version: versionBeforeTurn,
        text: text,
        history: history
      })
    });
    setThinking(false);
    const reply = typeof data.assistant_reply === "string" && data.assistant_reply.trim()
      ? data.assistant_reply
      : "我在听。你愿意再多说一点吗？";
    state.messages.push({role: "assistant", content: reply});
    const returnedVersion = Number(data.profile_version) || versionBeforeTurn;
    const understandingChanged = returnedVersion > versionBeforeTurn;
    state.version = returnedVersion;
    appendMessage("assistant", reply);
    try {
      await refreshCurrentInsights();
    } catch {
      showToast("回应已完成，摘要稍后会自动同步");
    }
    if (understandingChanged) showToast("互动理解摘要已更新");
  } catch (error) {
    setThinking(false);
    const savedWithoutReply =
      error.status === 502 &&
      error.data &&
      error.data.code === "assistant_temporarily_unavailable";
    if (savedWithoutReply) {
      state.version = Number(error.data.profile_version) || state.version;
      try {
        await refreshCurrentInsights();
      } catch {
        // The saved turn remains usable even if the side summary refresh is delayed.
      }
      showToast("消息已记录，暂时无法生成回应，请稍后继续");
    } else {
      state.messages.pop();
      const rows = byId("messages").querySelectorAll(".message-row.user");
      const last = rows[rows.length - 1];
      if (last) last.remove();
      input.value = text;
      resizeComposer();
      if (error.status === 401 || error.status === 403) showGate(error.message);
      else showToast(error.message);
    }
  } finally {
    state.busy = false;
    setChatEnabled(Boolean(state.current && state.conversationId));
    input.focus();
  }
}

function resizeComposer() {
  const input = byId("messageInput");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 132) + "px";
}

async function bootstrap() {
  byId("appShell").setAttribute("aria-busy", "true");
  const data = await api("/demo/api/workspace/bootstrap", {
    method: "POST",
    body: "{}"
  });
  state.people = Array.isArray(data.people) ? data.people.slice(0, 5) : [];
  sessionStorage.setItem(accessStoreKey, state.code);
  setServiceStatus(data.service_status);
  renderCases();
  hideGate();
  if (state.people.length) await selectPerson(personKey(state.people[0]));
}

async function enterWithCode(code) {
  state.code = String(code || "").trim();
  if (!state.code) {
    setGateError("请输入访问口令。");
    return;
  }
  const button = byId("gateSubmitBtn");
  button.disabled = true;
  button.textContent = "正在进入…";
  setGateError("");
  try {
    await bootstrap();
  } catch (error) {
    showGate(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "进入体验";
  }
}

byId("accessForm").addEventListener("submit", (event) => {
  event.preventDefault();
  enterWithCode(byId("gateCode").value);
});

byId("logoutBtn").addEventListener("click", () => {
  state.people = [];
  state.current = null;
  state.messages = [];
  state.conversations = [];
  state.conversationId = "";
  showGate("");
  byId("gateCode").value = "";
});

byId("newConversationBtn").addEventListener("click", async () => {
  if (state.busy || !state.current) return;
  state.busy = true;
  setChatEnabled(false);
  try {
    await createConversation(false);
  } catch (error) {
    if (error.status === 401 || error.status === 403) showGate(error.message);
    else showToast(error.message);
  } finally {
    state.busy = false;
    setChatEnabled(Boolean(state.current && state.conversationId));
  }
});

byId("conversationSelect").addEventListener("change", async (event) => {
  if (state.busy || !event.target.value) return;
  state.busy = true;
  setChatEnabled(false);
  try {
    await loadMessages(event.target.value);
  } catch (error) {
    if (error.status === 401 || error.status === 403) showGate(error.message);
    else showToast(error.message);
  } finally {
    state.busy = false;
    setChatEnabled(Boolean(state.current && state.conversationId));
  }
});

byId("sendBtn").addEventListener("click", sendMessage);
byId("messageInput").addEventListener("input", resizeComposer);
byId("messageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

byId("messages").addEventListener("scroll", () => {
  state.nearBottom = isNearBottom();
  byId("jumpLatestBtn").classList.toggle("show", !state.nearBottom);
});
byId("jumpLatestBtn").addEventListener("click", scrollToLatest);

byId("openCasesBtn").addEventListener("click", () => {
  byId("insightPanel").classList.remove("open");
  byId("casePanel").classList.add("open");
  byId("drawerShade").classList.add("show");
});
byId("closeCasesBtn").addEventListener("click", closeMobilePanels);
byId("openInsightsBtn").addEventListener("click", () => {
  byId("casePanel").classList.remove("open");
  byId("insightPanel").classList.add("open");
  byId("drawerShade").classList.add("show");
});
byId("closeInsightsBtn").addEventListener("click", closeMobilePanels);
byId("drawerShade").addEventListener("click", closeMobilePanels);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMobilePanels();
});

const storedCode = sessionStorage.getItem(accessStoreKey);
if (storedCode) {
  byId("gateCode").value = storedCode;
  enterWithCode(storedCode);
} else {
  showGate("");
}
