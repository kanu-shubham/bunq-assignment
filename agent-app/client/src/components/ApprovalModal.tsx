interface Props {
  reason: string;
  payload: { kind?: string; to?: string; subject?: string; body?: string } & Record<string, unknown>;
  onDecide: (approved: boolean, note?: string) => void;
}

export function ApprovalModal({ reason, payload, onDecide }: Props) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="hitl-title">
      <div className="modal">
        <h2 id="hitl-title">Approval required</h2>
        <p className="modal-reason">{reason}</p>
        {payload.kind === "approve_email" && (
          <dl className="approval-fields">
            <dt>To</dt><dd>{payload.to}</dd>
            <dt>Subject</dt><dd>{payload.subject}</dd>
            <dt>Body</dt><dd><pre>{payload.body}</pre></dd>
          </dl>
        )}
        {payload.kind !== "approve_email" && (
          <pre className="approval-raw">{JSON.stringify(payload, null, 2)}</pre>
        )}
        <div className="modal-actions">
          <button className="btn-reject" onClick={() => onDecide(false, "rejected by operator")}>
            Reject
          </button>
          <button className="btn-approve" onClick={() => onDecide(true)}>
            Approve
          </button>
        </div>
      </div>
    </div>
  );
}
