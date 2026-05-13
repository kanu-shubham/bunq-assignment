import React, { ReactNode, useEffect, useRef } from 'react';
import ReactDOM from 'react-dom';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useEscapeKey } from '../hooks/useEscapeKey';
import './Modal.css';

let portalRoot: HTMLDivElement | null = null;
function getPortalRoot(): HTMLDivElement | null {
  if (typeof document === 'undefined') return null;
  if (portalRoot && document.body.contains(portalRoot)) return portalRoot;
  const existing = document.getElementById('feedback-portal') as HTMLDivElement | null;
  if (existing) {
    portalRoot = existing;
  } else {
    portalRoot = document.createElement('div');
    portalRoot.setAttribute('id', 'feedback-portal');
    document.body.appendChild(portalRoot);
  }
  return portalRoot;
}

export type ModalVariant = 'default' | 'dark' | 'blue' | 'review';

export interface ModalProps {
  open: boolean;
  onClose?: () => void;
  labelledBy?: string;
  describedBy?: string;
  variant?: ModalVariant;
  dismissable?: boolean;
  showClose?: boolean;
  role?: 'dialog' | 'alertdialog' | 'status';
  children: ReactNode;
}

/**
 * Accessible modal primitive: portal mount, focus trap, ESC + backdrop
 * dismiss, aria-modal + labelling, body-scroll lock. Variants are pure CSS.
 */
export function Modal({
  open,
  onClose,
  labelledBy,
  describedBy,
  variant = 'default',
  dismissable = true,
  showClose = true,
  role = 'dialog',
  children,
}: ModalProps): JSX.Element | null {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open);
  useEscapeKey(() => {
    if (dismissable) onClose?.();
  }, open);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;
  const root = getPortalRoot();
  if (!root) return null;

  const onBackdropMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!dismissable) return;
    if (e.target === e.currentTarget) onClose?.();
  };

  return ReactDOM.createPortal(
    <div className="fb-backdrop" onMouseDown={onBackdropMouseDown} data-testid="fb-backdrop">
      <div
        ref={ref}
        role={role}
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        className={`fb-modal fb-modal--${variant}`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {showClose && (
          <button
            type="button"
            className="fb-modal__close"
            aria-label="Close"
            onClick={onClose}
            data-testid="fb-close"
          >
            ×
          </button>
        )}
        {children}
      </div>
    </div>,
    root,
  );
}
