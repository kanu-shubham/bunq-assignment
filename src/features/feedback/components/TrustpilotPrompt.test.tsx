import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { TrustpilotPrompt } from './TrustpilotPrompt';

describe('<TrustpilotPrompt />', () => {
  test('renders the heading with the supplied titleId', () => {
    render(<TrustpilotPrompt titleId="tp-title" />);
    expect(screen.getByRole('heading', { name: /enjoying bunq/i }))
      .toHaveAttribute('id', 'tp-title');
  });

  test('falls back to the default Trustpilot URL when none provided', () => {
    render(<TrustpilotPrompt titleId="t" />);
    expect(screen.getByTestId('fb-trustpilot-cta'))
      .toHaveAttribute('href', 'https://www.trustpilot.com/review/bunq.com');
  });

  test('uses the supplied trustpilotUrl', () => {
    render(<TrustpilotPrompt titleId="t" trustpilotUrl="https://example.com/r" />);
    expect(screen.getByTestId('fb-trustpilot-cta')).toHaveAttribute('href', 'https://example.com/r');
  });

  test('CTA opens in a new tab safely', () => {
    render(<TrustpilotPrompt titleId="t" />);
    const cta = screen.getByTestId('fb-trustpilot-cta');
    expect(cta).toHaveAttribute('target', '_blank');
    expect(cta).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(cta).toHaveAttribute('rel', expect.stringContaining('noreferrer'));
  });

  test('clicking the CTA invokes onClose', () => {
    const onClose = jest.fn();
    render(<TrustpilotPrompt titleId="t" onClose={onClose} />);
    fireEvent.click(screen.getByTestId('fb-trustpilot-cta'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('does not crash when onClose is omitted', () => {
    render(<TrustpilotPrompt titleId="t" />);
    expect(() => fireEvent.click(screen.getByTestId('fb-trustpilot-cta'))).not.toThrow();
  });
});
