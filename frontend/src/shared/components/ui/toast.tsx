import { useEffect, useState } from "react";
import { cn } from "@/shared/lib";

export type ToastTone = "info" | "success" | "warning" | "danger";

export interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
  durationMs: number;
}

type Listener = (toasts: ToastItem[]) => void;

const listeners = new Set<Listener>();
let toasts: ToastItem[] = [];
let nextId = 1;

function emit() {
  for (const listener of listeners) listener(toasts);
}

function remove(id: number) {
  toasts = toasts.filter((toast) => toast.id !== id);
  emit();
}

export interface ToastOptions {
  title: string;
  description?: string;
  tone?: ToastTone;
  durationMs?: number;
}

export function toast({ title, description, tone = "info", durationMs = 4000 }: ToastOptions): number {
  const id = nextId++;
  toasts = [...toasts, { id, title, description, tone, durationMs }];
  emit();
  if (durationMs > 0) {
    window.setTimeout(() => remove(id), durationMs);
  }
  return id;
}

export const toastApi = {
  info: (options: Omit<ToastOptions, "tone">) => toast({ ...options, tone: "info" }),
  success: (options: Omit<ToastOptions, "tone">) => toast({ ...options, tone: "success" }),
  warning: (options: Omit<ToastOptions, "tone">) => toast({ ...options, tone: "warning" }),
  danger: (options: Omit<ToastOptions, "tone">) => toast({ ...options, tone: "danger" }),
  dismiss: (id: number) => remove(id),
  clearAll: () => {
    toasts = [];
    emit();
  }
};

function useToasts(): ToastItem[] {
  const [items, setItems] = useState<ToastItem[]>(toasts);
  useEffect(() => {
    listeners.add(setItems);
    return () => {
      listeners.delete(setItems);
    };
  }, []);
  return items;
}

const toneStyles: Record<ToastTone, string> = {
  info: "border-[var(--color-info)] bg-[color-mix(in_oklch,var(--color-info),white_85%)] text-[color-mix(in_oklch,var(--color-info),black_25%)]",
  success: "border-[var(--color-success)] bg-[color-mix(in_oklch,var(--color-success),white_85%)] text-[color-mix(in_oklch,var(--color-success),black_25%)]",
  warning: "border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_85%)] text-[color-mix(in_oklch,var(--color-warn),black_30%)]",
  danger: "border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] text-[color-mix(in_oklch,var(--color-danger),black_25%)]"
};

export function Toaster() {
  const items = useToasts();
  if (!items.length) return null;
  return (
    <div
      className="pointer-events-none fixed inset-x-0 top-4 z-50 mx-auto flex w-full max-w-md flex-col items-stretch gap-2 px-4"
      role="region"
      aria-live="polite"
      aria-label="알림"
    >
      {items.map((item) => (
        <div
          key={item.id}
          className={cn(
            "pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-sm",
            toneStyles[item.tone]
          )}
          role={item.tone === "danger" || item.tone === "warning" ? "alert" : "status"}
        >
          <div className="flex items-start justify-between gap-2">
            <strong className="font-semibold leading-tight">{item.title}</strong>
            <button
              type="button"
              className="text-xs opacity-70 hover:opacity-100"
              onClick={() => remove(item.id)}
              aria-label="알림 닫기"
            >
              ✕
            </button>
          </div>
          {item.description ? (
            <p className="mt-1 text-xs leading-snug opacity-90">{item.description}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}
