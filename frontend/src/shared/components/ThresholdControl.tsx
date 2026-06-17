import { useId } from "react";
import { cn } from "@/shared/lib";

export interface ThresholdControlProps {
  label: string;
  value: number;
  onChange: (next: number) => void;
  /** Range bounds for the slider. */
  min: number;
  max: number;
  /** Step for both slider and number input. */
  step?: number;
  description?: string;
  error?: string | null;
  unit?: string;
  disabled?: boolean;
  /** Format the displayed value (e.g. percentage). */
  format?: (value: number) => string;
}

export function ThresholdControl({
  label,
  value,
  onChange,
  min,
  max,
  step = 0.01,
  description,
  error,
  unit,
  disabled = false,
  format
}: ThresholdControlProps) {
  const sliderId = useId();
  const numberId = useId();
  const display = format ? format(value) : value.toString();
  const errorId = error ? `${numberId}-error` : undefined;

  const setBounded = (raw: number) => {
    if (Number.isNaN(raw)) return;
    const clamped = Math.min(max, Math.max(min, raw));
    onChange(clamped);
  };

  return (
    <div className={cn("flex flex-col gap-1", disabled && "opacity-60")}>
      <div className="flex items-baseline justify-between">
        <label htmlFor={sliderId} className="text-xs font-medium text-[var(--color-muted)]">
          {label}
        </label>
        <span className="text-xs tabular-nums text-[var(--color-fg)]">
          {display}
          {unit ? <span className="ml-0.5 text-[var(--color-muted)]">{unit}</span> : null}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <input
          id={sliderId}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(event) => setBounded(Number(event.target.value))}
          className="flex-1 accent-[var(--color-primary)]"
          aria-describedby={errorId ?? (description ? `${sliderId}-desc` : undefined)}
        />
        <input
          id={numberId}
          type="number"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(event) => {
            const raw = event.target.value;
            if (raw === "") return;
            setBounded(Number(raw));
          }}
          onBlur={(event) => {
            const raw = event.target.value;
            if (raw === "") return;
            setBounded(Number(raw));
          }}
          className={cn(
            "h-8 w-20 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-right text-xs tabular-nums",
            error && "border-[var(--color-danger)]"
          )}
          aria-label={label}
          aria-invalid={Boolean(error)}
          aria-describedby={errorId}
        />
      </div>
      {description ? (
        <p id={`${sliderId}-desc`} className="text-[11px] text-[var(--color-muted)]">
          {description}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-[11px] text-[var(--color-danger)]" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
