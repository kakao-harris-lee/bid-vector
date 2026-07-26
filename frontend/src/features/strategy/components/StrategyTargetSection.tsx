import { Controller, type Control } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { ChipInput } from "@/shared/components";
import type { StrategyFormValues } from "../schema";

export function StrategyTargetSection({ control }: { control: Control<StrategyFormValues> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>대상</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Controller
          control={control}
          name="focus_categories"
          render={({ field }) => (
            <ChipInput
              label="중점 카테고리"
              value={field.value}
              onChange={field.onChange}
              placeholder="용역, 물품 ..."
            />
          )}
        />
        <Controller
          control={control}
          name="focus_regions"
          render={({ field }) => (
            <ChipInput
              label="중점 지역"
              value={field.value}
              onChange={field.onChange}
              placeholder="서울, 경기 ..."
            />
          )}
        />
        <Controller
          control={control}
          name="exclude_regions"
          render={({ field }) => (
            <ChipInput
              label="제외 지역"
              value={field.value}
              onChange={field.onChange}
              placeholder="제외할 지역"
            />
          )}
        />
        <Controller
          control={control}
          name="required_keywords"
          render={({ field }) => (
            <ChipInput
              label="필수 키워드"
              value={field.value}
              onChange={field.onChange}
              placeholder="포함되어야 할 단어"
            />
          )}
        />
        <Controller
          control={control}
          name="exclude_keywords"
          render={({ field }) => (
            <ChipInput
              label="제외 키워드"
              value={field.value}
              onChange={field.onChange}
              placeholder="제외할 단어"
            />
          )}
        />
      </CardContent>
    </Card>
  );
}
