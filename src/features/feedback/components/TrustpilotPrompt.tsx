import React from 'react';
import './TrustpilotPrompt.css';

const DEFAULT_URL = 'https://www.trustpilot.com/review/bunq.com';

export interface TrustpilotPromptProps {
  trustpilotUrl?: string;
  onClose?: () => void;
  titleId: string;
}

export function TrustpilotPrompt({
  trustpilotUrl = DEFAULT_URL,
  onClose,
  titleId,
}: TrustpilotPromptProps): JSX.Element {
  return (
    <div className="fb-trustpilot">
      <h2 id={titleId} className="fb-trustpilot__title">Enjoying bunq?</h2>
      <p className="fb-trustpilot__body">
        Your feedback helps us improve!<br />
        Please leave us a review on Trustpilot.
      </p>
      <a
        className="fb-trustpilot__cta"
        href={trustpilotUrl}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => onClose?.()}
        data-testid="fb-trustpilot-cta"
      >
        Go to Trustpilot
      </a>
    </div>
  );
}
