import React from "react";

interface Props {
  title: string;
  current: Record<string, unknown>;
  proposed: Record<string, unknown>;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
}

export function DiffViewer({ title, current, proposed }: Props) {
  const allKeys = Array.from(
    new Set([...Object.keys(current), ...Object.keys(proposed)])
  );

  return (
    <div style={styles.container}>
      <div style={styles.header}>{title}</div>
      <div style={styles.columns}>
        <div style={styles.colHeader}>Current State</div>
        <div style={styles.colHeader}>Proposed Change</div>
      </div>
      {allKeys.map((key) => {
        const c = current[key];
        const p = proposed[key];
        const changed = JSON.stringify(c) !== JSON.stringify(p);
        return (
          <div key={key} style={{ ...styles.row, background: changed ? "#fffbeb" : "white" }}>
            <div style={styles.key}>{key}</div>
            <div style={{ ...styles.value, color: changed ? "#92400e" : "#374151" }}>
              {formatValue(c)}
            </div>
            <div style={{ ...styles.value, color: changed ? "#166534" : "#374151", background: changed ? "#f0fdf4" : "transparent" }}>
              {formatValue(p)}
              {changed && <span style={styles.changedTag}>CHANGED</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    border: "1px solid #e5e7eb",
    borderRadius: 8,
    overflow: "hidden",
    marginBottom: 16,
    fontSize: 12,
  },
  header: {
    padding: "8px 12px",
    background: "#f3f4f6",
    fontWeight: 700,
    borderBottom: "1px solid #e5e7eb",
    fontSize: 13,
  },
  columns: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    background: "#f9fafb",
    borderBottom: "1px solid #e5e7eb",
    marginLeft: 120,
  },
  colHeader: {
    padding: "4px 8px",
    fontWeight: 600,
    color: "#6b7280",
    fontSize: 11,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  row: {
    display: "grid",
    gridTemplateColumns: "120px 1fr 1fr",
    borderBottom: "1px solid #f3f4f6",
  },
  key: {
    padding: "6px 8px",
    fontWeight: 600,
    color: "#374151",
    background: "#f9fafb",
    borderRight: "1px solid #e5e7eb",
    wordBreak: "break-all" as const,
  },
  value: {
    padding: "6px 8px",
    fontFamily: "monospace",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-all" as const,
    borderRight: "1px solid #f3f4f6",
  },
  changedTag: {
    display: "inline-block",
    marginLeft: 6,
    background: "#16a34a",
    color: "white",
    fontSize: 9,
    padding: "1px 4px",
    borderRadius: 4,
    fontWeight: 700,
  },
};
