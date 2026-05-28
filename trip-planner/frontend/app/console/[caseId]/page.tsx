"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  CopilotKit,
  useCoAgent,
  useCopilotAction,
  useCopilotChat,
} from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { KIND_LABELS, updateCase, useCase, type CaseKind } from "../../../lib/cases";
import { appendAudit, useAuditFor, type AuditEntry } from "../../../lib/audit";

// ---------- Per-kind config ----------

const AGENT_BY_KIND: Record<CaseKind, string> = {
  "settlement-break": "settlementBreak",
  "margin-dispute": "tripPlanner",
  reconciliation: "tripPlanner",
  "kyc-refresh": "tripPlanner",
};

// ---------- Trip-planner panel (used as a placeholder for non-settlement kinds) ----------

type Stop = { city: string; days: number };
type Itinerary = { stops: Stop[] };

function TripPlannerPanel({ caseId }: { caseId: string }) {
  const { state, setState } = useCoAgent<Itinerary>({
    name: "tripPlanner",
    initialState: { stops: [] },
  });

  useCopilotAction({
    name: "updateItinerary",
    description: "Add or remove stops from the trip itinerary.",
    parameters: [
      { name: "op", type: "string", enum: ["add", "remove", "clear"], required: true },
      { name: "city", type: "string", required: false },
      { name: "days", type: "number", required: false },
      { name: "index", type: "number", required: false },
    ],
    handler: ({ op, city, days, index }) => {
      const stops = [...(state?.stops ?? [])];
      if (op === "add" && city) stops.push({ city, days: days ?? 1 });
      else if (op === "remove" && typeof index === "number") stops.splice(index, 1);
      else if (op === "clear") stops.length = 0;
      setState({ stops });
      updateCase(caseId, { status: "running", currentStep: `Itinerary updated (${stops.length} stops)` });
      appendAudit(caseId, "agent", `Itinerary ${op}`, op === "add" ? `${city} (${days ?? 1}d)` : op === "remove" ? `index ${index}` : "all");
      return `Itinerary now has ${stops.length} stop(s).`;
    },
  });

  useCopilotAction({
    name: "confirmBooking",
    description: "Ask the user to approve booking the current itinerary.",
    parameters: [{ name: "summary", type: "string", required: true }],
    renderAndWaitForResponse: ({ args, respond, status }) => (
      <ApprovalCard
        caseId={caseId}
        title="Confirm booking"
        summary={(args as { summary: string }).summary}
        respond={respond}
        status={status as string}
        stepLabel="Awaiting booking approval"
        actionLabel="booking"
      />
    ),
  });

  return (
    <section>
      <SectionTitle>Proposed itinerary</SectionTitle>
      {(state?.stops?.length ?? 0) === 0 ? (
        <p style={{ color: "#777", fontSize: 13 }}>Empty. Ask the agent to add stops.</p>
      ) : (
        <ol style={{ paddingLeft: 18, margin: 0 }}>
          {state!.stops.map((s, i) => (
            <li key={i} style={{ margin: "4px 0" }}>
              {s.city} — {s.days} day{s.days === 1 ? "" : "s"}
            </li>
          ))}
        </ol>
      )}
      <p style={{ marginTop: 14, fontSize: 11, color: "#999" }}>
        Demo agent. In production this slot renders a domain-specific component per case kind.
      </p>
    </section>
  );
}

// ---------- Settlement-break panel ----------

type Resolution = {
  tradeId: string | null;
  status: "investigating" | "identified" | "drafted" | "sent" | null;
  rootCause: string | null;
  proposedAction: string | null;
  counterpartyDraft: string | null;
  counterparty: string | null;
};

const INITIAL_RESOLUTION: Resolution = {
  tradeId: null,
  status: null,
  rootCause: null,
  proposedAction: null,
  counterpartyDraft: null,
  counterparty: null,
};

function SettlementBreakPanel({ caseId }: { caseId: string }) {
  const { state, setState } = useCoAgent<Resolution>({
    name: "settlementBreak",
    initialState: INITIAL_RESOLUTION,
  });

  useCopilotAction({
    name: "updateResolution",
    description: "Update the case resolution state as the investigation progresses.",
    parameters: [
      { name: "tradeId", type: "string", required: false },
      { name: "status", type: "string", enum: ["investigating", "identified", "drafted", "sent"], required: false },
      { name: "rootCause", type: "string", required: false },
      { name: "proposedAction", type: "string", required: false },
      { name: "counterpartyDraft", type: "string", required: false },
      { name: "counterparty", type: "string", required: false },
    ],
    handler: (patch) => {
      const next = { ...(state ?? INITIAL_RESOLUTION), ...patch };
      setState(next);
      updateCase(caseId, {
        status: "running",
        currentStep:
          patch.status === "identified" ? "Root cause identified" :
          patch.status === "drafted" ? "Counterparty message drafted" :
          patch.status === "sent" ? "Counterparty contacted" :
          "Investigating",
      });
      appendAudit(caseId, "agent", `Resolution updated → ${patch.status ?? "patch"}`,
        patch.rootCause || patch.proposedAction || patch.tradeId || undefined);
      return "Resolution state updated.";
    },
  });

  useCopilotAction({
    name: "confirmCounterpartyContact",
    description: "Ask the operator to approve sending the drafted message to the counterparty.",
    parameters: [{ name: "summary", type: "string", required: true }],
    renderAndWaitForResponse: ({ args, respond, status }) => (
      <ApprovalCard
        caseId={caseId}
        title="Send to counterparty?"
        summary={(args as { summary: string }).summary}
        respond={(v) => {
          if (v === "approved") setState({ ...(state ?? INITIAL_RESOLUTION), status: "sent" });
          respond?.(v);
        }}
        status={status as string}
        stepLabel="Awaiting send approval"
        actionLabel="counterparty contact"
      />
    ),
  });

  const r = state ?? INITIAL_RESOLUTION;
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <SectionTitle>Resolution</SectionTitle>
      <Field label="Trade ID" value={r.tradeId} />
      <Field label="Status" value={r.status} highlight={r.status === "sent" ? "#16a34a" : undefined} />
      <Field label="Root cause" value={r.rootCause} multiline />
      <Field label="Proposed action" value={r.proposedAction} multiline />
      <Field label="Counterparty" value={r.counterparty} />
      {r.counterpartyDraft && (
        <div>
          <div style={fieldLabel}>Draft message</div>
          <pre style={{
            background: "#fafafa", border: "1px solid #eee", borderRadius: 4,
            padding: 10, margin: "4px 0 0", fontSize: 12, whiteSpace: "pre-wrap",
          }}>{r.counterpartyDraft}</pre>
        </div>
      )}
      {!r.tradeId && (
        <p style={{ fontSize: 12, color: "#888" }}>
          Try: "Investigate T-100012" or "Investigate T-100013".
        </p>
      )}
    </section>
  );
}

const fieldLabel: React.CSSProperties = {
  fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "#888",
};

function Field({ label, value, multiline, highlight }: { label: string; value: string | null; multiline?: boolean; highlight?: string }) {
  return (
    <div>
      <div style={fieldLabel}>{label}</div>
      <div style={{
        marginTop: 2, fontSize: 13, color: value ? (highlight ?? "#111") : "#bbb",
        whiteSpace: multiline ? "normal" : "nowrap", overflow: multiline ? "visible" : "hidden", textOverflow: "ellipsis",
        fontWeight: highlight ? 600 : 400,
      }}>
        {value ?? "—"}
      </div>
    </div>
  );
}

// ---------- Shared approval card ----------

function ApprovalCard({
  caseId, title, summary, respond, status, stepLabel, actionLabel,
}: {
  caseId: string;
  title: string;
  summary: string;
  respond?: (value: string) => void;
  status: string;
  stepLabel: string;
  actionLabel: string;
}) {
  useEffect(() => {
    if (status === "executing" || status === "inProgress") {
      updateCase(caseId, {
        status: "blocked", currentStep: stepLabel, pendingApprovals: 1, blockedSince: Date.now(),
      });
      appendAudit(caseId, "agent", `Requested approval: ${actionLabel}`, summary);
    } else if (status === "complete") {
      updateCase(caseId, {
        status: "running", currentStep: "Decision received", pendingApprovals: 0, blockedSince: null,
      });
    }
  }, [status, caseId, stepLabel, actionLabel, summary]);

  if (status === "complete") {
    return <div style={{ padding: 8, color: "#444", fontSize: 13 }}>Decision recorded.</div>;
  }

  const decide = (value: "approved" | "rejected") => {
    appendAudit(caseId, "operator", `${actionLabel} ${value}`, summary);
    respond?.(value);
  };

  return (
    <div style={{ padding: 12, border: "1px solid #d4b800", borderRadius: 8, background: "#fffbe6" }}>
      <p style={{ margin: "0 0 4px", fontWeight: 600 }}>{title}</p>
      <p style={{ margin: "0 0 12px", color: "#555", fontSize: 13 }}>{summary}</p>
      <button onClick={() => decide("approved")} style={btn("#16a34a")}>Approve</button>
      <button onClick={() => decide("rejected")} style={btn("#dc2626")}>Reject</button>
    </div>
  );
}

// ---------- Reasoning trace ----------

type TraceEvent =
  | { kind: "user"; id: string; text: string }
  | { kind: "agent-text"; id: string; text: string }
  | { kind: "tool-call"; id: string; name: string; args: unknown; result?: unknown };

function deriveTrace(messages: any[]): TraceEvent[] {
  const out: TraceEvent[] = [];
  const toolById = new Map<string, TraceEvent & { kind: "tool-call" }>();
  for (const m of messages) {
    const id: string = m.id ?? Math.random().toString(36);
    const role: string | undefined = m.role ?? m.type;
    if (typeof m.isTextMessage === "function" && m.isTextMessage()) {
      out.push({ kind: role === "user" ? "user" : "agent-text", id, text: m.content ?? "" });
      continue;
    }
    if (typeof m.isActionExecutionMessage === "function" && m.isActionExecutionMessage()) {
      const ev: TraceEvent & { kind: "tool-call" } = {
        kind: "tool-call", id, name: m.name ?? "tool", args: m.arguments ?? m.args ?? {},
      };
      toolById.set(id, ev);
      out.push(ev);
      continue;
    }
    if (typeof m.isResultMessage === "function" && m.isResultMessage()) {
      const target = toolById.get(m.actionExecutionId);
      if (target) target.result = m.result;
      continue;
    }
    if (role === "user" && typeof m.content === "string") {
      out.push({ kind: "user", id, text: m.content });
    } else if (role === "assistant" && typeof m.content === "string" && m.content.length > 0) {
      out.push({ kind: "agent-text", id, text: m.content });
    } else if (m.name) {
      out.push({ kind: "tool-call", id, name: m.name, args: m.arguments ?? m.args ?? {}, result: m.result });
    }
  }
  return out;
}

function ReasoningTrace() {
  const chat = useCopilotChat();
  const messages: any[] = (chat as any)?.visibleMessages ?? (chat as any)?.messages ?? [];
  const events = useMemo(() => deriveTrace(messages), [messages]);
  return (
    <section style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <SectionTitle>Reasoning trace</SectionTitle>
      <div style={{ overflowY: "auto", flex: 1, paddingRight: 4 }}>
        {events.length === 0 ? (
          <p style={{ color: "#777", fontSize: 13 }}>
            No agent activity yet. Send a message in the chat to populate the trace.
          </p>
        ) : (
          <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {events.map((e, i) => <TraceItem key={e.id} ev={e} index={i + 1} />)}
          </ol>
        )}
      </div>
      <p style={{ fontSize: 11, color: "#999", margin: "8px 0 0" }}>
        Derived from <code>useCopilotChat()</code> — one row per AG-UI message.
      </p>
    </section>
  );
}

function TraceItem({ ev, index }: { ev: TraceEvent; index: number }) {
  const [open, setOpen] = useState(false);
  const meta = ev.kind === "user" ? { label: "User", color: "#2563eb" }
    : ev.kind === "agent-text" ? { label: "Agent", color: "#111" }
    : { label: `Tool: ${ev.name}`, color: "#7c3aed" };
  return (
    <li style={{ padding: "8px 10px", margin: "6px 0", borderLeft: `3px solid ${meta.color}`, background: "#fafafa", borderRadius: 4, fontSize: 13 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ color: meta.color, fontWeight: 600 }}>{index}. {meta.label}</span>
        {ev.kind === "tool-call" && (
          <button onClick={() => setOpen((o) => !o)} style={{ fontSize: 11, background: "transparent", border: "1px solid #ddd", padding: "2px 6px", borderRadius: 4, cursor: "pointer" }}>
            {open ? "hide" : "args/result"}
          </button>
        )}
      </div>
      {ev.kind !== "tool-call" && <div style={{ marginTop: 4, color: "#222", whiteSpace: "pre-wrap" }}>{ev.text}</div>}
      {ev.kind === "tool-call" && open && (
        <pre style={{ marginTop: 6, padding: 8, background: "#0b1021", color: "#cfd8ff", borderRadius: 4, fontSize: 11, overflowX: "auto" }}>
{JSON.stringify({ args: ev.args, result: ev.result }, null, 2)}
        </pre>
      )}
    </li>
  );
}

// ---------- Audit log ----------

function AuditStrip({ caseId }: { caseId: string }) {
  const log = useAuditFor(caseId);
  const [expanded, setExpanded] = useState(false);
  const reversed = useMemo(() => [...log].reverse(), [log]);
  const shown = expanded ? reversed : reversed.slice(0, 3);
  return (
    <footer style={{ borderTop: "1px solid #eee", background: "#fafafa", padding: "10px 20px", fontSize: 12, color: "#444" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Audit log ({log.length})</strong>
        <button onClick={() => setExpanded((x) => !x)} style={{ fontSize: 11, background: "transparent", border: "1px solid #ddd", padding: "2px 8px", borderRadius: 4, cursor: "pointer" }}>
          {expanded ? "collapse" : "show all"}
        </button>
      </div>
      {log.length === 0 ? (
        <p style={{ color: "#999", margin: "6px 0 0" }}>No actions recorded yet.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "6px 0 0", maxHeight: expanded ? 180 : "auto", overflowY: expanded ? "auto" : "visible" }}>
          {shown.map((e) => <AuditRow key={e.id} e={e} />)}
        </ul>
      )}
    </footer>
  );
}

function AuditRow({ e }: { e: AuditEntry }) {
  const t = new Date(e.timestamp);
  const color = e.actor === "operator" ? "#16a34a" : e.actor === "agent" ? "#7c3aed" : "#6b7280";
  return (
    <li style={{ display: "flex", gap: 10, padding: "3px 0", alignItems: "baseline" }}>
      <span style={{ color: "#888", fontVariantNumeric: "tabular-nums", minWidth: 70 }}>{t.toLocaleTimeString()}</span>
      <span style={{ color, fontWeight: 600, textTransform: "uppercase", fontSize: 10, minWidth: 70 }}>{e.actor}</span>
      <span>{e.action}</span>
      {e.details && <span style={{ color: "#777" }}>— {e.details}</span>}
    </li>
  );
}

// ---------- Layout ----------

function btn(bg: string): React.CSSProperties {
  return { background: bg, color: "white", border: 0, padding: "6px 12px", marginRight: 8, borderRadius: 6, cursor: "pointer" };
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 style={{ margin: "0 0 10px", fontSize: 12, textTransform: "uppercase", letterSpacing: 0.6, color: "#666" }}>{children}</h3>;
}

function CaseHeader({ caseId }: { caseId: string }) {
  const c = useCase(caseId);
  if (!c) return null;
  return (
    <header style={{ padding: "14px 20px", borderBottom: "1px solid #eee", display: "flex", alignItems: "center", justifyContent: "space-between", background: "white" }}>
      <div>
        <Link href="/console" style={{ color: "#666", fontSize: 13, textDecoration: "none" }}>← Console</Link>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginTop: 2 }}>
          <strong>{c.id}</strong>
          <span style={{ color: "#888", fontSize: 13 }}>{KIND_LABELS[c.kind]}</span>
          <span style={{ fontSize: 11, color: "#999", padding: "2px 6px", border: "1px solid #ddd", borderRadius: 4 }}>
            agent: {AGENT_BY_KIND[c.kind]}
          </span>
        </div>
        <div style={{ marginTop: 4, fontSize: 15 }}>{c.title}</div>
      </div>
      <div style={{ textAlign: "right", fontSize: 12, color: "#666" }}>
        <div>Owner: <b>{c.owner}</b></div>
        <div>Status: <b style={{ color: c.status === "blocked" ? "#dc2626" : "#111" }}>{c.status}</b></div>
        <div style={{ marginTop: 2 }}>{c.currentStep}</div>
      </div>
    </header>
  );
}

export default function CaseDetailPage() {
  const params = useParams<{ caseId: string }>();
  const caseId = params.caseId;
  const c = useCase(caseId);
  const agentName = c ? AGENT_BY_KIND[c.kind] : "tripPlanner";

  // Key the provider on agentName so switching cases of different kinds
  // re-instantiates with the right agent.
  return (
    <CopilotKit
      key={`${agentName}-${caseId}`}
      runtimeUrl="/api/copilotkit"
      agent={agentName}
      threadId={caseId}
    >
      <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
        <CaseHeader caseId={caseId} />
        <div style={{ display: "grid", gridTemplateColumns: "340px 1fr 1fr", flex: 1, minHeight: 0 }}>
          <div style={{ padding: 16, overflowY: "auto", borderRight: "1px solid #eee" }}>
            {c?.kind === "settlement-break" ? (
              <SettlementBreakPanel caseId={caseId} />
            ) : (
              <TripPlannerPanel caseId={caseId} />
            )}
          </div>
          <div style={{ padding: 16, minHeight: 0, borderRight: "1px solid #eee" }}>
            <ReasoningTrace />
          </div>
          <div style={{ minHeight: 0 }}>
            <CopilotChat
              instructions="Help the operator resolve this case."
              labels={{
                title: c ? KIND_LABELS[c.kind] : "Agent",
                initial: c?.kind === "settlement-break"
                  ? "Give me a trade ID to investigate (e.g. T-100012)."
                  : "What would you like me to do?",
              }}
            />
          </div>
        </div>
        <AuditStrip caseId={caseId} />
      </div>
    </CopilotKit>
  );
}
