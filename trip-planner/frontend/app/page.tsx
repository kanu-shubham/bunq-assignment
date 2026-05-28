"use client";

import { CopilotKit, useCoAgent, useCopilotAction } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";

type Stop = { city: string; days: number };
type Itinerary = { stops: Stop[] };

function ItineraryPanel() {
  // (Feature 4) Shared state: kept in sync with the agent over AG-UI.
  const { state, setState } = useCoAgent<Itinerary>({
    name: "tripPlanner",
    initialState: { stops: [] },
  });

  // (Feature 4) Agent-callable frontend action that mutates the shared state.
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
      return `Itinerary now has ${stops.length} stop(s).`;
    },
  });

  // (Feature 3) Human-in-the-loop: the agent calls this, UI pauses on Approve/Reject.
  useCopilotAction({
    name: "confirmBooking",
    description: "Ask the user to approve booking the current itinerary.",
    parameters: [{ name: "summary", type: "string", required: true }],
    renderAndWaitForResponse: ({ args, respond, status }) => {
      if (status === "complete") return <div>Booking decision received.</div>;
      return (
        <div style={{ padding: 12, border: "1px solid #ddd", borderRadius: 8 }}>
          <p style={{ margin: "0 0 8px" }}><b>Confirm booking</b></p>
          <p style={{ margin: "0 0 12px" }}>{args.summary}</p>
          <button onClick={() => respond?.("approved")} style={btn("#16a34a")}>Approve</button>
          <button onClick={() => respond?.("rejected")} style={btn("#dc2626")}>Reject</button>
        </div>
      );
    },
  });

  return (
    <div style={{ padding: 16 }}>
      <h2 style={{ marginTop: 0 }}>Itinerary</h2>
      {(state?.stops?.length ?? 0) === 0 ? (
        <p style={{ color: "#666" }}>No stops yet. Ask the agent to add some.</p>
      ) : (
        <ol>
          {state!.stops.map((s, i) => (
            <li key={i}>{s.city} — {s.days} day{s.days === 1 ? "" : "s"}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

function btn(bg: string): React.CSSProperties {
  return { background: bg, color: "white", border: 0, padding: "6px 12px", marginRight: 8, borderRadius: 6, cursor: "pointer" };
}

export default function Page() {
  return (
    <CopilotKit runtimeUrl="/api/copilotkit" agent="tripPlanner">
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", height: "100vh" }}>
        <ItineraryPanel />
        <div style={{ borderLeft: "1px solid #eee", height: "100vh" }}>
          {/* (Feature 1) Streaming chat. (Feature 2) Tool calls render inline. */}
          <CopilotChat
            instructions="Help me plan a trip."
            labels={{ title: "Trip Planner", initial: "Where do you want to go?" }}
          />
        </div>
      </div>
    </CopilotKit>
  );
}
