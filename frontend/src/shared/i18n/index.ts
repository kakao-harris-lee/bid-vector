import ko from "./ko.json";

type Locale = "ko";
const DEFAULT_LOCALE: Locale = "ko";

const bundles: Record<Locale, typeof ko> = { ko };

/**
 * Lookup a translated string by dot-path (e.g. `"strategy.title"`).
 * Phase 7 bundle is single-locale Korean — the API exists so future locales
 * can be added without touching call sites.
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
