export type TraceItem =
  | { kind: "handoff"; from: string; to: string; id: string }
  | { kind: "message"; messageId: string; agent: string; text: string }
  | { kind: "tool"; toolCallId: string; agent: string; name: string; args?: unknown; result?: unknown };

interface Props {
  items: TraceItem[];
}

export function Trace({ items }: Props) {
  return (
    <div className="trace">
      {items.map((item) => {
        if (item.kind === "handoff") {
          return (
            <div key={item.id} className="trace-row trace-handoff">
              <span className="trace-tag">handoff</span>
              <span>{item.from} → <strong>{item.to}</strong></span>
            </div>
          );
        }
        if (item.kind === "message") {
          return (
            <div key={item.messageId} className="trace-row trace-message">
              <span className="trace-tag" data-agent={item.agent}>{item.agent}</span>
              <span className="trace-text">{item.text || <em>thinking…</em>}</span>
            </div>
          );
        }
        return (
          <div key={item.toolCallId} className="trace-row trace-tool">
            <span className="trace-tag tool">tool</span>
            <div className="trace-tool-body">
              <div><strong>{item.name}</strong> <span className="muted">({item.agent})</span></div>
              {item.args !== undefined && (
                <pre className="trace-args">{JSON.stringify(item.args, null, 2)}</pre>
              )}
              {item.result !== undefined && (
                <pre className="trace-result">→ {String(item.result).slice(0, 400)}</pre>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
