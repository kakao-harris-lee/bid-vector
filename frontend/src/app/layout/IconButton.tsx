import type { ReactNode } from "react";
import { Button } from "@/shared/components/ui";

export function IconButton({
  label,
  onClick,
  disabled = false,
  children
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </Button>
  );
}
