/**
 * INTERVIEW TOPIC: HITL approval card — rendering a tool call as an
 * interactive checkpoint instead of executing it silently.
 *
 * Design talking points:
 *  - Args are Zod-validated *before* a human is asked to approve. If the LLM
 *    produced invalid args, Approve is not rendered at all: a human can't be
 *    tricked into rubber-stamping garbage (illegal action made unrepresentable
 *    in the UI, not just guarded in the handler).
 *  - The decision and result stay visible afterwards — the card is the audit
 *    record of who approved what.
 */
import type { ToolMessage } from '../types/agentEvents';
import { transferFundsArgs } from '../schemas/llmOutput';

interface Props {
  message: ToolMessage;
  onDecide: (decision: 'approved' | 'rejected') => void;
}

export function ToolApprovalCard({ message, onDecide }: Props) {
  const parsed = transferFundsArgs.safeParse(message.toolCall.args);

  return (
    <div className={`tool-card tool-card--${message.status}`}>
      <div className="tool-card__header">
        <span className="tool-card__name">{message.toolCall.name}</span>
        <span className="tool-card__status">{message.status}</span>
      </div>

      {parsed.success ? (
        <dl className="tool-card__args">
          <dt>Amount</dt>
          <dd>
            {parsed.data.amount.toFixed(2)} {parsed.data.currency}
          </dd>
          <dt>To</dt>
          <dd>{parsed.data.toIban}</dd>
          {parsed.data.reference && (
            <>
              <dt>Reference</dt>
              <dd>{parsed.data.reference}</dd>
            </>
          )}
        </dl>
      ) : (
        <div className="tool-card__invalid" role="alert">
          <p>The agent produced invalid arguments — this call cannot be approved:</p>
          <ul>
            {parsed.error.issues.map((issue) => (
              <li key={issue.path.join('.')}>
                <code>{issue.path.join('.') || '(root)'}</code>: {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {message.status === 'pending' && (
        <div className="tool-card__actions">
          {parsed.success && (
            <button type="button" className="btn btn--approve" onClick={() => onDecide('approved')}>
              Approve
            </button>
          )}
          <button type="button" className="btn btn--reject" onClick={() => onDecide('rejected')}>
            Reject
          </button>
        </div>
      )}

      {message.result && <p className="tool-card__result">{message.result}</p>}
    </div>
  );
}
