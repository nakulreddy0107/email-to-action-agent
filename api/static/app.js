// ==============================================================
// Mailroom SPA
// ==============================================================

// ---------- small DOM helpers ----------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return n;
};
const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso.slice(0, 16).replace("T", " "); }
};

// ---------- tabs ----------
$$(".nav-tab").forEach((t) => {
  t.addEventListener("click", () => {
    $$(".nav-tab").forEach((x) => x.classList.remove("active"));
    $$(".tab-panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $(`.tab-panel[data-panel="${t.dataset.tab}"]`).classList.add("active");
    if (t.dataset.tab === "pending") refreshPending();
    if (t.dataset.tab === "history") refreshHistory();
  });
});

// ---------- env badge ----------
fetch("/health").then((r) => r.json()).then((h) => {
  $("#env-model").textContent = `${h.model}${h.dry_run ? " · DRY RUN" : " · LIVE"}`;
});

// ==============================================================
// Sample emails
// ==============================================================
let SAMPLES = [];

async function loadSamples() {
  try {
    const r = await fetch("/api/samples");
    const d = await r.json();
    SAMPLES = d.samples || [];
    const list = $("#samples-list");
    list.innerHTML = "";
    SAMPLES.forEach((s) => {
      const chip = el("button",
        { class: "sample-chip", type: "button", onclick: () => applySample(s) },
        s.label,
      );
      list.appendChild(chip);
    });
  } catch (e) {
    console.error("sample load failed", e);
  }
}

function applySample(s) {
  $("#email-sender").value = s.sender;
  $("#email-subject").value = s.subject;
  $("#email-body").value = s.body;
}

loadSamples();

// ==============================================================
// Email form -> SSE streaming
// ==============================================================

$("#clear-btn").addEventListener("click", () => {
  $("#email-sender").value = "";
  $("#email-subject").value = "";
  $("#email-body").value = "";
  resetTimeline();
});

$("#email-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const payload = {
    sender: $("#email-sender").value.trim(),
    subject: $("#email-subject").value.trim(),
    body: $("#email-body").value.trim(),
  };
  if (!payload.sender || !payload.subject || !payload.body) return;
  await runPipeline(payload);
});

function resetTimeline() {
  const tl = $("#timeline");
  tl.innerHTML = `<div class="timeline-empty"><span class="empty-mark">—</span><p>Run an email to see the pipeline light up, one agent at a time.</p></div>`;
  $("#timeline-status").textContent = "Waiting";
  $("#timeline-status").className = "timeline-status";
  $("#run-summary").hidden = true;
  $("#summary-grid").innerHTML = "";
}

async function runPipeline(payload) {
  const btn = $("#run-btn");
  btn.disabled = true;
  btn.querySelector(".btn-label").textContent = "Running…";

  const tl = $("#timeline");
  tl.innerHTML = "";
  $("#run-summary").hidden = true;
  $("#summary-grid").innerHTML = "";

  const statusEl = $("#timeline-status");
  statusEl.textContent = "Running";
  statusEl.className = "timeline-status running";

  // Track totals for summary
  let intentCount = 0;
  let actionCount = 0;
  let executedCount = 0;
  let pendingCount = 0;

  // Running items, keyed by stage+agent, so we can flip "running" → "done"
  const running = new Map();

  try {
    // Use fetch streaming (compatible with POST + SSE-like body)
    const res = await fetch("/api/emails/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) throw new Error("stream failed: " + res.status);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // Parse SSE lines: "data: {...}\n\n"
      const events = buf.split("\n\n");
      buf = events.pop() || "";
      for (const raw of events) {
        const line = raw.trim();
        if (!line.startsWith("data:")) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        let evt;
        try { evt = JSON.parse(json); } catch { continue; }
        handleEvent(evt, running, (counts) => {
          intentCount += counts.intent || 0;
          actionCount += counts.action || 0;
          executedCount += counts.executed || 0;
          pendingCount += counts.pending || 0;
        });
      }
    }

    statusEl.textContent = "Done";
    statusEl.className = "timeline-status done";
    renderSummary({ intentCount, actionCount, executedCount, pendingCount });
    // Refresh pending count in top nav
    refreshPendingCount();
  } catch (e) {
    console.error(e);
    statusEl.textContent = "Error";
    statusEl.className = "timeline-status error";
    tl.appendChild(el("div", { class: "timeline-item error" },
      el("div", { class: "agent-label" }, "error"),
      el("div", { class: "agent-msg" }, String(e)),
    ));
  } finally {
    btn.disabled = false;
    btn.querySelector(".btn-label").textContent = "Run agents";
  }
}

function confBadge(conf) {
  const pct = (conf * 100).toFixed(0) + "%";
  const cls = conf >= 0.8 ? "conf-high" : conf >= 0.6 ? "conf-mid" : "conf-low";
  return `<span class="${cls}">${pct}</span>`;
}

function handleEvent(evt, running, tally) {
  const tl = $("#timeline");
  // Empty state cleanup
  const empty = tl.querySelector(".timeline-empty");
  if (empty) empty.remove();

  const key = `${evt.stage}:${evt.agent}:${evt.action_id || ""}`;

  // If we had a running item for this key, upgrade it to done/error
  if (["done", "error", "skipped"].includes(evt.status) && running.has(key)) {
    const node = running.get(key);
    node.classList.remove("running");
    node.classList.add(evt.status === "error" ? "error" : "done");
    updateItemDetails(node, evt);
    if (evt.status !== "error") {
      running.delete(key);
    }
    applyTally(evt, tally);
    return;
  }

  // Otherwise create a new item
  const node = el("div", { class: `timeline-item ${evt.status}` });
  node.appendChild(el("div", { class: "agent-label" }, `${evt.agent} · ${evt.stage}`));
  node.appendChild(el("div", { class: "agent-msg" }, stageSummary(evt)));
  const details = stageDetails(evt);
  if (details) node.appendChild(details);
  tl.appendChild(node);

  if (evt.status === "running") running.set(key, node);
  applyTally(evt, tally);
}

function updateItemDetails(node, evt) {
  // Replace the msg + details with the "done" version
  const msg = node.querySelector(".agent-msg");
  if (msg) msg.textContent = stageSummary(evt);
  // remove old detail(s), append new one
  node.querySelectorAll(".agent-detail, .intent-chip-wrap").forEach((n) => n.remove());
  const details = stageDetails(evt);
  if (details) node.appendChild(details);
}

function stageSummary(evt) {
  if (evt.stage === "start") return "Pipeline starting…";
  if (evt.stage === "end") return evt.status === "error" ? `Failed: ${evt.error}` : `Done · ${evt.total_actions || 0} action(s)`;
  if (evt.stage === "ingest" && evt.status === "done") return `Email parsed from ${evt.sender_name || evt.sender}`;
  if (evt.stage === "ingest") return evt.message || "Cleaning email…";
  if (evt.stage === "intent" && evt.status === "done") return `Found ${evt.intent_count} intent(s), sentiment: ${evt.sentiment}`;
  if (evt.stage === "intent") return evt.message || "Analyzing intent…";
  if (evt.stage === "plan" && evt.status === "done") return `Planned ${evt.tool} action`;
  if (evt.stage === "plan") return evt.message || "Planning…";
  if (evt.stage === "policy") return `${evt.verdict.replace("_", " ")} — ${evt.reason}`;
  if (evt.stage === "execute" && evt.status === "done") return evt.message || `Executed via ${evt.agent}`;
  if (evt.stage === "execute") return evt.message || `Calling ${evt.agent}…`;
  return evt.message || evt.status;
}

function stageDetails(evt) {
  if (evt.stage === "intent" && evt.status === "done" && evt.intents) {
    const wrap = el("div", { class: "intent-chip-wrap" });
    for (const i of evt.intents) {
      const chip = el("span", {
        class: "intent-chip",
        html: `${i.type} ${confBadge(i.confidence)} · ${escapeHtml(i.summary)}`,
      });
      wrap.appendChild(chip);
    }
    if (evt.llm_call) {
      const btn = el("button", {
        class: "prompt-inspect-btn",
        type: "button",
        onclick: () => openPromptInspector(evt.llm_call, "Intent Detection"),
      }, "↘ Inspect prompt");
      wrap.appendChild(btn);
    }
    return wrap;
  }
  if (evt.stage === "plan" && evt.status === "done" && evt.payload) {
    const wrap = el("div");
    const preview = previewPayload(evt.tool, evt.payload);
    if (preview) wrap.appendChild(el("div", { class: "agent-detail" }, preview));
    if (evt.llm_call) {
      const btn = el("button", {
        class: "prompt-inspect-btn",
        type: "button",
        onclick: () => openPromptInspector(evt.llm_call, "Reply Drafter"),
      }, "↘ Inspect prompt");
      wrap.appendChild(btn);
    }
    return wrap;
  }
  if (evt.stage === "execute" && evt.status === "done" && evt.external_url) {
    const d = el("div", { class: "agent-detail" });
    d.appendChild(el("a", { href: evt.external_url, target: "_blank", style: "color: var(--coral); text-decoration: none;" },
      `${evt.external_id || "link"} ↗`));
    return d;
  }
  return null;
}

function previewPayload(tool, payload) {
  try {
    if (tool === "jira") return `→ ${payload.summary} (priority: ${payload.priority || "—"})`;
    if (tool === "slack") return `→ ${payload.channel}: "${payload.text.slice(0, 80)}${payload.text.length > 80 ? "…" : ""}"`;
    if (tool === "calendar") return `→ ${payload.title} · ${payload.attendees?.length || 0} attendees`;
    if (tool === "email_reply") return `→ Reply draft · "${payload.subject}"`;
  } catch {}
  return null;
}

function applyTally(evt, tally) {
  if (evt.stage === "intent" && evt.status === "done") tally({ intent: evt.intent_count || 0 });
  if (evt.stage === "plan" && evt.status === "done") tally({ action: 1 });
  if (evt.stage === "execute" && evt.status === "done") {
    if (evt.outcome === "pending") tally({ pending: 1 });
    else if (evt.outcome === "executed" || evt.outcome === "dry_run") tally({ executed: 1 });
  }
  // Policy stage flags some actions as requiring human review — count as pending
  if (evt.stage === "policy" && evt.verdict === "require_human") tally({ pending: 1 });
}

function renderSummary({ intentCount, actionCount, executedCount, pendingCount }) {
  const grid = $("#summary-grid");
  grid.innerHTML = "";
  const cards = [
    { num: intentCount, lbl: "Intents detected" },
    { num: actionCount, lbl: "Actions planned" },
    { num: executedCount, lbl: "Executed" },
  ];
  if (pendingCount > 0) cards.push({ num: pendingCount, lbl: "Pending review" });
  cards.forEach((c) => {
    grid.appendChild(el("div", { class: "summary-card" },
      el("div", { class: "num" }, String(c.num)),
      el("div", { class: "lbl" }, c.lbl),
    ));
  });
  $("#run-summary").hidden = false;
}

// ==============================================================
// Pending approvals
// ==============================================================

async function refreshPending() {
  try {
    const r = await fetch("/api/actions/pending");
    const d = await r.json();
    renderPending(d.pending || []);
  } catch (e) {
    console.error(e);
  }
}

async function refreshPendingCount() {
  try {
    const r = await fetch("/api/actions/pending");
    const d = await r.json();
    const count = (d.pending || []).length;
    const pill = $("#pending-count");
    pill.textContent = count || "";
    pill.dataset.count = count;
  } catch {}
}
refreshPendingCount();

function renderPending(items) {
  const wrap = $("#pending-list");
  wrap.innerHTML = "";
  if (!items.length) {
    wrap.appendChild(el("div", { class: "empty-pane" }, "Nothing waiting. All clear."));
    return;
  }
  items.forEach((a) => {
    const card = el("div", { class: "pending-card" },
      el("span", { class: `tool-badge tool-${a.tool}` }, a.tool),
      el("div", { class: "pending-body" },
        el("div", { class: "rationale" }, a.rationale || a.intent_type),
        el("div", { class: "meta" }, `confidence ${(a.confidence * 100).toFixed(0)}% · ${a.intent_type}`),
        el("pre", {}, JSON.stringify(a.payload, null, 2)),
      ),
      el("div", { class: "pending-actions" },
        el("button", {
          class: "btn btn-approve btn-sm",
          onclick: () => approveAction(a.action_id),
        }, "Approve"),
        el("button", {
          class: "btn btn-reject btn-sm",
          onclick: () => rejectAction(a.action_id),
        }, "Reject"),
      ),
    );
    wrap.appendChild(card);
  });
}

async function approveAction(id) {
  const r = await fetch(`/api/actions/${id}/approve`, { method: "POST" });
  if (r.ok) {
    refreshPending();
    refreshPendingCount();
  } else {
    alert("Approve failed");
  }
}
async function rejectAction(id) {
  const r = await fetch(`/api/actions/${id}/reject`, { method: "POST" });
  if (r.ok) {
    refreshPending();
    refreshPendingCount();
  } else {
    alert("Reject failed");
  }
}

$("#refresh-pending").addEventListener("click", refreshPending);

// ==============================================================
// Run history
// ==============================================================

let HISTORY_Q = "";

async function refreshHistory() {
  const url = "/api/runs" + (HISTORY_Q ? `?q=${encodeURIComponent(HISTORY_Q)}` : "");
  try {
    const r = await fetch(url);
    const d = await r.json();
    renderHistory(d.runs || []);
  } catch (e) {
    console.error(e);
  }
}

function renderHistory(runs) {
  const tbody = $("#history-tbody");
  tbody.innerHTML = "";
  if (!runs.length) {
    const tr = el("tr", {}, el("td", { colspan: "6", style: "text-align:center; padding: 40px; color: var(--muted); font-family: var(--font-serif); font-style: italic;" }, "No runs yet."));
    tbody.appendChild(tr);
    return;
  }
  runs.forEach((r) => {
    const allDone = r.executed_count === r.action_count && r.action_count > 0;
    const tr = el("tr", { onclick: () => openRunDetail(r.id) },
      el("td", { class: "time-cell" }, fmtTime(r.processed_at)),
      el("td", {}, r.sender || "—"),
      el("td", {}, r.subject || "—"),
      el("td", {}, String(r.intent_count || 0)),
      el("td", {}, el("span", { class: `badge ${allDone ? "" : "partial"}` }, `${r.executed_count || 0}/${r.action_count || 0}`)),
      el("td", { style: "color: var(--coral);" }, "view →"),
    );
    tbody.appendChild(tr);
  });
}

$("#history-search").addEventListener("input", (ev) => {
  HISTORY_Q = ev.target.value;
  clearTimeout(window._hq);
  window._hq = setTimeout(refreshHistory, 200);
});

// ==============================================================
// Run detail modal
// ==============================================================

async function openRunDetail(emailId) {
  const r = await fetch(`/api/runs/${emailId}`);
  if (!r.ok) return;
  const d = await r.json();
  renderDetail(d);
}

function renderDetail(d) {
  const c = $("#modal-content");
  const em = d.email || {};
  const intents = d.intents || [];
  const actions = d.actions || [];

  c.innerHTML = `
    <div class="modal-content">
      <h2>${escapeHtml(em.subject || "(no subject)")}</h2>
      <div class="sub">${escapeHtml(em.sender || "")} · ${fmtTime(em.received_at)}</div>

      <h3>Email body</h3>
      <div class="block"><pre>${escapeHtml(em.body || "")}</pre></div>

      <h3>Detected intents (${intents.length})</h3>
      ${intents.map((i) => `
        <div class="block">
          <div><strong>${escapeHtml(i.intent_type)}</strong> · ${confBadge(i.confidence)}</div>
          <div style="font-size: 13px; color: var(--ink-2); margin-top: 4px;">${escapeHtml(i.summary)}</div>
          <pre>${escapeHtml(i.entities_json || "{}")}</pre>
        </div>
      `).join("")}

      <h3>Actions (${actions.length})</h3>
      ${actions.map((a) => `
        <div class="block">
          <div>
            <span class="tool-badge tool-${a.tool}" style="font-family: var(--font-mono); font-size: 10px; padding: 3px 8px; border-radius: 4px; border: 1px solid;">${escapeHtml(a.tool)}</span>
            <span style="margin-left: 8px; color: var(--ink-2); font-size: 13px;">${escapeHtml(a.status || "")}</span>
            ${a.external_url ? `<a href="${escapeHtml(a.external_url)}" target="_blank" style="margin-left: 8px; color: var(--coral);">${escapeHtml(a.external_id || "link")} ↗</a>` : ""}
          </div>
          <div style="font-size: 12px; color: var(--muted); margin-top: 4px;">${escapeHtml(a.message || "")}</div>
          <pre>${escapeHtml(a.payload_json || "{}")}</pre>
        </div>
      `).join("")}
    </div>
  `;
  $("#detail-modal").hidden = false;
}

$("#modal-close").addEventListener("click", () => { $("#detail-modal").hidden = true; });
$(".modal-backdrop").addEventListener("click", () => { $("#detail-modal").hidden = true; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#detail-modal").hidden = true;
});

// ==============================================================
// Prompt inspector — shows the LLM call's system prompt, user prompt,
// and raw JSON response. This is the "demystify the LLM" feature.
// ==============================================================

function openPromptInspector(call, label) {
  const c = $("#modal-content");
  const tokens = call.tokens_in != null
    ? `${call.tokens_in} in · ${call.tokens_out} out · ${call.elapsed_ms}ms`
    : `${call.elapsed_ms}ms`;

  c.innerHTML = `
    <div class="modal-content prompt-inspector">
      <h2>${escapeHtml(label)} — LLM call</h2>
      <div class="sub">model: <code>${escapeHtml(call.model)}</code> · ${tokens}</div>

      <div class="inspector-tabs">
        <button class="ins-tab active" data-ins="system">System prompt</button>
        <button class="ins-tab" data-ins="user">User prompt</button>
        <button class="ins-tab" data-ins="response">Response</button>
      </div>

      <div class="ins-pane active" data-ins-pane="system"><pre>${escapeHtml(call.system_prompt)}</pre></div>
      <div class="ins-pane" data-ins-pane="user"><pre>${escapeHtml(call.user_prompt)}</pre></div>
      <div class="ins-pane" data-ins-pane="response"><pre>${escapeHtml(JSON.stringify(call.response, null, 2))}</pre></div>
    </div>
  `;

  // Wire up tabs
  c.querySelectorAll(".ins-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      c.querySelectorAll(".ins-tab").forEach((b) => b.classList.remove("active"));
      c.querySelectorAll(".ins-pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      c.querySelector(`.ins-pane[data-ins-pane="${btn.dataset.ins}"]`).classList.add("active");
    });
  });

  $("#detail-modal").hidden = false;
}
