/**
 * ============================================================================
 *  HITL WORKBENCH — A TEACHING UI
 * ============================================================================
 *  One self-contained file that DEMONSTRATES every frontend pattern you'd
 *  use for an agentic finance HITL application. Read it top-to-bottom like
 *  a lesson. Each section is labelled with the PATTERN it teaches and WHY
 *  it matters in an interview.
 *
 *  Runs on plain React 17 — no extra deps. The three state layers
 *  (server / UI / event) are implemented inline in miniature so you can
 *  SEE the architecture instead of importing a black box.
 *
 *  THE THESIS (say this in the interview):
 *    "The frontend job isn't rendering chat — it's representing a durable
 *     state machine honestly: stream the agent's reasoning, surface
 *     uncertainty as a first-class state, make approval a workflow gate
 *     not a button, and keep every render correct under live data."
 * ============================================================================
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
} from "react";
import {
  AGUIEvent,
  AGUIEventType,
  ApprovalGate,
  UserRole,
  WorkflowInstance,
  WorkflowListItem,
  WorkflowState,
  WorkflowType,
} from "../types/domain";

/* ===========================================================================
 *  PATTERN 1 — TYPE SAFETY: DISCRIMINATED UNIONS + EXHAUSTIVE `never`
 * ===========================================================================
 *  Every live event is a discriminated union keyed by `event_type`. When we
 *  render or reduce an event, we `switch` on that key. The `default: never`
 *  branch means: if anyone adds a new event type to the union and forgets to
 *  handle it here, the COMPILER fails the build. Bugs become type errors.
 *
 *  Interview line: "I don't handle events with if/else and hope — I use a
 *  discriminated union with an exhaustive switch, so a new event type is a
 *  compile error until someone handles it."
 * ========================================================================= */

/** Compile-time guarantee that every case is handled. */
function assertNever(x: never): never {
  throw new Error(`Unhandled event type: ${JSON.stringify(x)}`);
}

/** Human-readable label for an event — note the exhaustive switch. */
function describeEvent(e: AGUIEvent): string {
  const t: AGUIEventType = e.event_type;
  switch (t) {
    case "CONNECTED":              return "🔌 Stream connected";
    case "RUN_STARTED":            return "▶️  Agent run started";
    case "RUN_FINISHED":           return "⏹️  Agent run finished";
    case "TEXT_MESSAGE_CONTENT":   return "💬 Agent reasoning…";
    case "TOOL_CALL_START":        return "🛠️  Calling tool…";
    case "TOOL_CALL_END":          return "✅ Tool returned";
    case "STATE_SNAPSHOT":         return "📸 Full state snapshot";
    case "STATE_DELTA":            return "➕ State delta";
    case "APPROVAL_REQUEST":       return "🙋 Approval requested";
    case "APPROVAL_GRANTED":       return "🟢 Approval granted";
    case "APPROVAL_DENIED":        return "🔴 Approval denied";
    case "OVERRIDE_INVOKED":       return "🚨 Break-glass override";
    case "COUNTERPARTY_INPUT_REQUIRED": return "📨 Counterparty input needed";
    case "REG_FILING_DRAFTED":     return "📄 Regulatory filing drafted";
    case "EVIDENCE_PINNED":        return "📌 Evidence pinned";
    case "WORKFLOW_STATE_CHANGED": return "🔁 Workflow state changed";
    case "AUDIT_EVENT":            return "🔐 Audit entry written";
    default:
      // If a new AGUIEventType is added, this line stops compiling.
      return assertNever(t);
  }
}

/* ===========================================================================
 *  PATTERN 2 — THE THREE STATE LAYERS
 * ===========================================================================
 *  Three KINDS of state → three TOOLS. Don't jam server state into Redux.
 *
 *    Server state  → "useServerQuery"  (mini TanStack Query: cache + SWR +
 *                                        patch-not-refetch invalidation)
 *    UI state      → "useUiStore"      (mini Zustand: role, selection, modals)
 *    Event state   → "useWorkflowEvents" (custom SSE hook: live event log)
 *
 *  Below each is implemented in miniature so the boundary is VISIBLE.
 * ========================================================================= */

/* ----- 2a. SERVER STATE (mini TanStack Query) ----------------------------- */
/**
 * The cache holds server-owned data. Two superpowers an interviewer wants:
 *   1. stale-while-revalidate — show cached instantly, refresh in background.
 *   2. PATCH-NOT-REFETCH — when an SSE event carries the new state, we write
 *      it straight into the cache. No network round-trip. The event already
 *      told us the delta; refetching would throw that away.
 */
type Cache = {
  list: WorkflowListItem[];
  detail: Record<string, WorkflowInstance>;
};

type ServerCtx = {
  cache: Cache;
  /** Patch a single workflow in-place from an event payload (no refetch). */
  patchWorkflow: (id: string, patch: Partial<WorkflowInstance>) => void;
  /** Patch the list row (e.g. state/risk changed). */
  patchListItem: (id: string, patch: Partial<WorkflowListItem>) => void;
};

const ServerContext = createContext<ServerCtx | null>(null);

function useServerQuery() {
  const ctx = useContext(ServerContext);
  if (!ctx) throw new Error("ServerContext missing");
  return ctx;
}

/* ----- 2b. UI STATE (mini Zustand) ---------------------------------------- */
/**
 * Pure client concerns. Never persisted server-side, never audited.
 * The role switcher lives here — it changes what ACTIONS are offered,
 * which is the heart of role-aware affordances (Pattern 5).
 */
type UiState = {
  currentUser: string;
  role: UserRole;
  selectedId: string | null;
  modalOpen: boolean;
};

type UiStore = UiState & {
  setRole: (r: UserRole) => void;
  setUser: (u: string) => void;
  select: (id: string | null) => void;
  setModal: (open: boolean) => void;
};

const UiContext = createContext<UiStore | null>(null);
function useUiStore() {
  const ctx = useContext(UiContext);
  if (!ctx) throw new Error("UiContext missing");
  return ctx;
}

/* ----- 2c. EVENT STATE (custom SSE hook) ---------------------------------- */
/**
 * Live workflow events. In production this wraps EventSource with:
 *   - last-event-id reconnection
 *   - a StreamBuffer for partial chunks
 *   - snapshot + delta on connect
 * Here we simulate a stream with setInterval so it runs offline, but the
 * SHAPE is identical: events arrive, we append to a bounded log, and we
 * PATCH the server cache from the payload (the cross-layer wiring).
 */
function useWorkflowEvents(
  onEvent: (e: AGUIEvent) => void
): { log: AGUIEvent[]; connected: boolean } {
  const [log, setLog] = useState<AGUIEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    // Simulated "open"
    setConnected(true);
    const hello: AGUIEvent = {
      event_type: "CONNECTED",
      payload: {},
      timestamp: new Date().toISOString(),
    };
    setLog([hello]);

    // Simulated server-pushed events (a mini scripted stream).
    const script: AGUIEvent[] = SIMULATED_STREAM();
    let i = 0;
    const id = setInterval(() => {
      if (i >= script.length) return;
      const ev = script[i++];
      // BOUNDED log — keep last 50 (backpressure: drop oldest, never grow).
      setLog((prev) => [...prev.slice(-49), ev]);
      onEvent(ev); // wire into the server cache (patch-not-refetch)
    }, 2600);

    return () => clearInterval(id);
    // onEvent is stable (useCallback) so this runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { log, connected };
}

/* ===========================================================================
 *  PATTERN 3 — WORKBENCH, NOT CHAT
 * ===========================================================================
 *  The product is a TASK QUEUE sorted by risk × SLA urgency — not a
 *  scroll-back conversation. An operator sees where every workflow stands
 *  at a glance, highest-stakes first.
 * ========================================================================= */

/** Risk × urgency ordering. Higher score floats to the top. */
function queuePriority(item: WorkflowListItem): number {
  const ageMs = Date.now() - new Date(item.created_at).getTime();
  const ageHours = ageMs / 3_600_000;
  const slaUrgency = Math.min(ageHours / 24, 1); // older = more urgent
  return item.risk_score * 0.7 + slaUrgency * 0.3;
}

/* ===========================================================================
 *  COMPONENT TREE
 * ========================================================================= */

export default function HitlLearnWorkbench() {
  /* ---- instantiate the three state layers as providers ---- */

  // SERVER STATE
  const [cache, dispatch] = useReducer(cacheReducer, INITIAL_CACHE);
  const patchWorkflow = useCallback(
    (id: string, patch: Partial<WorkflowInstance>) =>
      dispatch({ kind: "PATCH_DETAIL", id, patch }),
    []
  );
  const patchListItem = useCallback(
    (id: string, patch: Partial<WorkflowListItem>) =>
      dispatch({ kind: "PATCH_LIST", id, patch }),
    []
  );
  const serverValue: ServerCtx = useMemo(
    () => ({ cache, patchWorkflow, patchListItem }),
    [cache, patchWorkflow, patchListItem]
  );

  // UI STATE
  const [ui, setUi] = useState<UiState>({
    currentUser: "alice@bank",
    role: "OPERATIONS",
    selectedId: null,
    modalOpen: false,
  });
  const uiValue: UiStore = useMemo(
    () => ({
      ...ui,
      setRole: (role) => setUi((s) => ({ ...s, role })),
      setUser: (currentUser) => setUi((s) => ({ ...s, currentUser })),
      select: (selectedId) => setUi((s) => ({ ...s, selectedId })),
      setModal: (modalOpen) => setUi((s) => ({ ...s, modalOpen })),
    }),
    [ui]
  );

  return (
    <ServerContext.Provider value={serverValue}>
      <UiContext.Provider value={uiValue}>
        <WorkbenchInner />
      </UiContext.Provider>
    </ServerContext.Provider>
  );
}

function WorkbenchInner() {
  const { cache, patchWorkflow, patchListItem } = useServerQuery();
  const ui = useUiStore();

  /* ---- EVENT LAYER → patches SERVER LAYER (patch-not-refetch) ---- */
  const onEvent = useCallback(
    (e: AGUIEvent) => {
      const id = e.workflow_id;
      if (!id) return;
      // Exhaustive-ish reduce: only the events that change cached state.
      switch (e.event_type) {
        case "WORKFLOW_STATE_CHANGED": {
          const state = e.payload.state as WorkflowState;
          patchWorkflow(id, { state });
          patchListItem(id, { state });
          break;
        }
        case "APPROVAL_GRANTED":
        case "APPROVAL_DENIED": {
          // patch the gate in place — the event carried the new gate.
          const gate = e.payload.gate as ApprovalGate | undefined;
          if (gate) patchWorkflow(id, { approval_gate: gate });
          break;
        }
        default:
          break; // other events are log-only
      }
    },
    [patchWorkflow, patchListItem]
  );

  const { log, connected } = useWorkflowEvents(onEvent);

  /* ---- WORKBENCH ORDERING (risk × SLA) ---- */
  const queue = useMemo(
    () => [...cache.list].sort((a, b) => queuePriority(b) - queuePriority(a)),
    [cache.list]
  );

  const selected = ui.selectedId ? cache.detail[ui.selectedId] : null;

  return (
    <div style={S.app}>
      <Header connected={connected} />
      <div style={S.body}>
        {/* LEFT: the task queue (workbench, not chat) */}
        <section style={S.queueCol}>
          <PaneTitle>
            Task queue · sorted by <b>risk × SLA</b>{" "}
            <Tag>PATTERN 3 · workbench not chat</Tag>
          </PaneTitle>
          {queue.map((item) => (
            <QueueRow
              key={item.id}
              item={item}
              selected={item.id === ui.selectedId}
              onClick={() => ui.select(item.id)}
            />
          ))}
        </section>

        {/* CENTER: drill-in = diff + evidence + gate */}
        <section style={S.detailCol}>
          {selected ? (
            <WorkflowDrilldown wf={selected} />
          ) : (
            <Empty>Select a workflow to see the 5-second decision view →</Empty>
          )}
        </section>

        {/* RIGHT: the live event stream (event layer made visible) */}
        <section style={S.eventCol}>
          <PaneTitle>
            Live event stream <Tag>PATTERN 2c · custom SSE hook</Tag>
          </PaneTitle>
          <EventLog log={log} />
        </section>
      </div>
    </div>
  );
}

/* ===========================================================================
 *  HEADER + ROLE SWITCHER  (PATTERN 5 · role-aware affordances)
 * ===========================================================================
 *  Switching role changes which ACTIONS the approval gate offers. Flip
 *  between roles and watch the Approve button enable/disable. This is the
 *  UI half of the backend's role gate + anti-self-approval rules.
 * ========================================================================= */

const ALL_ROLES: UserRole[] = [
  "TRADER",
  "RISK",
  "COMPLIANCE",
  "OPERATIONS",
  "LEGAL",
  "ADMIN",
];

function Header({ connected }: { connected: boolean }) {
  const ui = useUiStore();
  return (
    <header style={S.header}>
      <div>
        <strong style={{ fontSize: 16 }}>HITL Workbench</strong>{" "}
        <span style={S.subtle}>
          LLMs propose · rules decide · humans commit · ledgers remember
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span style={S.subtle}>
          acting as <b>{ui.currentUser}</b>
        </span>
        <label style={S.subtle}>
          role:{" "}
          <select
            value={ui.role}
            onChange={(e) => ui.setRole(e.target.value as UserRole)}
            style={S.select}
          >
            {ALL_ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <span style={{ ...S.dot, background: connected ? "#16a34a" : "#9ca3af" }} />
        <span style={S.subtle}>{connected ? "live" : "offline"}</span>
      </div>
    </header>
  );
}

/* ===========================================================================
 *  QUEUE ROW
 * ========================================================================= */

function QueueRow({
  item,
  selected,
  onClick,
}: {
  item: WorkflowListItem;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{ ...S.queueRow, ...(selected ? S.queueRowSel : {}) }}
    >
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={S.wfType}>{labelType(item.type)}</span>
        <RiskPill score={item.risk_score} />
      </div>
      <div style={S.wfTitle}>{item.title}</div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <StateBadge state={item.state} />
        {item.pending_approvals > 0 && (
          <span style={S.pendingTag}>{item.pending_approvals} pending</span>
        )}
      </div>
    </button>
  );
}

/* ===========================================================================
 *  WORKFLOW DRILLDOWN  — PATTERN 4 · THE 5-SECOND DECISION
 * ===========================================================================
 *  Everything a human needs to commit with confidence, in one glance:
 *    1. WHAT changed   → the proposal diff
 *    2. WHY            → the LLM reasoning (shown AS a proposal, not a fact)
 *    3. WHICH RULE     → the rules flags + risk score
 *    4. WHO signed     → the N-of-M approval gate progress
 *    5. WHAT IF WRONG  → blast radius / amount
 * ========================================================================= */

function WorkflowDrilldown({ wf }: { wf: WorkflowInstance }) {
  return (
    <div>
      <PaneTitle>
        {labelType(wf.type)} · {wf.title}{" "}
        <Tag>PATTERN 4 · the 5-second decision</Tag>
      </PaneTitle>

      <div style={S.fiveSecGrid}>
        <DecisionCell n={1} label="WHAT changed">
          <ProposalDiff before={wf.payload} after={wf.llm_proposal} />
        </DecisionCell>

        <DecisionCell n={2} label="WHY (proposal — not a decision)">
          {/* PATTERN 6 · confidence surfaced visibly. The LLM output is
              explicitly framed as a PROPOSAL the human evaluates. */}
          <div style={S.proposalBox}>
            <span style={S.proposalChip}>AI PROPOSAL · advisory only</span>
            <p style={{ margin: "8px 0 0" }}>{wf.llm_reasoning}</p>
          </div>
        </DecisionCell>

        <DecisionCell n={3} label="WHICH RULE flagged it">
          <RiskPill score={wf.risk_score} big />
          <div style={S.subtle}>
            deterministic rules engine · risk {wf.risk_score.toFixed(2)}
          </div>
        </DecisionCell>

        <DecisionCell n={5} label="WHAT IF wrong (blast radius)">
          <BlastRadius wf={wf} />
        </DecisionCell>
      </div>

      <DecisionCell n={4} label="WHO signs — N-of-M approval gate">
        {wf.approval_gate ? (
          <ApprovalGatePanel wf={wf} gate={wf.approval_gate} />
        ) : (
          <Empty>No approval gate on this workflow.</Empty>
        )}
      </DecisionCell>
    </div>
  );
}

/* ===========================================================================
 *  APPROVAL GATE PANEL
 * ===========================================================================
 *  PATTERN 7 · approval as WORKFLOW STATE, not a button flag.
 *  PATTERN 5 · role-aware affordances + anti-self-approval enforced in UI.
 *
 *  canAct() is the UI mirror of the backend's five enforcement rules:
 *    - workflow must be AWAITING_APPROVAL
 *    - gate must be PENDING
 *    - current role must be in required_roles
 *    - current user must not have already voted
 *    - current user must not be the creator (ANTI-SELF-APPROVAL)
 * ========================================================================= */

function ApprovalGatePanel({
  wf,
  gate,
}: {
  wf: WorkflowInstance;
  gate: ApprovalGate;
}) {
  const ui = useUiStore();
  const { patchWorkflow } = useServerQuery();

  const approveCount = gate.approvals.filter((a) => a.decision === "approve").length;
  const alreadyVoted = gate.approvals.some((a) => a.actor === ui.currentUser);
  const isSelfApproval = wf.created_by === ui.currentUser;
  const roleAllowed = gate.required_roles.includes(ui.role);
  const stateOk = wf.state === "AWAITING_APPROVAL";
  const gatePending = gate.status === "PENDING";

  const canAct =
    stateOk && gatePending && roleAllowed && !alreadyVoted && !isSelfApproval;

  // The single reason the button is disabled — shown to the operator so the
  // UI never silently greys out an action (honest affordances).
  const blockedReason = !stateOk
    ? "workflow is not awaiting approval"
    : !gatePending
    ? "gate already resolved"
    : !roleAllowed
    ? `your role (${ui.role}) is not a required signer`
    : isSelfApproval
    ? "you created this workflow — anti-self-approval"
    : alreadyVoted
    ? "you have already voted on this gate"
    : null;

  /* OPTIMISTIC UPDATE + ROLLBACK (mini-mutation).
     We patch the cache immediately, then "confirm" — if the server rejected,
     we'd roll back to the snapshot. Here it always succeeds, but the SHAPE
     is the interview point. */
  const vote = (decision: "approve" | "deny") => {
    const snapshot = gate; // for rollback
    const optimistic: ApprovalGate = {
      ...gate,
      approvals: [
        ...gate.approvals,
        {
          actor: ui.currentUser,
          role: ui.role,
          decision,
          comment: "",
          timestamp: new Date().toISOString(),
        },
      ],
    };
    const newApprove = optimistic.approvals.filter(
      (a) => a.decision === "approve"
    ).length;
    const resolved =
      decision === "deny"
        ? "DENIED"
        : newApprove >= gate.quorum
        ? "APPROVED"
        : "PENDING";
    optimistic.status = resolved as ApprovalGate["status"];

    patchWorkflow(wf.id, {
      approval_gate: optimistic,
      state:
        resolved === "APPROVED"
          ? "RESOLVED"
          : resolved === "DENIED"
          ? "REJECTED"
          : wf.state,
    });

    // (In real code: await api.approve(); on error → patchWorkflow(snapshot))
    void snapshot;
  };

  return (
    <div>
      {/* N-of-M progress — approval as a quantified state */}
      <div style={S.quorumRow}>
        <ProgressBar value={approveCount} max={gate.quorum} />
        <span style={S.subtle}>
          {approveCount} of {gate.quorum} required ·{" "}
          <StateBadge state={statusToState(gate.status)} />
        </span>
      </div>

      <div style={S.signerRow}>
        <span style={S.subtle}>required roles: </span>
        {gate.required_roles.map((r) => (
          <span
            key={r}
            style={{
              ...S.roleChip,
              ...(r === ui.role ? S.roleChipActive : {}),
            }}
          >
            {r}
          </span>
        ))}
      </div>

      {/* votes cast so far */}
      {gate.approvals.length > 0 && (
        <ul style={S.voteList}>
          {gate.approvals.map((a, i) => (
            <li key={i} style={S.subtle}>
              {a.decision === "approve" ? "🟢" : "🔴"} <b>{a.actor}</b> ({a.role})
            </li>
          ))}
        </ul>
      )}

      {/* role-aware action buttons */}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          disabled={!canAct}
          onClick={() => vote("approve")}
          style={{ ...S.btn, ...S.btnApprove, ...(canAct ? {} : S.btnDisabled) }}
        >
          Approve
        </button>
        <button
          disabled={!canAct}
          onClick={() => vote("deny")}
          style={{ ...S.btn, ...S.btnDeny, ...(canAct ? {} : S.btnDisabled) }}
        >
          Deny
        </button>
      </div>

      {blockedReason && (
        <div style={S.blockedNote}>🔒 Action disabled — {blockedReason}.</div>
      )}
    </div>
  );
}

/* ===========================================================================
 *  SMALL PRESENTATIONAL PIECES
 * ========================================================================= */

function ProposalDiff({
  before,
  after,
}: {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}) {
  const keys = Array.from(
    new Set([...Object.keys(before), ...Object.keys(after)])
  );
  return (
    <table style={S.diffTable}>
      <tbody>
        {keys.map((k) => {
          const b = before[k];
          const a = after[k];
          const changed = JSON.stringify(b) !== JSON.stringify(a) && a !== undefined;
          return (
            <tr key={k}>
              <td style={S.diffKey}>{k}</td>
              <td style={S.diffOld}>{fmt(b)}</td>
              <td style={S.diffArrow}>{changed ? "→" : ""}</td>
              <td style={{ ...S.diffNew, ...(changed ? S.diffNewHot : {}) }}>
                {a !== undefined ? fmt(a) : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function BlastRadius({ wf }: { wf: WorkflowInstance }) {
  const amount = (wf.payload.amount as number) ?? (wf.llm_proposal.amount as number);
  const ccy = (wf.payload.currency as string) ?? "";
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700 }}>
        {amount ? `${ccy} ${amount.toLocaleString()}` : "—"}
      </div>
      <div style={S.subtle}>
        irreversible once committed · saga-compensated on partial failure
      </div>
    </div>
  );
}

function EventLog({ log }: { log: AGUIEvent[] }) {
  return (
    <div style={S.eventLog}>
      {log
        .slice()
        .reverse()
        .map((e, i) => (
          <div key={i} style={S.eventRow}>
            <span style={S.eventTime}>
              {e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ""}
            </span>
            <span>{describeEvent(e)}</span>
            {e.workflow_id && (
              <span style={S.eventWf}>#{e.workflow_id.slice(0, 6)}</span>
            )}
          </div>
        ))}
    </div>
  );
}

function RiskPill({ score, big }: { score: number; big?: boolean }) {
  const { bg, fg, label } =
    score >= 0.66
      ? { bg: "#fee2e2", fg: "#b91c1c", label: "HIGH" }
      : score >= 0.33
      ? { bg: "#fef3c7", fg: "#b45309", label: "MED" }
      : { bg: "#dcfce7", fg: "#15803d", label: "LOW" };
  return (
    <span
      style={{
        background: bg,
        color: fg,
        padding: big ? "4px 12px" : "1px 8px",
        borderRadius: 999,
        fontSize: big ? 14 : 11,
        fontWeight: 700,
      }}
    >
      {label} {score.toFixed(2)}
    </span>
  );
}

function StateBadge({ state }: { state: WorkflowState }) {
  const color: Record<WorkflowState, string> = {
    OPEN: "#6b7280",
    AWAITING_APPROVAL: "#b45309",
    DISPUTED: "#be123c",
    ESCALATED: "#9333ea",
    RESOLVED: "#15803d",
    REJECTED: "#b91c1c",
  };
  return (
    <span style={{ ...S.stateBadge, color: color[state], borderColor: color[state] }}>
      {state}
    </span>
  );
}

function ProgressBar({ value, max }: { value: number; max: number }) {
  const pct = Math.min((value / max) * 100, 100);
  return (
    <div style={S.progressOuter}>
      <div style={{ ...S.progressInner, width: `${pct}%` }} />
    </div>
  );
}

/* layout helpers */
const PaneTitle: React.FC = ({ children }) => <div style={S.paneTitle}>{children}</div>;
const Tag: React.FC = ({ children }) => <span style={S.patternTag}>{children}</span>;
const Empty: React.FC = ({ children }) => <div style={S.empty}>{children}</div>;
function DecisionCell({
  n,
  label,
  children,
}: {
  n: number;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={S.decisionCell}>
      <div style={S.decisionLabel}>
        <span style={S.decisionNum}>{n}</span> {label}
      </div>
      {children}
    </div>
  );
}

/* ===========================================================================
 *  PURE HELPERS
 * ========================================================================= */

function labelType(t: WorkflowType): string {
  return t.replace("_", " ");
}
function statusToState(s: ApprovalGate["status"]): WorkflowState {
  return s === "APPROVED"
    ? "RESOLVED"
    : s === "DENIED"
    ? "REJECTED"
    : "AWAITING_APPROVAL";
}
function fmt(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "number") return v.toLocaleString();
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/* ===========================================================================
 *  CACHE REDUCER (mini TanStack Query internals)
 * ========================================================================= */
type CacheAction =
  | { kind: "PATCH_DETAIL"; id: string; patch: Partial<WorkflowInstance> }
  | { kind: "PATCH_LIST"; id: string; patch: Partial<WorkflowListItem> };

function cacheReducer(state: Cache, action: CacheAction): Cache {
  switch (action.kind) {
    case "PATCH_DETAIL":
      return {
        ...state,
        detail: {
          ...state.detail,
          [action.id]: { ...state.detail[action.id], ...action.patch },
        },
      };
    case "PATCH_LIST":
      return {
        ...state,
        list: state.list.map((it) =>
          it.id === action.id ? { ...it, ...action.patch } : it
        ),
      };
    default:
      return state;
  }
}

/* ===========================================================================
 *  SEED DATA + SIMULATED STREAM (so the file runs offline)
 * ========================================================================= */

const NOW = Date.now();
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();

const W1: WorkflowInstance = {
  id: "wf-001-settle",
  type: "SETTLEMENT",
  state: "AWAITING_APPROVAL",
  title: "EUR/USD settlement — Acme Capital",
  description: "Large settlement, new counterparty",
  payload: { amount: 6_200_000, currency: "USD", counterparty: "Acme Capital", settlement_date: "T+2", lei: null },
  llm_proposal: { amount: 6_200_000, currency: "USD", counterparty: "Acme Capital", settlement_date: "T+2", lei: "REQUIRE_VERIFY" },
  created_at: iso(3 * 3600_000),
  updated_at: iso(60_000),
  created_by: "bob@bank",
  risk_score: 0.73,
  llm_reasoning:
    "High risk: new counterparty with no verified LEI on a $6.2M settlement. Last 3 trades settled clean. Recommend COMPLIANCE sign-off and LEI verification before commit.",
  approval_gate: {
    id: "gate-1",
    workflow_id: "wf-001-settle",
    required_roles: ["COMPLIANCE", "RISK"],
    quorum: 2,
    approvals: [],
    status: "PENDING",
    created_at: iso(60_000),
  },
};

const W2: WorkflowInstance = {
  id: "wf-002-margin",
  type: "MARGIN_CALL",
  state: "AWAITING_APPROVAL",
  title: "Margin call dispute — Northwind LP",
  description: "Counterparty disputes variation margin",
  payload: { amount: 1_450_000, currency: "EUR", counterparty: "Northwind LP" },
  llm_proposal: { amount: 1_450_000, currency: "EUR", counterparty: "Northwind LP", recommendation: "partial_release" },
  created_at: iso(8 * 3600_000),
  updated_at: iso(120_000),
  created_by: "alice@bank",
  risk_score: 0.41,
  llm_reasoning:
    "Dispute centres on CSA valuation timing. Recommend partial release pending reconciliation. Note: you created this workflow — you cannot self-approve.",
  approval_gate: {
    id: "gate-2",
    workflow_id: "wf-002-margin",
    required_roles: ["RISK", "LEGAL"],
    quorum: 2,
    approvals: [{ actor: "carol@bank", role: "RISK", decision: "approve", comment: "", timestamp: iso(30_000) }],
    status: "PENDING",
    created_at: iso(120_000),
  },
};

const W3: WorkflowInstance = {
  id: "wf-003-recon",
  type: "RECONCILIATION",
  state: "OPEN",
  title: "Recon break — custody vs ledger",
  description: "12-unit position mismatch",
  payload: { instrument: "US0378331005", break_qty: 12 },
  llm_proposal: { instrument: "US0378331005", break_qty: 12, resolution: "adjust_ledger" },
  created_at: iso(1 * 3600_000),
  updated_at: iso(900_000),
  created_by: "dave@bank",
  risk_score: 0.18,
  llm_reasoning: "Small quantity break, likely a corporate-action timing difference. Low risk.",
  approval_gate: null,
};

const INITIAL_CACHE: Cache = {
  detail: { [W1.id]: W1, [W2.id]: W2, [W3.id]: W3 },
  list: [W1, W2, W3].map((w) => ({
    id: w.id,
    type: w.type,
    state: w.state,
    title: w.title,
    risk_score: w.risk_score,
    created_at: w.created_at,
    updated_at: w.updated_at,
    pending_approvals: w.approval_gate ? w.approval_gate.quorum - w.approval_gate.approvals.length : 0,
  })),
};

/** A scripted live stream so the right-hand log animates on load. */
function SIMULATED_STREAM(): AGUIEvent[] {
  const t = () => new Date().toISOString();
  return [
    { event_type: "RUN_STARTED", workflow_id: "wf-001-settle", payload: {}, timestamp: t() },
    { event_type: "TOOL_CALL_START", workflow_id: "wf-001-settle", payload: { tool: "rules.validate_settlement" }, timestamp: t() },
    { event_type: "TOOL_CALL_END", workflow_id: "wf-001-settle", payload: { risk: 0.73 }, timestamp: t() },
    { event_type: "TEXT_MESSAGE_CONTENT", workflow_id: "wf-001-settle", payload: {}, timestamp: t() },
    { event_type: "APPROVAL_REQUEST", workflow_id: "wf-001-settle", payload: {}, timestamp: t() },
    { event_type: "EVIDENCE_PINNED", workflow_id: "wf-002-margin", payload: {}, timestamp: t() },
    { event_type: "AUDIT_EVENT", workflow_id: "wf-001-settle", payload: {}, timestamp: t() },
  ];
}

/* ===========================================================================
 *  STYLES (inline so the file is fully self-contained)
 * ========================================================================= */
const S: Record<string, React.CSSProperties> = {
  app: { fontFamily: "ui-sans-serif, system-ui, sans-serif", color: "#111827", height: "100vh", display: "flex", flexDirection: "column", background: "#f9fafb" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 20px", borderBottom: "1px solid #e5e7eb", background: "#fff" },
  subtle: { color: "#6b7280", fontSize: 13 },
  select: { fontSize: 13, padding: "2px 6px" },
  dot: { width: 9, height: 9, borderRadius: 999, display: "inline-block" },
  body: { display: "grid", gridTemplateColumns: "320px 1fr 280px", gap: 0, flex: 1, minHeight: 0 },
  queueCol: { borderRight: "1px solid #e5e7eb", padding: 12, overflowY: "auto", background: "#fff" },
  detailCol: { padding: 20, overflowY: "auto" },
  eventCol: { borderLeft: "1px solid #e5e7eb", padding: 12, overflowY: "auto", background: "#fff" },
  paneTitle: { fontSize: 13, fontWeight: 600, marginBottom: 12, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" },
  patternTag: { fontSize: 10, fontWeight: 700, color: "#4338ca", background: "#eef2ff", padding: "2px 6px", borderRadius: 4, letterSpacing: 0.3 },
  queueRow: { display: "block", width: "100%", textAlign: "left", border: "1px solid #e5e7eb", borderRadius: 8, padding: 10, marginBottom: 8, background: "#fff", cursor: "pointer" },
  queueRowSel: { borderColor: "#4338ca", boxShadow: "0 0 0 2px #eef2ff" },
  wfType: { fontSize: 10, fontWeight: 700, color: "#6b7280", letterSpacing: 0.5 },
  wfTitle: { fontSize: 13, fontWeight: 600, margin: "4px 0 6px" },
  pendingTag: { fontSize: 10, color: "#b45309", background: "#fef3c7", padding: "1px 6px", borderRadius: 999 },
  stateBadge: { fontSize: 10, fontWeight: 700, border: "1px solid", borderRadius: 4, padding: "1px 6px" },
  fiveSecGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 },
  decisionCell: { border: "1px solid #e5e7eb", borderRadius: 8, padding: 12, background: "#fff", marginBottom: 12 },
  decisionLabel: { fontSize: 11, fontWeight: 700, color: "#374151", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 },
  decisionNum: { background: "#4338ca", color: "#fff", width: 18, height: 18, borderRadius: 999, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 11 },
  proposalBox: { border: "1px dashed #c7d2fe", background: "#f5f3ff", borderRadius: 8, padding: 10 },
  proposalChip: { fontSize: 10, fontWeight: 700, color: "#6d28d9", background: "#ede9fe", padding: "2px 6px", borderRadius: 4 },
  diffTable: { width: "100%", borderCollapse: "collapse", fontSize: 12 },
  diffKey: { color: "#6b7280", padding: "2px 6px", whiteSpace: "nowrap" },
  diffOld: { color: "#9ca3af", padding: "2px 6px", textDecoration: "line-through" },
  diffArrow: { color: "#4338ca", padding: "2px 4px" },
  diffNew: { padding: "2px 6px" },
  diffNewHot: { fontWeight: 700, color: "#15803d" },
  quorumRow: { display: "flex", alignItems: "center", gap: 12, marginBottom: 10 },
  progressOuter: { flex: 1, height: 10, background: "#e5e7eb", borderRadius: 999, overflow: "hidden", maxWidth: 180 },
  progressInner: { height: "100%", background: "#16a34a", transition: "width .3s" },
  signerRow: { marginBottom: 8 },
  roleChip: { fontSize: 10, fontWeight: 600, border: "1px solid #d1d5db", borderRadius: 999, padding: "1px 8px", marginRight: 4, color: "#6b7280" },
  roleChipActive: { borderColor: "#4338ca", color: "#4338ca", background: "#eef2ff" },
  voteList: { margin: "8px 0", paddingLeft: 18 },
  btn: { padding: "8px 18px", borderRadius: 8, border: "none", fontWeight: 700, cursor: "pointer", fontSize: 13 },
  btnApprove: { background: "#16a34a", color: "#fff" },
  btnDeny: { background: "#dc2626", color: "#fff" },
  btnDisabled: { opacity: 0.4, cursor: "not-allowed" },
  blockedNote: { marginTop: 10, fontSize: 12, color: "#92400e", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 6, padding: "6px 10px" },
  eventLog: { display: "flex", flexDirection: "column", gap: 6 },
  eventRow: { fontSize: 11, display: "flex", gap: 6, alignItems: "center", borderBottom: "1px solid #f3f4f6", paddingBottom: 4 },
  eventTime: { color: "#9ca3af", fontVariantNumeric: "tabular-nums" },
  eventWf: { color: "#4338ca", fontFamily: "monospace" },
  empty: { color: "#9ca3af", fontSize: 13, padding: 20, textAlign: "center" },
};
