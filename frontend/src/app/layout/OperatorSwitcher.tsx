import { useEffect, useId, useRef, useState } from "react";
import { Building2, Check, ChevronDown } from "lucide-react";
import type { ActiveOperator } from "@/app/operatorContext";

interface OperatorSwitcherProps {
  activeOperator: ActiveOperator;
}

/**
 * Header company-switcher dropdown.
 *
 * - Hidden for non-privileged users (single-self pick, no UX value).
 * - Renders canonical operator first, then `synthetic-*` rows in a separate
 *   group. The "기본 (본인)" option resets the override to `null`.
 * - Selecting an entry mutates active-operator state via
 *   `setActiveOperatorId`, which invalidates the dashboard / operations /
 *   decisions queries so every screen re-fetches under the new scope.
 */
export function OperatorSwitcher({ activeOperator }: OperatorSwitcherProps) {
  const {
    activeOperatorId,
    currentOperatorId,
    isPrivileged,
    operators,
    isLoading,
    setActiveOperatorId
  } = activeOperator;

  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const listboxId = useId();

  useEffect(() => {
    if (!open) return;
    const onDocClick = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (triggerRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!isPrivileged) {
    // Single-user operator: dropdown would be a no-op. Hide entirely so the
    // header stays compact and avoids confusing affordances.
    return null;
  }

  const canonicalRows = operators.filter((row) => row.is_canonical);
  const syntheticRows = operators.filter((row) => row.is_synthetic);
  const otherRows = operators.filter((row) => !row.is_canonical && !row.is_synthetic);

  const selectedRow =
    operators.find(
      (row) => row.operator_id === (activeOperatorId ?? currentOperatorId ?? -1)
    ) ?? null;

  const triggerLabel = selectedRow
    ? selectedRow.company || selectedRow.full_name || selectedRow.username
    : isLoading
      ? "회사 불러오는 중…"
      : "회사 선택";

  const handleSelect = (operatorId: number | null) => {
    setActiveOperatorId(operatorId);
    setOpen(false);
    triggerRef.current?.focus();
  };

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1 text-xs hover:bg-[var(--color-bg)]"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label="회사 전환"
        onClick={() => setOpen((value) => !value)}
        disabled={isLoading || operators.length === 0}
      >
        <Building2 size={14} aria-hidden />
        <span className="max-w-[10rem] truncate">{triggerLabel}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open ? (
        <div
          ref={popoverRef}
          className="absolute right-0 z-20 mt-1 w-72 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-1 shadow-lg"
        >
          <ul
            id={listboxId}
            role="listbox"
            aria-label="회사 선택"
            className="flex max-h-80 flex-col gap-0.5 overflow-y-auto"
          >
            <OperatorOption
              label="기본 (본인)"
              hint="토큰 소유자 기본 데이터"
              selected={activeOperatorId === null}
              onSelect={() => handleSelect(null)}
            />
            {canonicalRows.length > 0 ? (
              <OperatorGroup label="운영자">
                {canonicalRows.map((row) => (
                  <OperatorOption
                    key={row.operator_id}
                    label={row.company || row.full_name || row.username}
                    hint={`${row.username}${row.business_type ? ` · ${row.business_type}` : ""}`}
                    selected={
                      activeOperatorId === row.operator_id ||
                      (activeOperatorId === null && row.operator_id === currentOperatorId)
                    }
                    onSelect={() => handleSelect(row.operator_id)}
                  />
                ))}
              </OperatorGroup>
            ) : null}
            {syntheticRows.length > 0 ? (
              <OperatorGroup label="가상 운영자 (synthetic)">
                {syntheticRows.map((row) => (
                  <OperatorOption
                    key={row.operator_id}
                    label={row.company || row.full_name || row.username}
                    hint={`${row.username}${row.business_type ? ` · ${row.business_type}` : ""}`}
                    selected={activeOperatorId === row.operator_id}
                    onSelect={() => handleSelect(row.operator_id)}
                  />
                ))}
              </OperatorGroup>
            ) : null}
            {otherRows.length > 0 ? (
              <OperatorGroup label="기타">
                {otherRows.map((row) => (
                  <OperatorOption
                    key={row.operator_id}
                    label={row.company || row.full_name || row.username}
                    hint={row.username}
                    selected={activeOperatorId === row.operator_id}
                    onSelect={() => handleSelect(row.operator_id)}
                  />
                ))}
              </OperatorGroup>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function OperatorGroup({
  label,
  children
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <li
        aria-hidden
        className="mt-1 px-2 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-muted)]"
      >
        {label}
      </li>
      {children}
    </>
  );
}

function OperatorOption({
  label,
  hint,
  selected,
  onSelect
}: {
  label: string;
  hint?: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li
      role="option"
      aria-selected={selected}
      tabIndex={-1}
      className={`flex cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1.5 text-xs ${
        selected
          ? "bg-[color-mix(in_oklch,var(--color-primary),white_85%)] text-[var(--color-fg)]"
          : "hover:bg-[var(--color-bg)] text-[var(--color-fg)]"
      }`}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-medium">{label}</span>
        {hint ? (
          <span className="truncate text-[10px] text-[var(--color-muted)]">{hint}</span>
        ) : null}
      </span>
      {selected ? <Check size={14} aria-hidden /> : null}
    </li>
  );
}
