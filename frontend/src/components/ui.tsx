import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";

export function Button({
  variant = "secondary",
  busy = false,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button className={`btn btn-${variant}`} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

export function TextInput({ label, hint, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return <Field label={label} hint={hint}>{(id) => <input id={id} {...rest} />}</Field>;
}

export function TextArea({ label, hint, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hint?: string }) {
  return <Field label={label} hint={hint}>{(id) => <textarea id={id} {...rest} />}</Field>;
}

export function Select({
  label,
  hint,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { label: string; hint?: string }) {
  return <Field label={label} hint={hint}>{(id) => <select id={id} {...rest}>{children}</select>}</Field>;
}

export function Badge({ tone, children }: { tone: "green" | "red" | "gray" | "amber" | "blue"; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function ErrorNote({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <div className="note note-error" role="alert">
      {message}
    </div>
  );
}

export function InfoNote({ children }: { children: ReactNode }) {
  return <div className="note note-info">{children}</div>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" /> {label}…
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <p className="muted">{children}</p> : null}
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p className="muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>("input, select, textarea, button");
    first?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && panel.current) {
        const items = panel.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        const firstItem = items[0];
        const lastItem = items[items.length - 1];
        if (!firstItem || !lastItem) return;
        if (event.shiftKey && document.activeElement === firstItem) {
          event.preventDefault();
          lastItem.focus();
        } else if (!event.shiftKey && document.activeElement === lastItem) {
          event.preventDefault();
          firstItem.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div ref={panel} className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard blocked: the text is selectable on screen */
        }
      }}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

/** A destructive action that asks first, inline, without a browser dialog. */
export function ConfirmButton({
  children,
  confirmLabel = "Confirm",
  onConfirm,
  busy,
}: {
  children: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void | Promise<void>;
  busy?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <Button variant="danger" onClick={() => setAsking(true)}>
        {children}
      </Button>
    );
  }
  return (
    <span className="confirm">
      <Button
        variant="danger"
        busy={busy}
        onClick={async () => {
          await onConfirm();
          setAsking(false);
        }}
      >
        {confirmLabel}
      </Button>
      <Button variant="ghost" onClick={() => setAsking(false)}>
        Cancel
      </Button>
    </span>
  );
}

/**
 * Shows a newly issued API key exactly once. The server stores only a hash, so closing
 * this dialog is the last time the key can be read.
 */
export function KeyRevealModal({
  apiKey,
  name,
  gatewayUrl,
  onClose,
}: {
  apiKey: string;
  name: string;
  gatewayUrl: string;
  onClose: () => void;
}) {
  const snippet = `Authorization: Bearer ${apiKey}`;
  return (
    <Modal title="Copy your new API key" onClose={onClose} wide>
      <p>
        This is the only time the key <strong>{name}</strong> will be shown. Store it somewhere safe; if you lose it,
        revoke it and create another.
      </p>
      <div className="secret">
        <code data-testid="issued-key">{apiKey}</code>
        <CopyButton text={apiKey} label="Copy key" />
      </div>
      <h3>Use it in an MCP client</h3>
      <dl className="kv">
        <dt>URL</dt>
        <dd>
          <code>{gatewayUrl}</code>
        </dd>
        <dt>Header</dt>
        <dd>
          <code>{snippet}</code>
        </dd>
      </dl>
      <div className="modal-actions">
        <Button variant="primary" onClick={onClose}>
          I have saved it
        </Button>
      </div>
    </Modal>
  );
}
