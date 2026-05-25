import { forwardRef, type HTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/shared/lib/cn";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium leading-none",
  {
    variants: {
      tone: {
        info: "bg-[color-mix(in_oklch,var(--color-info),white_70%)] text-[color-mix(in_oklch,var(--color-info),black_30%)]",
        healthy: "bg-[color-mix(in_oklch,var(--color-success),white_75%)] text-[color-mix(in_oklch,var(--color-success),black_30%)]",
        watch: "bg-[color-mix(in_oklch,var(--color-warn),white_70%)] text-[color-mix(in_oklch,var(--color-warn),black_40%)]",
        critical: "bg-[color-mix(in_oklch,var(--color-danger),white_70%)] text-[color-mix(in_oklch,var(--color-danger),black_30%)]",
        muted: "bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)]"
      }
    },
    defaultVariants: { tone: "info" }
  }
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, tone, ...rest }, ref) => (
    <span ref={ref} className={cn(badgeVariants({ tone }), className)} {...rest} />
  )
);
Badge.displayName = "Badge";
