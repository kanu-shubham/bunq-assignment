import type { Rating } from '../state/feedbackMachine';

const MAX_COMMENT_LENGTH = 2000;

export class FeedbackValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FeedbackValidationError';
  }
}

export interface FeedbackPayload {
  rating: Rating;
  comment?: string;
}

export interface SubmitOptions {
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

export function sanitizeComment(raw: unknown): string {
  if (typeof raw !== 'string') return '';
  // React escapes when rendering, so DOM XSS isn't a worry here. We still
  // bound the payload server-side to avoid abuse.
  return raw.trim().slice(0, MAX_COMMENT_LENGTH);
}

export async function submitFeedback(
  { rating, comment }: FeedbackPayload,
  { fetchImpl = fetch, endpoint = '/api/feedback' }: SubmitOptions = {},
): Promise<unknown> {
  if (!rating) throw new FeedbackValidationError('rating is required');

  const body = JSON.stringify({ rating, comment: sanitizeComment(comment) });

  const res = await fetchImpl(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });

  if (!res.ok) {
    throw new Error(`Feedback submission failed: ${res.status}`);
  }
  return res.json().catch(() => ({}));
}

export const __test__ = { MAX_COMMENT_LENGTH };
