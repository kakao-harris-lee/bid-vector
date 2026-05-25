import type { CSSProperties } from "react";
import { ClipboardCheck, RefreshCw } from "lucide-react";

export function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      <span>{count}</span>
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="loading-state">
      <RefreshCw size={18} />
      <span>불러오는 중</span>
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <ClipboardCheck size={20} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function MiniBar({ value, tone = "teal" }: { value: number; tone?: "teal" | "amber" }) {
  const safeValue = Math.max(0, Math.min(1, value || 0));
  return (
    <span className={`mini-bar ${tone}`} aria-hidden="true">
      <i style={{ width: `${safeValue * 100}%` }} />
    </span>
  );
}

export function MiniDonut({ value }: { value: number }) {
  const safeValue = Math.max(0, Math.min(1, value || 0));
  return (
    <span
      className="mini-donut"
      style={{ "--value": `${safeValue * 100}%` } as CSSProperties}
      aria-hidden="true"
    />
  );
}
