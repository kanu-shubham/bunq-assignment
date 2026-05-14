import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { NegativeFeedbackForm } from './NegativeFeedbackForm';

describe('<NegativeFeedbackForm />', () => {
  test('renders the heading with the supplied titleId', () => {
    render(<NegativeFeedbackForm onSubmit={jest.fn()} titleId="neg-title" />);
    expect(screen.getByRole('heading', { name: /how can we make things better/i }))
      .toHaveAttribute('id', 'neg-title');
  });

  test('submit is disabled while the textarea is empty or whitespace', () => {
    render(<NegativeFeedbackForm onSubmit={jest.fn()} titleId="t" />);
    const submit = screen.getByTestId('fb-negative-submit');
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId('fb-negative-text'), { target: { value: '   ' } });
    expect(submit).toBeDisabled();
  });

  test('submit enables once non-whitespace is entered', () => {
    render(<NegativeFeedbackForm onSubmit={jest.fn()} titleId="t" />);
    fireEvent.change(screen.getByTestId('fb-negative-text'), { target: { value: 'good' } });
    expect(screen.getByTestId('fb-negative-submit')).not.toBeDisabled();
  });

  test('onSubmit receives the trimmed comment', () => {
    const onSubmit = jest.fn();
    render(<NegativeFeedbackForm onSubmit={onSubmit} titleId="t" />);

    fireEvent.change(screen.getByTestId('fb-negative-text'), { target: { value: '  hello  ' } });
    fireEvent.click(screen.getByTestId('fb-negative-submit'));

    expect(onSubmit).toHaveBeenCalledWith('hello');
  });

  test('does not submit when disabled', () => {
    const onSubmit = jest.fn();
    const { container } = render(<NegativeFeedbackForm onSubmit={onSubmit} titleId="t" />);

    // Submit via form submission to bypass the disabled button.
    const form = container.querySelector('form')!;
    fireEvent.submit(form);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  test('respects the MAX_LENGTH cap by slicing pasted overflow', () => {
    render(<NegativeFeedbackForm onSubmit={jest.fn()} titleId="t" />);
    const textarea = screen.getByTestId('fb-negative-text') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'a'.repeat(2500) } });
    expect(textarea.value.length).toBe(2000);
  });

  test('disables textarea and shows submitting label when submitting', () => {
    render(<NegativeFeedbackForm onSubmit={jest.fn()} submitting titleId="t" />);
    expect(screen.getByTestId('fb-negative-text')).toBeDisabled();
    expect(screen.getByTestId('fb-negative-submit')).toHaveTextContent(/submitting/i);
  });

  test('announces submitting via the polite live region', () => {
    const { rerender } = render(<NegativeFeedbackForm onSubmit={jest.fn()} titleId="t" />);
    const live = screen.getByTestId('fb-negative-live');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveTextContent('');

    rerender(<NegativeFeedbackForm onSubmit={jest.fn()} submitting titleId="t" />);
    expect(live).toHaveTextContent(/submitting your feedback/i);
  });

  test('renders the error in an alert and sets aria-invalid + aria-describedby', () => {
    render(<NegativeFeedbackForm onSubmit={jest.fn()} error="Network down" titleId="t" />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/network down/i);

    const textarea = screen.getByTestId('fb-negative-text');
    expect(textarea).toHaveAttribute('aria-invalid', 'true');
    expect(textarea).toHaveAttribute('aria-describedby', alert.id);
  });

  test('aria-busy reflects submitting state', () => {
    const { container, rerender } = render(<NegativeFeedbackForm onSubmit={jest.fn()} titleId="t" />);
    const form = container.querySelector('form')!;
    expect(form).toHaveAttribute('aria-busy', 'false');

    rerender(<NegativeFeedbackForm onSubmit={jest.fn()} submitting titleId="t" />);
    expect(form).toHaveAttribute('aria-busy', 'true');
  });
});
