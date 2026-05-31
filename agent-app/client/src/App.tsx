import { useCallback, useRef, useState } from "react";
import { AgUiEvent, streamAgUi } from "./lib/agui.js";
import { ApprovalModal } from "./components/ApprovalModal.js";
import { Trace, TraceItem } from "./components/Trace.js";

interface ChatTurn {
  role: "user" | "system";
  text: string;
}

interface PendingInterrupt {
  interruptId: string;
  reason: string;
  payload: Record<string, unknown>;
}

export function App() {
  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [trace, setTrace] = useState<TraceItem[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState<PendingInterrupt | null>(null);
  const threadId = useRef<string | null>(null);

  /** Apply one AG-UI event to local state. */
  const apply = useCallback((event: AgUiEvent) => {
    switch (event.type) {
      case "RUN_STARTED":
        threadId.current = event.threadId;
        break;
      case "AGENT_HANDOFF":
        setTrace((t) => [...t, { kind: "handoff", from: event.from, to: event.to, id: crypto.randomUUID() }]);
        break;
      case "TEXT_MESSAGE_START":
        setTrace((t) => [...t, { kind: "message", messageId: event.messageId, agent: event.agent, text: "" }]);
        break;
      case "TEXT_MESSAGE_CONTENT":
        setTrace((t) =>
          t.map((item) =>
            item.kind === "message" && item.messageId === event.messageId
              ? { ...item, text: item.text + event.delta }
              : item,
          ),
        );
        break;
      case "TOOL_CALL_START":
        setTrace((t) => [...t, { kind: "tool", toolCallId: event.toolCallId, agent: event.agent, name: event.name }]);
        break;
      case "TOOL_CALL_ARGS":
        setTrace((t) =>
          t.map((item) =>
            item.kind === "tool" && item.toolCallId === event.toolCallId ? { ...item, args: event.args } : item,
          ),
        );
        break;
      case "TOOL_CALL_RESULT":
        setTrace((t) =>
          t.map((item) =>
            item.kind === "tool" && item.toolCallId === event.toolCallId ? { ...item, result: event.result } : item,
          ),
        );
        break;
      case "HITL_INTERRUPT":
        setPending({
          interruptId: event.interruptId,
          reason: event.reason,
          payload: event.payload as Record<string, unknown>,
        });
        break;
      case "RUN_FINISHED":
        if (event.reason === "error") {
          setChat((c) => [...c, { role: "system", text: `error: ${event.error ?? "unknown"}` }]);
        }
        setRunning(false);
        break;
    }
  }, []);

  const send = async () => {
    if (!input.trim() || running) return;
    const message = input.trim();
    setInput("");
    setChat((c) => [...c, { role: "user", text: message }]);
    setRunning(true);
    try {
      await streamAgUi("/runs", { message, threadId: threadId.current }, apply);
    } catch (err) {
      setChat((c) => [...c, { role: "system", text: `stream error: ${(err as Error).message}` }]);
      setRunning(false);
    }
  };

  const decide = async (approved: boolean, note?: string) => {
    if (!pending || !threadId.current) return;
    setPending(null);
    setRunning(true);
    try {
      await streamAgUi(`/runs/${threadId.current}/resume`, { approved, note }, apply);
    } catch (err) {
      setChat((c) => [...c, { role: "system", text: `resume error: ${(err as Error).message}` }]);
      setRunning(false);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>Multi-Agent Supervisor</h1>
        <span className="subtitle">LangGraph.js · AG-UI · Anthropic Claude</span>
      </header>

      <main>
        <section className="chat" aria-label="Conversation">
          <ul>
            {chat.map((turn, i) => (
              <li key={i} className={`turn turn-${turn.role}`}>
                <span className="turn-role">{turn.role}</span>
                <span className="turn-text">{turn.text}</span>
              </li>
            ))}
          </ul>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={running ? "agent is working…" : "ask the supervisor anything"}
              disabled={running}
              aria-label="Message"
            />
            <button type="submit" disabled={running || !input.trim()}>
              Send
            </button>
          </form>
        </section>

        <section className="trace-pane" aria-label="Agent trace">
          <h2>Live trace</h2>
          <Trace items={trace} />
        </section>
      </main>

      {pending && (
        <ApprovalModal
          reason={pending.reason}
          payload={pending.payload}
          onDecide={decide}
        />
      )}
    </div>
  );
}
