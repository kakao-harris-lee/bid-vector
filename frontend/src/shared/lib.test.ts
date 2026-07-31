import { describe, expect, it } from "vitest";
import { formatDateTime, formatRelativeTime } from "./lib";

/**
 * 스냅샷 신선도 배지("N분 전 기준")가 이 버킷 경계에 의존한다. `now` 를 주입해
 * 벽시계와 무관하게 고정한다(§4.7-3).
 */
describe("formatRelativeTime", () => {
  const now = new Date("2026-07-30T12:00:00Z");

  it.each([
    ["2026-07-30T11:59:59Z", "방금"],
    ["2026-07-30T11:59:01Z", "방금"],
    ["2026-07-30T11:59:00Z", "1분 전"],
    ["2026-07-30T11:57:00Z", "3분 전"],
    ["2026-07-30T11:01:00Z", "59분 전"],
    ["2026-07-30T11:00:00Z", "1시간 전"],
    ["2026-07-29T13:00:00Z", "23시간 전"]
  ])("%s → %s", (value, expected) => {
    expect(formatRelativeTime(value, now)).toBe(expected);
  });

  it("24시간을 넘기면 KST 절대 시각으로 폴백한다", () => {
    const value = "2026-07-28T12:00:00Z";
    expect(formatRelativeTime(value, now)).toBe(formatDateTime(value));
  });

  it("미래 시각(클럭 스큐)은 '-분 전'이 아니라 '방금'으로 표기한다", () => {
    expect(formatRelativeTime("2026-07-30T12:05:00Z", now)).toBe("방금");
  });

  it("값이 없거나 파싱 불가면 '-'", () => {
    expect(formatRelativeTime(null, now)).toBe("-");
    expect(formatRelativeTime(undefined, now)).toBe("-");
    expect(formatRelativeTime("not-a-date", now)).toBe("-");
  });
});
