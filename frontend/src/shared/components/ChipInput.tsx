import { type KeyboardEvent, useId, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/shared/lib/cn";

export interface ChipInputProps {
  value: string[];
  onChange: (next: string[]) => void;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  /** Treat these characters as separators in addition to Enter. Default: Enter only. */
  separators?: string[];
  ariaDescribedBy?: string;
  className?: string;
}

export function ChipInput({
  value,
  onChange,
  label,
  placeholder,
  disabled = false,
  separators = [],
  ariaDescribedBy,
  className
}: ChipInputProps) {
  const inputId = useId();
  const [draft, setDraft] = useState("");

  const commit = (raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    if (value.includes(trimmed)) {
      setDraft("");
      return;
    }
    onChange([...value, trimmed]);
    setDraft("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit(draft);
      return;
    }
    if (event.key === "Backspace" && !draft && value.length) {
      event.preventDefault();
      onChange(value.slice(0, -1));
      return;
    }
    if (separators.includes(event.key)) {
      event.preventDefault();
      commit(draft);
    }
  };

  const removeChip = (chip: string) => {
    onChange(value.filter((entry) => entry !== chip));
  };

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label htmlFor={inputId} className="text-xs font-medium text-[var(--color-muted)]">
        {label}
      </label>
      <div
        className={cn(
          "flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1.5 text-sm",
          disabled && "opacity-60"
        )}
      >
        {value.map((chip) => (
          <span
            key={chip}
            className="inline-flex items-center gap-1 rounded-full bg-[var(--color-secondary)] px-2 py-0.5 text-xs text-[var(--color-secondary-foreground)]"
          >
            <span>{chip}</span>
            <button
              type="button"
              className="grid h-4 w-4 place-items-center rounded-full hover:bg-[color-mix(in_oklch,var(--color-secondary),black_10%)] disabled:cursor-not-allowed"
              onClick={() => removeChip(chip)}
              aria-label={`${label}에서 ${chip} 제거`}
              disabled={disabled}
            >
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          id={inputId}
          className="min-w-[8ch] flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-muted)]"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => commit(draft)}
          placeholder={value.length ? "" : placeholder}
          disabled={disabled}
          aria-describedby={ariaDescribedBy}
        />
      </div>
    </div>
  );
}
