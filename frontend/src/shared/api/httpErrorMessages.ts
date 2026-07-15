/**
 * HTTP 상태 코드 → 사용자용 한국어 오류 메시지.
 *
 * `client.ts`와 `projects.ts`에 동일한 매핑이 복붙돼 있던 것을 한 곳으로 모은
 * 단일 출처입니다. 개별 코드에 없는 5xx는 서버 오류로, 그 외는 일반 실패
 * 메시지로 폴백합니다.
 */
const HTTP_ERROR_MESSAGES = {
  401: "세션이 만료되었습니다.",
  403: "권한이 없습니다.",
  404: "요청한 자원을 찾을 수 없습니다."
} as const satisfies Record<number, string>;

export function httpErrorMessage(status: number): string {
  const known = HTTP_ERROR_MESSAGES[status as keyof typeof HTTP_ERROR_MESSAGES];
  if (known) return known;
  if (status >= 500) return "서버 오류가 발생했습니다.";
  return "요청을 처리하지 못했습니다.";
}
