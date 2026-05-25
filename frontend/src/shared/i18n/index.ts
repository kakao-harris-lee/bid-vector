import ko from "./ko.json";

type Locale = "ko";
const DEFAULT_LOCALE: Locale = "ko";

const bundles: Record<Locale, typeof ko> = { ko };

/**
 * Lookup a translated string by dot-path (e.g. `"strategy.title"`).
 *
 * **bid-vector는 한국형(나라장터/KONEPS) 서비스이므로 다중 로케일을 지원하지 않습니다.**
 * 이 모듈은 단일 ko 번들만 유지하고, 외부 노출 문구를 `ko.json`에 모아 일관성을
 * 관리하기 위한 헬퍼입니다. `Locale` 유니온은 `"ko"`로 고정하고 다른 로케일을
 * 추가하지 않습니다.
 */
export function t(path: string, locale: Locale = DEFAULT_LOCALE): string {
  const segments = path.split(".");
  let cursor: unknown = bundles[locale];
  for (const seg of segments) {
    if (cursor && typeof cursor === "object" && seg in (cursor as Record<string, unknown>)) {
      cursor = (cursor as Record<string, unknown>)[seg];
    } else {
      return path;
    }
  }
  return typeof cursor === "string" ? cursor : path;
}

export type { Locale };
