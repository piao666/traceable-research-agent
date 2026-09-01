import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
  variant?: ButtonVariant;
}

/** Figma Button — component set 22:3. */
export function Button({ children, loading = false, variant = "primary", disabled, ...props }: ButtonProps) {
  return (
    <button
      className={`button button-${variant}`}
      disabled={disabled || loading}
      aria-busy={loading}
      data-figma-node="22:3"
      {...props}
    >
      {loading && <span className="button-spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}

export type StatusTone = "plan" | "running" | "success" | "warning" | "danger" | "neutral";

/** Figma StatusChip — component set 23:15. */
export function StatusChip({ children, tone = "neutral" }: { children: ReactNode; tone?: StatusTone }) {
  return <span className={`status-chip status-${tone}`} data-figma-node="23:15">{children}</span>;
}

/** Figma MetricCard — component 24:3. */
export function MetricCard({ label, value, note }: { label: string; value: string | number; note: string }) {
  return (
    <article className="metric-card" data-figma-node="24:3">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-note">{note}</div>
    </article>
  );
}

/** Figma Panel — component 24:8. */
export function Panel({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`.trim()} data-figma-node="24:8">
      {title && <h2 className="panel-title">{title}</h2>}
      <div className="panel-body">{children}</div>
    </section>
  );
}

/** Figma TableRow — component 26:3. */
export function TableRow({ primary, status, meta, time, action }: { primary: ReactNode; status: ReactNode; meta: ReactNode; time: ReactNode; action?: ReactNode }) {
  return (
    <div className="table-row" role="row" data-figma-node="26:3">
      <div className="table-primary" role="cell">{primary}</div>
      <div role="cell">{status}</div>
      <div role="cell">{meta}</div>
      <div role="cell">{time}{action && <> · <span className="table-row-action">{action}</span></>}</div>
    </div>
  );
}

/** Figma Tab — component set 25:13. */
export function Tab({ selected, children, onClick }: { selected: boolean; children: ReactNode; onClick: () => void }) {
  return <button type="button" role="tab" aria-selected={selected} className="tab" onClick={onClick} data-figma-node="25:13">{children}</button>;
}

/** Figma OptionCard — component set 44:32. */
export function OptionCard({ title, description, selected, onClick }: { title: string; description: string; selected: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`option-card${selected ? " selected" : ""}`} aria-pressed={selected} onClick={onClick} data-figma-node="44:32">
      <span className="option-title">{title}</span>
      <span className="option-description">{description}</span>
    </button>
  );
}

/** Figma TimelineRow — component set 26:29. */
export function TimelineRow({ index, title, meta, risk }: { index: number; title: string; meta: string; risk?: string }) {
  return (
    <div className="timeline-row" data-figma-node="26:29">
      <div className="timeline-index">{String(index).padStart(2, "0")}</div>
      <div><div className="timeline-title">{title}</div><div className="timeline-meta">{meta}</div></div>
      {risk && <StatusChip tone={risk === "high" ? "danger" : risk === "medium" ? "warning" : "neutral"}>{risk === "high" ? "高风险" : risk === "medium" ? "中风险" : "低风险"}</StatusChip>}
    </div>
  );
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle: string; action?: ReactNode }) {
  return (
    <header className="page-header">
      <div><h1 className="page-title">{title}</h1><p className="page-subtitle">{subtitle}</p></div>
      {action}
    </header>
  );
}
