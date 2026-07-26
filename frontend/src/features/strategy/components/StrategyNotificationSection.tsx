import { Controller, type Control } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type { StrategyFormValues } from "../schema";

export function StrategyNotificationSection({ control }: { control: Control<StrategyFormValues> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>알림</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Controller
          control={control}
          name="notify_only_high_priority"
          render={({ field }) => (
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={field.value}
                onChange={(event) => field.onChange(event.target.checked)}
                className="h-4 w-4 accent-[var(--color-primary)]"
              />
              <span>높은 우선순위 후보에만 알림 보내기</span>
            </label>
          )}
        />
      </CardContent>
    </Card>
  );
}
