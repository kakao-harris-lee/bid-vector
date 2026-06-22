import type { ReactNode } from "react";
import { renderHook } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isStandaloneBundle, useCrossAppNavigate } from "./crossAppNav";

const mockNavigate = vi.fn();

// react-router's navigate is the in-app branch; spy on it so we can assert the
// helper picks in-app vs full-page based on the bundle base.
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

function wrapper({ children }: { children: ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>;
}

let originalLocation: Location;
let assignMock: ReturnType<typeof vi.fn>;

function installLocationMock() {
  assignMock = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign: assignMock }
  });
}

beforeEach(() => {
  originalLocation = window.location;
  mockNavigate.mockReset();
  installLocationMock();
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation
  });
  vi.unstubAllEnvs();
});

describe("isStandaloneBundle", () => {
  it("combined test app (base '/')에서는 standalone이 아니다", () => {
    vi.stubEnv("BASE_URL", "/");
    expect(isStandaloneBundle()).toBe(false);
  });

  it("standalone admin 번들(base '/admin/')은 standalone이다", () => {
    vi.stubEnv("BASE_URL", "/admin/");
    expect(isStandaloneBundle()).toBe(true);
  });

  it("standalone user 번들(base '/dashboard/')도 standalone이다", () => {
    vi.stubEnv("BASE_URL", "/dashboard/");
    expect(isStandaloneBundle()).toBe(true);
  });
});

describe("useCrossAppNavigate", () => {
  it("combined test app에서는 in-app navigate를 호출하고 full-page 이동을 하지 않는다", () => {
    vi.stubEnv("BASE_URL", "/");
    const { result } = renderHook(() => useCrossAppNavigate(), { wrapper });

    result.current("/dashboard/projects/42");

    expect(mockNavigate).toHaveBeenCalledWith("/dashboard/projects/42");
    expect(assignMock).not.toHaveBeenCalled();
  });

  it("standalone 번들에서는 full-page window.location.assign으로 앱 경계를 넘는다", () => {
    vi.stubEnv("BASE_URL", "/admin/");
    const { result } = renderHook(() => useCrossAppNavigate(), { wrapper });

    result.current("/dashboard/projects/42");

    expect(assignMock).toHaveBeenCalledWith("/dashboard/projects/42");
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
