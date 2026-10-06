/**
 * Three small presentation pieces shared by every page.
 *
 * `EmptyState` exists because the honest version of "this feature is not built yet" is a
 * sentence that says so. A placeholder row with invented data would be worse than nothing:
 * the whole point of this UI is that what it shows is real.
 */

import { ReactNode } from "react";

export function Panel({
  title,
  hint,
  children,
  variant,
}: {
  title: string;
  hint?: ReactNode;
  children: ReactNode;
  variant?: "error";
}) {
  return (
    <section className={variant ? `panel ${variant}` : "panel"}>
      <header>
        <h2>{title}</h2>
        {hint ? <span className="hint">{hint}</span> : null}
      </header>
      <div className="body">{children}</div>
    </section>
  );
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export function KeyValues({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="kv">
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Loading({ label = "loading" }: { label?: string }) {
  return <p className="muted">…{label}</p>;
}

export function Failure({ label, error }: { label: string; error: unknown }) {
  return (
    <Panel title={`${label} unavailable`} variant="error">
      <p className="muted">
        {error instanceof Error ? error.message : String(error)}
      </p>
      <p className="faint">
        The daemon is the system of record; when it cannot be reached this view says so instead
        of showing stale state as current.
      </p>
    </Panel>
  );
}
