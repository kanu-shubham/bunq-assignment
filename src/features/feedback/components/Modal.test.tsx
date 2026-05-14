import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { Modal } from './Modal';

describe('<Modal />', () => {
  afterEach(() => {
    document.body.style.overflow = '';
  });

  test('renders nothing when closed', () => {
    const { container } = render(
      <Modal open={false}>
        <p>hidden</p>
      </Modal>,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText('hidden')).not.toBeInTheDocument();
  });

  test('portals children into the feedback-portal node', () => {
    render(
      <Modal open>
        <p data-testid="child">visible</p>
      </Modal>,
    );
    const portal = document.getElementById('feedback-portal');
    expect(portal).not.toBeNull();
    expect(portal!.contains(screen.getByTestId('child'))).toBe(true);
  });

  test('sets aria-modal, role, and labelling attributes', () => {
    render(
      <Modal open labelledBy="title" describedBy="desc" role="alertdialog">
        <p>x</p>
      </Modal>,
    );
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 'title');
    expect(dialog).toHaveAttribute('aria-describedby', 'desc');
  });

  test('applies the variant class', () => {
    render(
      <Modal open variant="blue">
        <p>x</p>
      </Modal>,
    );
    expect(screen.getByRole('dialog')).toHaveClass('fb-modal--blue');
  });

  test('shows the close button by default and invokes onClose when clicked', () => {
    const onClose = jest.fn();
    render(
      <Modal open onClose={onClose}>
        <p>x</p>
      </Modal>,
    );
    fireEvent.click(screen.getByTestId('fb-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('hides the close button when showClose is false', () => {
    render(
      <Modal open showClose={false}>
        <p>x</p>
      </Modal>,
    );
    expect(screen.queryByTestId('fb-close')).not.toBeInTheDocument();
  });

  test('clicking the backdrop closes when dismissable', () => {
    const onClose = jest.fn();
    render(
      <Modal open onClose={onClose}>
        <p>x</p>
      </Modal>,
    );
    const backdrop = screen.getByTestId('fb-backdrop');
    fireEvent.mouseDown(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('clicking inside the dialog does not close', () => {
    const onClose = jest.fn();
    render(
      <Modal open onClose={onClose}>
        <p data-testid="child">x</p>
      </Modal>,
    );
    fireEvent.mouseDown(screen.getByTestId('child'));
    expect(onClose).not.toHaveBeenCalled();
  });

  test('non-dismissable modal ignores backdrop clicks and ESC', () => {
    const onClose = jest.fn();
    render(
      <Modal open onClose={onClose} dismissable={false}>
        <p>x</p>
      </Modal>,
    );
    fireEvent.mouseDown(screen.getByTestId('fb-backdrop'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  test('ESC dismisses when dismissable', () => {
    const onClose = jest.fn();
    render(
      <Modal open onClose={onClose}>
        <p>x</p>
      </Modal>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('locks body scroll while open and restores on close', () => {
    document.body.style.overflow = 'auto';
    const { rerender } = render(
      <Modal open>
        <p>x</p>
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('hidden');

    rerender(
      <Modal open={false}>
        <p>x</p>
      </Modal>,
    );
    expect(document.body.style.overflow).toBe('auto');
  });
});
