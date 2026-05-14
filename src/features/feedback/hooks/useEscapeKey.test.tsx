import React from 'react';
import { render, fireEvent } from '@testing-library/react';
import { useEscapeKey } from './useEscapeKey';

function Probe({ handler, enabled }: { handler: (e: KeyboardEvent) => void; enabled?: boolean }) {
  useEscapeKey(handler, enabled);
  return null;
}

describe('useEscapeKey', () => {
  test('invokes the handler when Escape is pressed', () => {
    const handler = jest.fn();
    render(<Probe handler={handler} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  test('ignores other keys', () => {
    const handler = jest.fn();
    render(<Probe handler={handler} />);
    fireEvent.keyDown(document, { key: 'Enter' });
    fireEvent.keyDown(document, { key: 'a' });
    expect(handler).not.toHaveBeenCalled();
  });

  test('does not subscribe when disabled', () => {
    const handler = jest.fn();
    render(<Probe handler={handler} enabled={false} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handler).not.toHaveBeenCalled();
  });

  test('unsubscribes on unmount', () => {
    const handler = jest.fn();
    const { unmount } = render(<Probe handler={handler} />);
    unmount();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handler).not.toHaveBeenCalled();
  });

  test('re-subscribes when enabled flips from false to true', () => {
    const handler = jest.fn();
    const { rerender } = render(<Probe handler={handler} enabled={false} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handler).not.toHaveBeenCalled();

    rerender(<Probe handler={handler} enabled />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
