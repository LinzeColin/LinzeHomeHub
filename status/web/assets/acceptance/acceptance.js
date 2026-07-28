"use strict";

const DATA_URL = "data/acceptance/chatgpt_latest.json";

function $(id) {
  return document.getElementById(id);
}

function statusClass(value) {
  return String(value || "UNKNOWN").toLowerCase().replaceAll("_", "-");
}

function addDefinition(container, key, value) {
  const wrap = document.createElement("div");
  const dt = document.createElement("dt");
  const dd = document.createElement("dd");
  dt.textContent = key;
  dd.textContent = value == null || value === "" ? "—" : String(value);
  wrap.append(dt, dd);
  container.append(wrap);
}

function addListItems(container, items) {
  for (const item of items || []) {
    const li = document.createElement("li");
    li.textContent = String(item);
    container.append(li);
  }
}

function render(data) {
  const verdict = data.verdict || {};
  const reviewer = data.reviewer || {};
  const subject = data.subject || {};
  const pageRuntime = data.page_runtime || {};

  const badge = $("verdict-badge");
  badge.textContent = verdict.status || "UNKNOWN";
  badge.className = "verdict " + statusClass(verdict.status);

  $("complete-value").textContent = verdict.product_complete === true ? "是" :
    verdict.product_complete === false ? "否" : "未知";
  $("taskpack-value").textContent = subject.taskpack_version || "—";
  $("review-date-value").textContent = reviewer.review_date || "—";
  $("blocker-count-value").textContent = String((data.blocking_findings || []).length);

  const reviewerGrid = $("reviewer-grid");
  reviewerGrid.replaceChildren();
  addDefinition(reviewerGrid, "验收者", reviewer.display_name);
  addDefinition(reviewerGrid, "提供方", reviewer.provider);
  addDefinition(reviewerGrid, "模型", reviewer.model);
  addDefinition(reviewerGrid, "角色", reviewer.role);
  addDefinition(reviewerGrid, "是否人类", reviewer.is_human === true ? "是" : "否");
  addDefinition(reviewerGrid, "是否运行组件", reviewer.is_runtime_component === true ? "是" : "否");
  addDefinition(reviewerGrid, "页面 Agent 依赖", pageRuntime.agent_dependency === false ? "0" : "未知");
  addDefinition(reviewerGrid, "页面模型调用 / Token", `${pageRuntime.llm_calls ?? "未知"} / ${pageRuntime.token_consumption ?? "未知"}`);

  const limitations = $("limitations-list");
  limitations.replaceChildren();
  addListItems(limitations, reviewer.limitations);

  const subjectGrid = $("subject-grid");
  subjectGrid.replaceChildren();
  addDefinition(subjectGrid, "产品", subject.product);
  addDefinition(subjectGrid, "仓库", subject.repository);
  addDefinition(subjectGrid, "TaskPack", subject.taskpack_version);
  addDefinition(subjectGrid, "TaskPack SHA-256", subject.taskpack_sha256);
  addDefinition(subjectGrid, "候选 Commit", subject.documented_candidate_commit);
  addDefinition(subjectGrid, "部署 Artifact SHA-256", subject.documented_artifact_sha256);

  $("verdict-summary").textContent = verdict.summary || "没有验收摘要。";
  $("pass-rule").textContent = verdict.pass_rule || "缺少 PASS 规则，当前必须视为 UNKNOWN。";

  const domainTable = $("domain-table");
  domainTable.replaceChildren();
  for (const row of data.domain_verdicts || []) {
    const tr = document.createElement("tr");
    const tdDomain = document.createElement("td");
    const tdStatus = document.createElement("td");
    const tdReason = document.createElement("td");
    const pill = document.createElement("span");
    tdDomain.textContent = row.domain || "—";
    pill.textContent = row.status || "UNKNOWN";
    pill.className = "status " + statusClass(row.status);
    tdStatus.append(pill);
    tdReason.textContent = row.reason || "—";
    tr.append(tdDomain, tdStatus, tdReason);
    domainTable.append(tr);
  }

  const blockerList = $("blocker-list");
  blockerList.replaceChildren();
  for (const item of data.blocking_findings || []) {
    const box = document.createElement("article");
    const title = document.createElement("b");
    const closure = document.createElement("p");
    box.className = "blocker";
    title.textContent = `${item.id || "P0"} · ${item.title || "未命名阻断"}`;
    closure.textContent = `关闭条件：${item.closure || "未定义"}`;
    box.append(title, closure);
    blockerList.append(box);
  }

  const ownerSteps = $("owner-steps");
  ownerSteps.replaceChildren();
  addListItems(ownerSteps, data.owner_acceptance_steps);

  const commands = $("command-list");
  commands.replaceChildren();
  for (const command of data.developer_commands || []) {
    const pre = document.createElement("pre");
    pre.className = "command";
    pre.textContent = command;
    commands.append(pre);
  }

  const evidenceLinks = $("evidence-links");
  evidenceLinks.replaceChildren();
  for (const entry of data.evidence_links || []) {
    const li = document.createElement("li");
    const link = document.createElement("a");
    const url = new URL(entry.url, window.location.origin);
    if (!["https:", "http:"].includes(url.protocol)) {
      continue;
    }
    link.href = url.href;
    link.textContent = entry.label || url.href;
    link.rel = "noopener noreferrer";
    link.target = "_blank";
    li.append(link);
    evidenceLinks.append(li);
  }

  $("record-id").textContent = `记录：${data.record_id || "—"}`;
  $("generated-at").textContent = `生成：${data.generated_at || "—"}`;
}

function renderUnknown(error) {
  $("load-error").hidden = false;
  const badge = $("verdict-badge");
  badge.textContent = "UNKNOWN";
  badge.className = "verdict unknown";
  $("complete-value").textContent = "未知";
  $("taskpack-value").textContent = "数据不可用";
  console.error("acceptance-data-error", error);
}

async function load() {
  try {
    const response = await fetch(`${DATA_URL}?t=${Date.now()}`, {
      cache: "no-store",
      credentials: "same-origin"
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    if (data.schema_version !== 1 || data.public_safe !== true) {
      throw new Error("schema/public_safe validation failed");
    }
    render(data);
  } catch (error) {
    renderUnknown(error);
  }
}

document.addEventListener("DOMContentLoaded", load);
