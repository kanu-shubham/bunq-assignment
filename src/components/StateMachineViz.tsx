import React from "react";
import type { WorkflowState } from "../types/domain";

const STATES: WorkflowState[] = [
  "OPEN",
  "AWAITING_APPROVAL",
  "DISPUTED",
  "ESCALATED",
  "RESOLVED",
  "REJECTED",
];

const STATE_X: Record<WorkflowState, number> = {
  OPEN: 60,
  AWAITING_APPROVAL: 200,
  DISPUTED: 340,
  ESCALATED: 340,
  RESOLVED: 480,
  REJECTED: 480,
};

const STATE_Y: Record<WorkflowState, number> = {
  OPEN: 60,
  AWAITING_APPROVAL: 60,
  DISPUTED: 120,
  ESCALATED: 60, // not used in layout below, using manual layout
  RESOLVED: 60,
  REJECTED: 120,
};

const STATE_COLORS: Record<WorkflowState, string> = {
  OPEN: "#6b7280",
  AWAITING_APPROVAL: "#d97706",
  DISPUTED: "#dc2626",
  ESCALATED: "#7c3aed",
  RESOLVED: "#16a34a",
  REJECTED: "#b91c1c",
};

const EDGES: [WorkflowState, WorkflowState][] = [
  ["OPEN", "AWAITING_APPROVAL"],
  ["AWAITING_APPROVAL", "RESOLVED"],
  ["AWAITING_APPROVAL", "REJECTED"],
  ["AWAITING_APPROVAL", "DISPUTED"],
  ["AWAITING_APPROVAL", "ESCALATED"],
  ["DISPUTED", "AWAITING_APPROVAL"],
  ["DISPUTED", "ESCALATED"],
  ["ESCALATED", "RESOLVED"],
  ["ESCALATED", "REJECTED"],
];

// Horizontal linear layout
const POS: Record<WorkflowState, { x: number; y: number }> = {
  OPEN: { x: 50, y: 55 },
  AWAITING_APPROVAL: { x: 170, y: 55 },
  DISPUTED: { x: 310, y: 95 },
  ESCALATED: { x: 310, y: 15 },
  RESOLVED: { x: 450, y: 15 },
  REJECTED: { x: 450, y: 95 },
};

interface Props {
  currentState: WorkflowState;
}

export function StateMachineViz({ currentState }: Props) {
  return (
    <div style={{ background: "#f9fafb", borderRadius: 8, padding: 8, marginBottom: 16 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#6b7280", marginBottom: 4 }}>
        WORKFLOW STATE MACHINE
      </div>
      <svg width="100%" viewBox="0 0 550 120" style={{ display: "block" }}>
        <defs>
          <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L0,6 L6,3 z" fill="#9ca3af" />
          </marker>
        </defs>

        {/* Edges */}
        {EDGES.map(([from, to], i) => {
          const f = POS[from];
          const t = POS[to];
          return (
            <line
              key={i}
              x1={f.x + 30}
              y1={f.y + 12}
              x2={t.x - 4}
              y2={t.y + 12}
              stroke="#d1d5db"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
          );
        })}

        {/* Nodes */}
        {STATES.map((state) => {
          const { x, y } = POS[state];
          const active = state === currentState;
          return (
            <g key={state}>
              <rect
                x={x}
                y={y}
                width={60}
                height={24}
                rx={6}
                fill={active ? STATE_COLORS[state] : "#e5e7eb"}
                stroke={active ? STATE_COLORS[state] : "#d1d5db"}
                strokeWidth={active ? 2 : 1}
              />
              <text
                x={x + 30}
                y={y + 15}
                textAnchor="middle"
                fontSize={7}
                fontWeight={active ? "bold" : "normal"}
                fill={active ? "white" : "#6b7280"}
              >
                {state.replace("_", " ")}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
