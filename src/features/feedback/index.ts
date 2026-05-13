export { FeedbackWidget } from './FeedbackWidget';
export type { FeedbackWidgetProps } from './FeedbackWidget';
export { RATING, STATUS } from './state/feedbackMachine';
export type { Rating, Status, FeedbackState } from './state/feedbackMachine';
export {
  submitFeedback,
  sanitizeComment,
  FeedbackValidationError,
} from './services/feedbackService';
export type { FeedbackPayload, SubmitOptions } from './services/feedbackService';
