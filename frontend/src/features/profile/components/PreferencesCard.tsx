import { Controller, type UseFormReturn } from "react-hook-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { ChipInput } from "@/shared/components";
import type { CompanyInfoFormValues } from "../schema";

export function PreferencesCard({ form }: { form: UseFormReturn<CompanyInfoFormValues> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>선호 · 제외 조건</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Controller
          control={form.control}
          name="focus_categories"
          render={({ field }) => (
            <ChipInput
              label="중점 카테고리"
              value={field.value}
              onChange={field.onChange}
              placeholder="공사, 용역 ..."
            />
          )}
        />
        <Controller
          control={form.control}
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
          control={form.control}
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
          control={form.control}
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
          control={form.control}
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
