import React, { FormEvent, useState } from 'react';
import { useStableId } from '../hooks/useStableId';
import './NegativeFeedbackForm.css';

const MAX_LENGTH = 2000;

export interface NegativeFeedbackFormProps {
  onSubmit: (comment: string) => void;
  submitting?: boolean;
  error?: string | null;
  titleId: string;
}

export function NegativeFeedbackForm({
  onSubmit,
  submitting = false,
  error = null,
  titleId,
}: NegativeFeedbackFormProps): JSX.Element {
  const [value, setValue] = useState('');
  const errorId = useStableId('fb-err');
  const trimmed = value.trim();
  const disabled = submitting || trimmed.length === 0;

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (disabled) return;
    onSubmit(trimmed);
  };

  return (
    <form className="fb-negative" onSubmit={handleSubmit} noValidate aria-busy={submitting}>
      <h2 id={titleId} className="fb-negative__title">How can we make things better?</h2>
      <label className="fb-visually-hidden" htmlFor={`${titleId}-text`}>
        Your feedback
      </label>
      <textarea
        id={`${titleId}-text`}
        className="fb-negative__textarea"
        value={value}
        onChange={(e) => setValue(e.target.value.slice(0, MAX_LENGTH))}
        maxLength={MAX_LENGTH}
        rows={4}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        disabled={submitting}
        autoFocus
        data-testid="fb-negative-text"
      />
      {error && (
        <p id={errorId} role="alert" className="fb-negative__error">{error}</p>
      )}
      {/* Polite live region: announces "Submitting your feedback…" once the
          form enters the SUBMITTING state. Visually hidden — the button
          label changes for sighted users; this is the screen-reader twin. */}
      <span role="status" aria-live="polite" className="fb-visually-hidden" data-testid="fb-negative-live">
        {submitting ? 'Submitting your feedback…' : ''}
      </span>
      <div className="fb-negative__footer">
        <button
          type="submit"
          className="fb-negative__submit"
          disabled={disabled}
          data-testid="fb-negative-submit"
        >
          {submitting ? 'Submitting…' : 'Submit'}
        </button>
      </div>
    </form>
  );
}
