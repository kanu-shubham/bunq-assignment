import {
  Action,
  FeedbackState,
  RATING,
  STATUS,
  initialState,
  reducer,
} from './feedbackMachine';

const open = (): FeedbackState => reducer(initialState, { type: 'OPEN' });

describe('feedbackMachine', () => {
  test('OPEN moves from CLOSED → RATING', () => {
    const next = open();
    expect(next.status).toBe(STATUS.RATING);
    expect(next.rating).toBeNull();
  });

  test('CLOSE resets state', () => {
    const rated = reducer(open(), { type: 'RATE', rating: RATING.STELLAR });
    const closed = reducer(rated, { type: 'CLOSE' });
    expect(closed).toEqual({ ...initialState, status: STATUS.CLOSED });
  });

  describe('RATE', () => {
    test('NEGATIVE → NEGATIVE_FORM', () => {
      const next = reducer(open(), { type: 'RATE', rating: RATING.NEGATIVE });
      expect(next.status).toBe(STATUS.NEGATIVE_FORM);
      expect(next.rating).toBe(RATING.NEGATIVE);
    });

    test.each([RATING.POSITIVE, RATING.STELLAR] as const)('%s → THANK_YOU', (rating) => {
      const next = reducer(open(), { type: 'RATE', rating });
      expect(next.status).toBe(STATUS.THANK_YOU);
      expect(next.rating).toBe(rating);
    });

    test('ignored when not in RATING state', () => {
      const rated = reducer(open(), { type: 'RATE', rating: RATING.NEGATIVE });
      const again = reducer(rated, { type: 'RATE', rating: RATING.POSITIVE });
      expect(again).toEqual(rated);
    });
  });

  describe('submit flow', () => {
    const inForm = (): FeedbackState =>
      reducer(open(), { type: 'RATE', rating: RATING.NEGATIVE });

    test('SUBMIT_START → SUBMITTING (clears error)', () => {
      const start = { ...inForm(), error: 'prev' };
      const next = reducer(start, { type: 'SUBMIT_START' });
      expect(next.status).toBe(STATUS.SUBMITTING);
      expect(next.error).toBeNull();
    });

    test('SUBMIT_SUCCESS → THANK_YOU with comment', () => {
      const submitting = reducer(inForm(), { type: 'SUBMIT_START' });
      const next = reducer(submitting, { type: 'SUBMIT_SUCCESS', comment: 'broken' });
      expect(next.status).toBe(STATUS.THANK_YOU);
      expect(next.comment).toBe('broken');
    });

    test('SUBMIT_ERROR → NEGATIVE_FORM with error', () => {
      const submitting = reducer(inForm(), { type: 'SUBMIT_START' });
      const next = reducer(submitting, { type: 'SUBMIT_ERROR', error: 'oops' });
      expect(next.status).toBe(STATUS.NEGATIVE_FORM);
      expect(next.error).toBe('oops');
    });

    test('SUBMIT_START ignored when not in NEGATIVE_FORM', () => {
      const next = reducer(initialState, { type: 'SUBMIT_START' });
      expect(next).toBe(initialState);
    });
  });

  describe('THANK_YOU_DONE', () => {
    test('STELLAR → TRUSTPILOT', () => {
      const stellar = reducer(open(), { type: 'RATE', rating: RATING.STELLAR });
      const next = reducer(stellar, { type: 'THANK_YOU_DONE' });
      expect(next.status).toBe(STATUS.TRUSTPILOT);
    });

    test('POSITIVE → CLOSED', () => {
      const positive = reducer(open(), { type: 'RATE', rating: RATING.POSITIVE });
      const next = reducer(positive, { type: 'THANK_YOU_DONE' });
      expect(next.status).toBe(STATUS.CLOSED);
    });

    test('NEGATIVE after submit → CLOSED', () => {
      const neg = reducer(open(), { type: 'RATE', rating: RATING.NEGATIVE });
      const submitting = reducer(neg, { type: 'SUBMIT_START' });
      const thanks = reducer(submitting, { type: 'SUBMIT_SUCCESS', comment: 'hi' });
      const next = reducer(thanks, { type: 'THANK_YOU_DONE' });
      expect(next.status).toBe(STATUS.CLOSED);
    });
  });

  test('unknown action returns same reference', () => {
    // Cast through unknown — the whole point of the test is to verify the
    // runtime guard, which TS would otherwise reject.
    const bogus = { type: 'WAT' } as unknown as Action;
    expect(reducer(initialState, bogus)).toBe(initialState);
  });
});
