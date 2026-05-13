import React from 'react';
import { RATING, Rating } from '../state/feedbackMachine';
import './RatingPrompt.css';

interface Option {
  rating: Rating;
  emoji: string;
  label: string;
}

const OPTIONS: ReadonlyArray<Option> = [
  { rating: RATING.NEGATIVE, emoji: '👎', label: 'Negative' },
  { rating: RATING.POSITIVE, emoji: '👍', label: 'Positive' },
  { rating: RATING.STELLAR,  emoji: '😍', label: 'Stellar'  },
];

export interface RatingPromptProps {
  onRate: (rating: Rating) => void;
  titleId: string;
}

export function RatingPrompt({ onRate, titleId }: RatingPromptProps): JSX.Element {
  return (
    <div className="fb-rating">
      <h2 id={titleId} className="fb-rating__title">How would you rate this feature?</h2>
      <div className="fb-rating__options" role="group" aria-labelledby={titleId}>
        {OPTIONS.map(({ rating, emoji, label }) => (
          <button
            key={rating}
            type="button"
            className="fb-rating__btn"
            aria-label={label}
            onClick={() => onRate(rating)}
            data-rating={rating}
          >
            <span aria-hidden="true">{emoji}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
