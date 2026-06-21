import { useCallback } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Cross-app navigation helper for the split user (`/dashboard`) and admin
 * (`/admin`) Vite bundles.
 *
 * Each surface ships as its own bundle, so a route that belongs to the *other*
 * app does not exist in the current bundle's router. Using react-router's
 * in-app `navigate("/dashboard/...")` from the standalone admin bundle would
 * fall through to the admin catch-all and bounce to `/admin/operations`.
 * Crossing the app boundary therefore requires a full-page navigation.
 *
 * In the combined test app (vitest mounts both route trees under base "/"),
 * both surfaces share one router, so an in-app `navigate(path)` is correct and
 * keeps existing `window.location.pathname` assertions working.
 *
 * `BASE_URL` is read at call time (not module load) so tests can flip the
 * standalone branch with `vi.stubEnv("BASE_URL", "/admin/")`.
 */
export function isStandaloneBundle(): boolean {
  const base = import.meta.env.BASE_URL;
  return base === "/admin/" || base === "/dashboard/";
}

/**
 * Returns a navigate function that crosses the user/admin app boundary safely:
 * full-page `window.location.assign` when running as a standalone bundle, and
 * in-app react-router `navigate` in the combined test app.
 *
 * Use this ONLY for links that cross the app boundary (e.g. an admin screen
 * linking into `/dashboard/...`). Same-surface navigation should keep using
 * react-router `useNavigate` directly.
 */
export function useCrossAppNavigate(): (path: string) => void {
  const navigate = useNavigate();
  return useCallback(
    (path: string) => {
      if (isStandaloneBundle()) {
        window.location.assign(path);
        return;
      }
      navigate(path);
    },
    [navigate]
  );
}
