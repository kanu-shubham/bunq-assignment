import React from 'react';
import { useAutoDismiss } from '../hooks/useAutoDismiss';
import './ThankYouToast.css';

export interface ThankYouToastProps {
  active: boolean;
  onDone: () => void;
  delay?: number;
  titleId: string;
}

export function ThankYouToast({ active, onDone, delay = 2000, titleId }: ThankYouToastProps): JSX.Element {
  useAutoDismiss(onDone, { active, delay });

  return (
    <div className="fb-thanks" role="status" aria-live="polite">
      <svg
        className="fb-thanks__check"
        viewBox="0 0 24 24"
        width="44"
        height="44"
        aria-hidden="true"
        focusable="false"
      >
        <path
          d="M5 12.5l4.5 4.5L19 7"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <p id={titleId} className="fb-thanks__text">Thanks for your feedback!</p>
    </div>
  );
}
