export interface ReadOnlyContextNoticeProps {
  /** 현재 임퍼소네이션 중인 회사 라벨. null이면 "다른 회사". */
  operatorLabel: string | null;
  /** 화면별 테스트 훅(`profile-readonly-notice` 등). */
  testId: string;
}

/**
 * 다른 회사 컨텍스트(읽기 전용)임을 알리는 노티스.
 *
 * CompanyInfoEditor와 StrategyEditor가 `data-testid`만 다르게 두고
 * 동일하게 렌더하던 블록을 공유 컴포넌트로 추출한 것입니다.
 */
export function ReadOnlyContextNotice({
  operatorLabel,
  testId
}: ReadOnlyContextNoticeProps) {
  return (
    <div
      role="note"
      data-testid={testId}
      className="rounded-md border border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_88%)] px-3 py-2 text-xs"
    >
      현재 회사: {operatorLabel ?? "다른 회사"} · 편집은 본인 회사로 돌아가야 가능합니다.
    </div>
  );
}
