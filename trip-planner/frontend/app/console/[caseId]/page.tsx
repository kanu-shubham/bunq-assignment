"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useParams } from "next/navigation";
import { CopilotKit, useCoAgent, useCopilotAction } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import { KIND_LABELS, updateCase, useCase } from "../../../lib/cases";

type Stop = { city: string; days: number };
type Itinerary = { stops: Stop[] };

function ItineraryPanel({ caseId }: { caseId: string }) {
  // (Feature 4) Shared state — synced via STATE_SNAPSHOT/DELTA events.
  const { state, setState } = useCoAgent<Itinerary>({
    name: "tripPlanner",
    initialState: { stops: [] },
  });

  // (Feature 4) Frontend action: agent mutates shared state.
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
      updateCase(caseId, {
        status: "running",
        currentStep: `Itinerary updated (${stops.length} stops)`,
      });
      return `Itinerary now has ${stops.length} stop(s).`;
    },
  });

  // (Feature 3) HITL — pauses agent run; flips case to "blocked" while open.
  useCopilotAction({
    name: "confirmBooking",
    description: "Ask the user to approve booking the current itinerary.",
    parameters: [{ name: "summary", type: "string", required: true }],
    renderAndWaitForResponse: ({ args, respond, status }) => (
      <ConfirmBookingCard
        caseId={caseId}
        args={args as { summary: string }}
        respond={respond}
        status={status as string}
      />
    ),
  });

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>Proposed itinerary</h3>
      {(state?.stops?.length ?? 0) === 0 ? (
        <p style={{ color: "#777" }}>Empty. Ask the agent to add stops.</p>
      ) : (
        <ol style={{ paddingLeft: 18 }}>
          {state!.stops.map((s, i) => (
            <li key={i} style={{ margin: "4px 0" }}>
              {s.city} — {s.days} day{s.days === 1 ? "" : "s"}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ConfirmBookingCard({
  caseId,
  args,
  respond,
  status,
}: {
  caseId: string;
  args: { summary: string };
  respond?: (value: string) => void;
  status: string;
}) {
  // Mirror the HITL pause into the case store so the console shows it as blocked.
  useEffect(() => {
    if (status === "executing" || status === "inProgress") {
      updateCase(caseId, {
        status: "blocked",
        currentStep: "Awaiting operator approval on booking",
        pendingApprovals: 1,
        blockedSince: Date.now(),
      });
    } else if (status === "complete") {
      updateCase(caseId, {
        status: "running",
        currentStep: "Booking decision received",
        pendingApprovals: 0,
        blockedSince: null,
      });
    }
  }, [status, caseId]);

  if (status === "complete") {
    return <div style={{ padding: 8, color: "#444" }}>Booking decision recorded.</div>;
  }

  return (
    <div style={{ padding: 12, border: "1px solid #ddd", borderRadius: 8, background: "#fffbe6" }}>
      <p style={{ margin: "0 0 4px", fontWeight: 600 }}>Approval required</p>
      <p style={{ margin: "0 0 12px", color: "#555" }}>{args.summary}</p>
      <button onClick={() => respond?.("approved")} style={btn("#16a34a")}>Approve</button>
      <button onClick={() => respond?.("rejected")} style={btn("#dc2626")}>Reject</button>
    </div>
  );
}

function btn(bg: string): React.CSSProperties {
  return {
    background: bg,
    color: "white",
    border: 0,
    padding: "6px 12px",
    marginRight: 8,
    borderRadius: 6,
    cursor: "pointer",
  };
}

function CaseHeader({ caseId }: { caseId: string }) {
  const c = useCase(caseId);
  if (!c) return null;
  return (
    <header
      style={{
        padding: "14px 20px",
        borderBottom: "1px solid #eee",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "white",
      }}
    >
      <div>
        <Link href="/console" style={{ color: "#666", fontSize: 13, textDecoration: "none" }}>
          ← Console
        </Link>
        <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginTop: 2 }}>
          <strong>{c.id}</strong>
          <span style={{ color: "#888", fontSize: 13 }}>{KIND_LABELS[c.kind]}</span>
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

  return (
    // threadId scopes the conversation to this case — independent memory per case.
    <CopilotKit runtimeUrl="/api/copilotkit" agent="tripPlanner" threadId={caseId}>
      <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
        <CaseHeader caseId={caseId} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", flex: 1, minHeight: 0 }}>
          <div style={{ padding: 20, overflowY: "auto" }}>
            <ItineraryPanel caseId={caseId} />
          </div>
          <div style={{ borderLeft: "1px solid #eee", minHeight: 0 }}>
            <CopilotChat
              instructions="Help the operator resolve this case."
              labels={{ title: "Agent", initial: "What would you like me to do for this case?" }}
            />
          </div>
        </div>
      </div>
    </CopilotKit>
  );
}
