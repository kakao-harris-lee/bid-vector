---
description: 새 프론트엔드 화면 스캐폴드 (features/<area>/<Screen>.tsx + 테스트 + 라우트 등록)
argument-hint: "<feature-area> <ScreenName>"
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# /screen

새 프론트엔드 화면을 `frontend/src/features/<area>/`에 스캐폴드하고 라우터에 등록한다.

## 사용

```
/screen strategy StrategyEditor
/screen synthetic-backtest ComparisonTable
```

인자:
1. `<feature-area>` — `features/` 하위 폴더명 (kebab-case). 예: `strategy`, `decisions`, `synthetic-backtest`
2. `<ScreenName>` — 컴포넌트 이름 (PascalCase). 예: `StrategyEditor`, `ComparisonTable`

## 생성 파일

```
frontend/src/features/<area>/
├── <ScreenName>.tsx           # 컴포넌트 본체 (Tailwind + shadcn)
├── <ScreenName>.test.tsx      # vitest + RTL smoke 1개
├── use<ScreenName>.ts         # react-query 훅 placeholder (있어야 할 때만)
└── index.ts                   # re-export
```

## 작업 절차

1. `frontend/src/features/<area>/` 가 없으면 생성한다.
2. `<ScreenName>.tsx` 본체에 다음 골격을 둔다:
   - default export 함수형 컴포넌트
   - `useQuery`/`useMutation` placeholder 주석
   - `Card`/`Button` 등 shadcn 컴포넌트로 최소 레이아웃
3. `<ScreenName>.test.tsx`:
   - `renderWithProviders` helper를 통해 QueryClientProvider + MemoryRouter 래핑
   - 컴포넌트가 마운트되고 핵심 헤딩이 보인다는 smoke 1개
4. `index.ts`에서 `<ScreenName>`을 named export로 재공개한다.
5. `frontend/src/app/router.tsx`에 라우트를 등록한다:
   - 경로: `/dashboard/<area>` 또는 `/dashboard/<area>/...` (사용자에게 확인)
   - lazy import 권장
6. `frontend/src/shared/api/queryKeys.ts`에 새 query key namespace를 한 줄 추가한다 (필요한 경우).
7. 변경 후 `npm --prefix frontend run test --silent`를 실행해 새 smoke가 통과하는지 확인한다.

## 금지

- App.tsx에 직접 화면 코드 추가
- 신규 화면을 `frontend/src/styles.css`로 스타일링
- shadcn 원본(`components/ui/`)을 화면 폴더 안에 복제
- 백엔드 파일 수정

## 보고

- 생성된 파일 목록
- 등록된 라우트 경로
- 사용자가 다음에 할 일 1줄 (예: "`use<ScreenName>` 훅에 실제 API 연결 필요")
