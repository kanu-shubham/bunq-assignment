import {
  FeedbackValidationError,
  sanitizeComment,
  submitFeedback,
} from './feedbackService';
import { RATING } from '../state/feedbackMachine';

describe('sanitizeComment', () => {
  test('trims whitespace', () => {
    expect(sanitizeComment('   hello  ')).toBe('hello');
  });

  test('caps length at 2000 chars', () => {
    expect(sanitizeComment('a'.repeat(5000)).length).toBe(2000);
  });

  test('coerces non-strings to empty', () => {
    expect(sanitizeComment(null)).toBe('');
    expect(sanitizeComment(undefined)).toBe('');
    expect(sanitizeComment(42)).toBe('');
  });
});

describe('submitFeedback', () => {
  test('throws when rating missing', async () => {
    // Force a missing rating through the type system for the runtime guard.
    await expect(
      submitFeedback({} as unknown as { rating: typeof RATING[keyof typeof RATING] }),
    ).rejects.toBeInstanceOf(FeedbackValidationError);
  });

  test('POSTs JSON to endpoint and returns response body', async () => {
    const fetchImpl = jest.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ id: 'abc' }),
    });
    const out = await submitFeedback(
      { rating: RATING.NEGATIVE, comment: '  bad  ' },
      { fetchImpl: fetchImpl as unknown as typeof fetch, endpoint: '/x' },
    );
    expect(fetchImpl).toHaveBeenCalledWith('/x', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating: 'NEGATIVE', comment: 'bad' }),
    }));
    expect(out).toEqual({ id: 'abc' });
  });

  test('throws on non-OK response', async () => {
    const fetchImpl = jest.fn().mockResolvedValue({ ok: false, status: 500, json: () => ({}) });
    await expect(
      submitFeedback({ rating: RATING.POSITIVE }, { fetchImpl: fetchImpl as unknown as typeof fetch }),
    ).rejects.toThrow(/500/);
  });
});
