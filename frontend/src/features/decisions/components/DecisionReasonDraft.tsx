export function DecisionReasonDraft({
  recordId,
  value,
  onChange
}: {
  recordId: number;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium text-[var(--color-muted)]">
        선택 사유 메모 (임시)
      </span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={`결정 ${recordId} 선택 사유 메모`}
        className="min-h-16 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 text-xs text-[var(--color-fg)]"
      />
      <span className="text-[11px] text-[var(--color-muted)]">
        현재는 저장되지 않는 검토 초안이며, 전환 버튼 활성화에만 사용됩니다.
      </span>
    </label>
  );
}
