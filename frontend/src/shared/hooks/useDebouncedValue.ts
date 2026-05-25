import { useEffect, useState } from "react";

/**
 * Returns a value that lags `delayMs` behind the input. Useful for typing
 * fields where you don't want to fire a network request on every keystroke.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}
