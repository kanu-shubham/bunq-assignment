/**
 * Pure finite-state machine for the feedback flow.
 *
 * Modelled as a discriminated union of states + actions so the type system
 * (rather than tests alone) rules out illegal transitions in source.
 */

export const STATUS = {
  CLOSED: 'CLOSED',
  RATING: 'RATING',
  NEGATIVE_FORM: 'NEGATIVE_FORM',
  SUBMITTING: 'SUBMITTING',
  THANK_YOU: 'THANK_YOU',
  TRUSTPILOT: 'TRUSTPILOT',
} as const;

export type Status = (typeof STATUS)[keyof typeof STATUS];

export const RATING = {
  NEGATIVE: 'NEGATIVE',
  POSITIVE: 'POSITIVE',
  STELLAR: 'STELLAR',
} as const;

export type Rating = (typeof RATING)[keyof typeof RATING];

export interface FeedbackState {
  status: Status;
  rating: Rating | null;
  comment: string;
  error: string | null;
}

export const initialState: FeedbackState = Object.freeze({
  status: STATUS.CLOSED,
  rating: null,
  comment: '',
  error: null,
});

export type Action =
  | { type: 'OPEN' }
  | { type: 'CLOSE' }
  | { type: 'RATE'; rating: Rating }
  | { type: 'SUBMIT_START' }
  | { type: 'SUBMIT_SUCCESS'; comment?: string }
  | { type: 'SUBMIT_ERROR'; error?: string }
  | { type: 'THANK_YOU_DONE' };

export const ACTION = {
  OPEN: 'OPEN',
  CLOSE: 'CLOSE',
  RATE: 'RATE',
  SUBMIT_START: 'SUBMIT_START',
  SUBMIT_SUCCESS: 'SUBMIT_SUCCESS',
  SUBMIT_ERROR: 'SUBMIT_ERROR',
  THANK_YOU_DONE: 'THANK_YOU_DONE',
} as const;

export function reducer(state: FeedbackState, action: Action): FeedbackState {
  switch (action.type) {
    case 'OPEN':
      return { ...initialState, status: STATUS.RATING };

    case 'CLOSE':
      return { ...initialState, status: STATUS.CLOSED };

    case 'RATE': {
      if (state.status !== STATUS.RATING) return state;
      if (action.rating === RATING.NEGATIVE) {
        return { ...state, rating: action.rating, status: STATUS.NEGATIVE_FORM };
      }
      return { ...state, rating: action.rating, status: STATUS.THANK_YOU };
    }

    case 'SUBMIT_START':
      if (state.status !== STATUS.NEGATIVE_FORM) return state;
      return { ...state, status: STATUS.SUBMITTING, error: null };

    case 'SUBMIT_SUCCESS':
      if (state.status !== STATUS.SUBMITTING) return state;
      return { ...state, comment: action.comment ?? '', status: STATUS.THANK_YOU };

    case 'SUBMIT_ERROR':
      if (state.status !== STATUS.SUBMITTING) return state;
      return { ...state, status: STATUS.NEGATIVE_FORM, error: action.error ?? 'Submission failed' };

    case 'THANK_YOU_DONE':
      if (state.status !== STATUS.THANK_YOU) return state;
      return state.rating === RATING.STELLAR
        ? { ...state, status: STATUS.TRUSTPILOT }
        : { ...initialState, status: STATUS.CLOSED };

    default: {
      // Exhaustiveness check — compile error if a new Action variant is added
      // without a case here.
      const _exhaustive: never = action;
      return state;
    }
  }
}
