import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type { CompanyInfoFormValues } from "../schema";

export function PilotReadinessCard({ values }: { values: CompanyInfoFormValues }) {
  const licenseCount = values.license_codes?.length ?? 0;
  const regionCount = values.region_codes?.length ?? 0;
  const preferenceCount =
    (values.focus_categories?.length ?? 0) +
    (values.focus_regions?.length ?? 0) +
    (values.required_keywords?.length ?? 0);
  const exclusionCount =
    (values.exclude_regions?.length ?? 0) + (values.exclude_keywords?.length ?? 0);
  const items = [
    {
      label: "보유 면허",
      ready: licenseCount > 0,
      detail:
        licenseCount > 0
          ? `${licenseCount.toLocaleString("ko-KR")}개 면허가 후보 필터에 반영됩니다.`
          : "공고 요구 면허와 맞출 기준이 필요합니다."
    },
    {
      label: "수행 지역",
      ready: regionCount > 0,
      detail:
        regionCount > 0
          ? `${regionCount.toLocaleString("ko-KR")}개 지역이 매칭 범위로 쓰입니다.`
          : "파일럿 대상 권역을 먼저 좁혀야 합니다."
    },
    {
      label: "시공능력평가액",
      ready: values.construction_capacity_amount > 0,
      detail:
        values.construction_capacity_amount > 0
          ? `${formatWon(values.construction_capacity_amount)} 입력됨`
          : "0이면 미입력으로 처리되어 금액 검토 근거가 약해집니다."
    },
    {
      label: "도급한도",
      ready: values.awarded_contract_limit > 0,
      detail:
        values.awarded_contract_limit > 0
          ? `${formatWon(values.awarded_contract_limit)} 입력됨`
          : "도급한도 확인 전에는 상한 초과 후보를 별도 검토해야 합니다."
    },
    {
      label: "관심 조건",
      ready: preferenceCount > 0,
      detail:
        preferenceCount > 0
          ? `${preferenceCount.toLocaleString("ko-KR")}개 조건으로 후보를 좁힙니다.`
          : "중점 카테고리·지역 또는 필수 키워드를 입력하세요."
    },
    {
      label: "제외 조건",
      ready: exclusionCount > 0,
      detail:
        exclusionCount > 0
          ? `${exclusionCount.toLocaleString("ko-KR")}개 제외 조건이 검토 비용을 줄입니다.`
          : "원치 않는 지역·키워드가 있으면 여기서 먼저 제거하세요."
    }
  ];
  const readyCount = items.filter((item) => item.ready).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>파일럿 준비도</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[var(--color-muted)]">실증 후보 매칭 입력</span>
          <Badge tone={readyCount >= 5 ? "healthy" : readyCount >= 3 ? "watch" : "muted"}>
            {readyCount} / {items.length} 확인
          </Badge>
        </div>
        <ul className="flex flex-col gap-2" aria-label="파일럿 준비도 체크리스트">
          {items.map((item) => (
            <li key={item.label} className="flex flex-col gap-0.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-[var(--color-fg)]">{item.label}</span>
                <Badge tone={item.ready ? "healthy" : "watch"}>
                  {item.ready ? "확인됨" : "보강 필요"}
                </Badge>
              </div>
              <p className="text-[var(--color-muted)]">{item.detail}</p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function formatWon(value: number): string {
  return `${value.toLocaleString("ko-KR")}원`;
}
