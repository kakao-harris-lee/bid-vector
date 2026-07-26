import { Button } from "@/shared/components/ui";

interface EditorHeaderProps {
  readOnly: boolean;
  isWizard: boolean;
  step: number;
  stepCount: number;
  activeStepTitle: string;
  onToggle: () => void;
}

export function EditorHeader({
  readOnly,
  isWizard,
  step,
  stepCount,
  activeStepTitle,
  onToggle
}: EditorHeaderProps) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-lg font-semibold text-[var(--color-fg)]">업체 정보</h2>
      <div className="flex items-center gap-3">
        <span className="text-xs text-[var(--color-muted)]">
          {readOnly
            ? "다른 회사 컨텍스트는 읽기 전용입니다."
            : isWizard
              ? `단계 ${step + 1} / ${stepCount} — ${activeStepTitle}`
              : "정확한 5축 입력이 추천 품질을 좌우합니다."}
        </span>
        {readOnly ? null : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onToggle}
            aria-pressed={isWizard}
          >
            {isWizard ? "전체 보기" : "단계별 가이드"}
          </Button>
        )}
      </div>
    </header>
  );
}

interface EditorActionsProps {
  isWizard: boolean;
  step: number;
  saving: boolean;
  isLastStep: boolean;
  onPrev: () => void;
  onNext: () => void;
  onReset: () => void;
  resetDisabled: boolean;
}

export function EditorActions({
  isWizard,
  step,
  saving,
  isLastStep,
  onPrev,
  onNext,
  onReset,
  resetDisabled
}: EditorActionsProps) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {isWizard ? (
        <>
          <Button
            type="button"
            variant="outline"
            onClick={onPrev}
            disabled={step === 0 || saving}
          >
            이전
          </Button>
          {isLastStep ? (
            <Button type="submit" disabled={saving}>
              {saving ? "저장 중" : "저장하고 마치기"}
            </Button>
          ) : (
            <Button type="button" onClick={onNext} disabled={saving}>
              다음
            </Button>
          )}
        </>
      ) : (
        <>
          <Button
            type="button"
            variant="outline"
            onClick={onReset}
            disabled={resetDisabled}
          >
            되돌리기
          </Button>
          <Button type="submit" disabled={saving}>
            {saving ? "저장 중" : "저장"}
          </Button>
        </>
      )}
    </div>
  );
}
