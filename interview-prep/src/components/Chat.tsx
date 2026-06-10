/**
 * THE CORE EXERCISE: a chat UI that streams a response and renders a tool
 * call as an interactive approval card.
 *
 * Things to point at while whiteboarding:
 *  - One source of truth (useChatStream's reducer); components are pure
 *    renderers of ChatMessage variants.
 *  - The discriminated union means the render switch is exhaustive — adding
 *    a message role without rendering it is a compile error.
 *  - Every status has a visible UI: streaming (stop button, pulsing cursor),
 *    awaiting_approval (input disabled — the human must decide first),
 *    error (banner with retry), idle.
 */
import { useRef, useState } from 'react';
import { useChatStream } from '../hooks/useChatStream';
import { assertNever, type ChatMessage, type ToolCall } from '../types/agentEvents';
import { ToolApprovalCard } from './ToolApprovalCard';

function MessageView({
  message,
  onDecide,
}: {
  message: ChatMessage;
  onDecide: (toolCall: ToolCall, d: 'approved' | 'rejected') => void;
}) {
  switch (message.role) {
    case 'user':
      return <div className="msg msg--user">{message.content}</div>;
    case 'assistant':
      return (
        <div className="msg msg--assistant">
          {message.content}
          {message.streaming && <span className="cursor" aria-hidden="true" />}
        </div>
      );
    case 'tool':
      return <ToolApprovalCard message={message} onDecide={(d) => onDecide(message.toolCall, d)} />;
    default:
      return assertNever(message);
  }
}

export function Chat() {
  const { messages, status, error, send, decideTool, stop } = useChatStream();
  const [input, setInput] = useState('');
  const lastSent = useRef('');

  const busy = status === 'streaming' || status === 'awaiting_approval';

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    lastSent.current = text;
    setInput('');
    send(text);
  };

  return (
    <div className="chat">
      <div className="chat__log" role="log" aria-live="polite">
        {messages.map((m) => (
          <MessageView key={m.id} message={m} onDecide={decideTool} />
        ))}
        {status === 'awaiting_approval' && (
          <p className="chat__hint">Waiting for your decision on the tool call above.</p>
        )}
      </div>

      {error && (
        <div className="chat__error" role="alert">
          <span>{error}</span>
          <button type="button" className="btn" onClick={() => send(lastSent.current)}>
            Retry
          </button>
        </div>
      )}

      <form className="chat__composer" onSubmit={submit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={busy ? 'Agent is working…' : 'e.g. “Pay the €250 invoice from ACME”'}
          disabled={busy}
          aria-label="Message"
        />
        {status === 'streaming' ? (
          <button type="button" className="btn" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="btn btn--primary" disabled={busy || !input.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}
