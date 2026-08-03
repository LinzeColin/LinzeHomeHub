"use strict";
const DATA_URL = "/data/agent-governance.json";
const stateLabels = { READY: "可发布验收", BLOCKED: "暂不可发布", UNKNOWN: "证据不足", STALE: "证据陈旧", PASS: "通过", FAIL: "失败", NOT_RUN: "未执行", WAIVED: "豁免" };
const byId = (id) => document.getElementById(id);
const safe = (value) => value === null || value === undefined || value === "" ? "—" : String(value);
const escapeHtml = (value) => safe(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[character]));
const safeClass = (value) => safe(value).replace(/[^A-Za-z0-9_-]/g, "-");
const status = (value) => `<span class="status-pill status-${safeClass(value)}">${escapeHtml(stateLabels[value] || value)}</span>`;
const emptyRow = (columns, text) => `<tr><td colspan="${columns}" class="empty">${escapeHtml(text)}</td></tr>`;

// v3 的公开投影刻意只披露可验证的发布结论，不携带运行、候选或私有摘要明细。
// 在这里做一次只读的展示适配：签名、新鲜度或本地时钟上的 expires_at 不成立时回落 STALE，
// 绝不借用旧 v1 字段猜绿色，也绝不把静态 READY 永久沿用。
const V3_STATES = new Set(["READY", "BLOCKED", "UNKNOWN", "STALE"]);
let projectionExpiryTimer = null;

function v3ProjectionExpiryEpoch(data) {
  const freshness = data && data.freshness && typeof data.freshness === "object" ? data.freshness : {};
  const evidence = freshness.evidence && typeof freshness.evidence === "object" ? freshness.evidence : {};
  const expiresAt = typeof evidence.expires_at === "string" ? Date.parse(evidence.expires_at) : NaN;
  return Number.isFinite(expiresAt) ? expiresAt : NaN;
}

function v3ProjectionIsFresh(data) {
  const freshness = data && data.freshness && typeof data.freshness === "object" ? data.freshness : {};
  const evidence = freshness.evidence && typeof freshness.evidence === "object" ? freshness.evidence : {};
  const expiresAt = v3ProjectionExpiryEpoch(data);
  return freshness.state === "CURRENT" && evidence.state === "CURRENT" && Number.isFinite(expiresAt) && Date.now() < expiresAt;
}

function scheduleProjectionExpiryRefresh(data) {
  if (projectionExpiryTimer !== null) {
    clearTimeout(projectionExpiryTimer);
    projectionExpiryTimer = null;
  }
  if (!data || data.schema_version !== 3) return;
  const expiresAt = v3ProjectionExpiryEpoch(data);
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) return;
  projectionExpiryTimer = window.setTimeout(() => {
    projectionExpiryTimer = null;
    load();
  }, Math.min(Math.max(0, expiresAt - Date.now()) + 20, 2147483647));
}

function v3ProjectionView(data) {
  if (!data || data.schema_version !== 3 || !V3_STATES.has(data.state)) return null;
  const freshness = data.freshness && typeof data.freshness === "object" ? data.freshness : {};
  const evidence = freshness.evidence && typeof freshness.evidence === "object" ? freshness.evidence : {};
  const signed = data.signature_state === "PASS";
  const fresh = v3ProjectionIsFresh(data);
  const decisionState = signed && fresh ? data.state : "STALE";
  const reasons = Array.isArray(data.reasons) ? data.reasons.filter((item) => typeof item === "string") : [];
  const blockers = decisionState === "READY" ? [] : [{
    code: decisionState === "STALE" ? "SIGNED_PROJECTION_STALE_OR_UNVERIFIED" : (reasons.join(" · ") || "SIGNED_PROJECTION_NONREADY"),
    title: stateLabels[decisionState] || "证据不足",
    state: decisionState,
  }];
  return {
    release_decision: { state: decisionState, label: stateLabels[decisionState] || "证据不足", blockers },
    metrics: {},
    pipeline: ["冻结 Release Subject", "独立签名 Gate", "只读公开投影"],
    runs: [],
    gates: signed ? [{
      verdict_id: data.verdict_id,
      verdict: "PASS",
      subject_commit: "—",
      artifact_digest: "—",
      acceptance_hash: "—",
      verified_at: evidence.verified_at || "—",
    }] : [],
    candidates: [],
    source: {
      authority: data.truth_source || "—",
      runtime_journal: "—",
      ttl_minutes: evidence.ttl_minutes ?? "—",
      evidence_fresh: signed && fresh,
      projection: "签名公开投影（最小披露）",
      reasons,
    },
    public_projection_v3: true,
    generated_at: data.observed_at || "—",
  };
}

function render(data) {
  data = v3ProjectionView(data) || (data && typeof data === "object" ? data : {});
  const publicProjectionV3 = data.public_projection_v3 === true;
  const decision = data.release_decision || { state: "UNKNOWN", label: "证据不足", blockers: [] };
  byId("decision-title").textContent = decision.label || stateLabels[decision.state] || "证据不足";
  byId("decision-state").innerHTML = status(decision.state || "UNKNOWN");
  byId("decision-copy").textContent = decision.state === "READY"
    ? (publicProjectionV3 ? "已签名的冻结 Release 结论通过；公开面仅披露必要的可验证字段。" : "冻结候选、构建物与全部验收 Oracle 已形成可追溯证据。")
    : "系统按 fail-closed 原则展示真实缺口；缺失或过期数据不会沿用旧绿色。";

  const m = data.metrics || {};
  byId("metric-runs").textContent = publicProjectionV3 ? "—" : safe(m.run_count ?? 0);
  byId("metric-gates").textContent = publicProjectionV3 ? "—" : safe(m.gate_count ?? 0);
  byId("metric-pass").textContent = publicProjectionV3 ? "—" : safe(m.pass_count ?? 0);
  byId("metric-candidates").textContent = publicProjectionV3 ? "—" : safe(m.candidate_count ?? 0);

  const blockers = Array.isArray(decision.blockers) ? decision.blockers : [];
  byId("blocker-grid").innerHTML = blockers.length ? blockers.map((item) => `
    <article class="card blocker ${safeClass(item.state)}">
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.code)} · ${escapeHtml(stateLabels[item.state] || item.state)}</p>
    </article>`).join("") : `<article class="card blocker"><h3>没有未关闭阻塞</h3><p>当前冻结验收条件均有有效证据。</p></article>`;

  const pipeline = Array.isArray(data.pipeline) ? data.pipeline : [];
  byId("pipeline").innerHTML = pipeline.map((name, index) => `<div class="pipeline-step"><b>阶段 ${index + 1}</b><span>${escapeHtml(name)}</span></div>`).join("");

  const runs = Array.isArray(data.runs) ? data.runs : [];
  byId("run-body").innerHTML = runs.length ? runs.map((run) => `<tr>
    <td><code>${escapeHtml(run.run_id)}</code></td><td>${escapeHtml(run.provider)}</td><td>${escapeHtml(run.task_id)}</td>
    <td>${status(run.gate_verdict || "UNKNOWN")}</td><td><code>${escapeHtml(run.candidate_commit)}</code></td><td>${escapeHtml(run.updated_at)}</td>
  </tr>`).join("") : emptyRow(6, publicProjectionV3 ? "公开投影按最小披露原则不含运行明细" : "尚无真实运行证据");

  const gates = Array.isArray(data.gates) ? data.gates : [];
  byId("gate-body").innerHTML = gates.length ? gates.map((gate) => `<tr>
    <td><code>${escapeHtml(gate.verdict_id)}</code></td><td>${status(gate.verdict || "UNKNOWN")}</td><td><code>${escapeHtml(gate.subject_commit)}</code></td>
    <td><code>${escapeHtml(gate.artifact_digest)}</code></td><td><code>${escapeHtml(gate.acceptance_hash)}</code></td><td>${escapeHtml(gate.verified_at)}</td>
  </tr>`).join("") : emptyRow(6, "尚无独立验收证据");

  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  byId("candidate-body").innerHTML = candidates.length ? candidates.map((candidate) => `<tr>
    <td>${escapeHtml(candidate.candidate_type)}</td><td>${escapeHtml(candidate.title)}</td><td>${escapeHtml(candidate.state)}</td>
    <td>${candidate.requires_owner_approval ? "需要" : "不需要"}</td><td><code>${escapeHtml(candidate.run_id)}</code></td><td>${escapeHtml(candidate.created_at)}</td>
  </tr>`).join("") : emptyRow(6, publicProjectionV3 ? "公开投影按最小披露原则不含候选明细" : "尚无经验候选；系统不会自动安装 Skill");

  const source = data.source || {};
  byId("source-meta").textContent = `权威层：${safe(source.authority)} · 运行日志：${safe(source.runtime_journal)} · 证据 TTL：${safe(source.ttl_minutes)} 分钟 · 生成：${safe(data.generated_at)}`;

  // 顶栏「治理」角标:与 index.html 同一套结论词,证据不新鲜一律回落 STALE,不沿用旧绿色。
  const navState = byId("nav-state");
  if (navState) {
    const effective = source.evidence_fresh ? (decision.state || "UNKNOWN") : "STALE";
    navState.textContent = stateLabels[effective] || "证据不足";
  }

  renderInfrastructure(source);
  byId("notice").classList.remove("show");
}

/* 基础设施事实边界:每一层「是权威还是投影」必须机器可分。
   角色是架构事实(写死),取值来自只读投影 —— 投影里没有的就显示未知,不猜、不补。 */
const AUTHORITY_LAYERS = [
  { name: "OVH 运行节点", role: "运行期短期事实：有界、可重建的 SQLite journal / outbox", key: "runtime_journal" },
  { name: "Private-Database", role: "完成态结构化事实的唯一权威；免 clone，put/get + 逐字节 readback", key: "authority" },
  { name: "Cloudflare R2", role: "原始加密会话与大文件：内容寻址 primary-objects/，回读摘要后删本地副本", key: null,
    fallback: "primary-objects/ · backups/private-database/" },
  { name: "OCI 异地灾备", role: "从 R2 再复制一份并独立恢复；缺失、多余、摘要不一致都必须为 0", key: null,
    fallback: "r2-d1-cold-backup" },
  { name: "status 只读投影", role: "只展示，不产生事实；缺失或陈旧一律 UNKNOWN / STALE", key: "projection" },
];
const FACT_FLOW = ["运行与采集", "结构化事实", "对象与冷备", "异地恢复", "只读展示"];

function renderInfrastructure(source) {
  const grid = byId("authority-grid");
  if (grid) {
    grid.innerHTML = AUTHORITY_LAYERS.map((layer) => {
      const value = layer.key ? source[layer.key] : layer.fallback;
      return `<article class="card authority">
        <h3><i aria-hidden="true"></i>${escapeHtml(layer.name)}</h3>
        <span class="role">${escapeHtml(layer.role)}</span>
        <span class="val">${value ? escapeHtml(value) : "未知（投影未提供）"}</span>
      </article>`;
    }).join("");
  }
  const flow = byId("fact-flow");
  if (flow) {
    flow.innerHTML = FACT_FLOW
      .map((step) => `<span class="step">${escapeHtml(step)}</span>`)
      .join('<span class="arrow" aria-hidden="true">→</span>');
  }
}

async function load() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    render(data);
    scheduleProjectionExpiryRefresh(data);
  } catch (error) {
    if (projectionExpiryTimer !== null) {
      clearTimeout(projectionExpiryTimer);
      projectionExpiryTimer = null;
    }
    render({ release_decision: { state: "UNKNOWN", label: "证据不足", blockers: [{ code: "PROJECTION_UNAVAILABLE", title: "只读投影暂不可用", state: "UNKNOWN" }] }, metrics: {}, pipeline: [], runs: [], gates: [], candidates: [], source: {} });
    byId("notice-text").textContent = `无法读取治理投影：${error.message}`;
    byId("notice").classList.add("show");
  }
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((item) => item.setAttribute("aria-selected", String(item === tab)));
  document.querySelectorAll(".panel").forEach((panel) => { panel.hidden = panel.id !== tab.dataset.panel; });
}));
byId("retry").addEventListener("click", load);

/* 主题切换:与 index.html 同一套 data-theme + localStorage,两页之间来回跳不会闪主题。
   本页没有 canvas,切完不需要重画,但键名必须一致,否则一站两种记忆。 */
(function () {
  const button = byId("themeBtn");
  if (!button) return;
  const SUN = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2.2M12 19.2v2.2M4.2 12H2M22 12h-2.2M6.3 6.3 4.8 4.8M19.2 19.2l-1.5-1.5M17.7 6.3l1.5-1.5M4.8 19.2l1.5-1.5"/></svg>';
  const MOON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a7 7 0 1 0 9 9 9 9 0 0 1-9-9Z"/></svg>';
  const systemDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
  const apply = (mode) => {
    const isDark = mode === "system" ? systemDark() : mode === "dark";
    if (mode === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    button.innerHTML = isDark ? SUN : MOON;
    const label = isDark ? "切换到浅色主题" : "切换到深色主题";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    return isDark;
  };
  const saved = localStorage.getItem("theme");
  let dark = apply(saved === "dark" || saved === "light" ? saved : "system");
  button.addEventListener("click", () => {
    const next = dark ? "light" : "dark";
    localStorage.setItem("theme", next);
    dark = apply(next);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!localStorage.getItem("theme")) dark = apply("system");
  });
})();

load();
