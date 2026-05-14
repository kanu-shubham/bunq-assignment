import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { RatingPrompt } from './RatingPrompt';
import { RATING } from '../state/feedbackMachine';

describe('<RatingPrompt />', () => {
  test('renders title with the supplied id', () => {
    render(<RatingPrompt onRate={jest.fn()} titleId="title-1" />);
    const heading = screen.getByRole('heading', { name: /how would you rate/i });
    expect(heading).toHaveAttribute('id', 'title-1');
  });

  test('option group is labelled by the title', () => {
    render(<RatingPrompt onRate={jest.fn()} titleId="title-2" />);
    expect(screen.getByRole('group')).toHaveAttribute('aria-labelledby', 'title-2');
  });

  test('renders one button per rating with accessible labels', () => {
    render(<RatingPrompt onRate={jest.fn()} titleId="t" />);
    expect(screen.getByRole('button', { name: 'Negative' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Positive' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stellar' })).toBeInTheDocument();
  });

  test('invokes onRate with the correct rating value', () => {
    const onRate = jest.fn();
    render(<RatingPrompt onRate={onRate} titleId="t" />);

    fireEvent.click(screen.getByRole('button', { name: 'Negative' }));
    fireEvent.click(screen.getByRole('button', { name: 'Positive' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stellar' }));

    expect(onRate).toHaveBeenNthCalledWith(1, RATING.NEGATIVE);
    expect(onRate).toHaveBeenNthCalledWith(2, RATING.POSITIVE);
    expect(onRate).toHaveBeenNthCalledWith(3, RATING.STELLAR);
  });

  test('emojis are hidden from assistive tech', () => {
    const { container } = render(<RatingPrompt onRate={jest.fn()} titleId="t" />);
    container.querySelectorAll('button span').forEach((span) => {
      expect(span).toHaveAttribute('aria-hidden', 'true');
    });
  });
});
