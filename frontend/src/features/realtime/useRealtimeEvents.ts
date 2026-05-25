import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toastApi } from "@/shared/components/ui";
import type { RealtimeEvent } from "@/shared/types/notifications";
import type { AuthSession } from "@/app/layout/AuthGate";

/**
 * Subscribe to the backend realtime stream and translate events to cache
 * invalidations + toasts. Kept minimal for Phase 7 — full replay/after_event_id
 * negotiation will come in a follow-up.
 */
export function useRealtimeEvents(session: AuthSession | null): void {
  const queryClient = useQueryClient();
  const reconnectTimer = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!session?.token) return;
    if (typeof WebSocket === "undefined") return; // jsdom / tests without WS

    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${window.location.host}/api/v1/realtime/events?token=${encodeURIComponent(session.token)}`;
      let socket: WebSocket;
      try {
        socket = new WebSocket(url);
      } catch {
        // Browser may refuse the connection (e.g., insecure origin). Skip.
        return;
      }
      wsRef.current = socket;

      socket.addEventListener("message", (event) => {
        let payload: RealtimeEvent | null = null;
        try {
          payload = JSON.parse(event.data) as RealtimeEvent;
        } catch {
          return;
        }
        if (!payload?.type) return;
        applyEvent(queryClient, payload);
      });

      socket.addEventListener("close", () => {
        if (cancelled) return;
        // Exponential backoff would be nicer; for Phase 7 use a fixed 5s.
        reconnectTimer.current = window.setTimeout(connect, 5_000);
      });

      socket.addEventListener("error", () => {
        socket.close();
      });
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer.current) {
        window.clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      const ws = wsRef.current;
      if (ws && ws.readyState <= WebSocket.OPEN) {
        ws.close();
      }
      wsRef.current = null;
    };
  }, [session?.token, queryClient]);
}

function applyEvent(queryClient: ReturnType<typeof useQueryClient>, event: RealtimeEvent) {
  if (event.type.startsWith("bid_decision.")) {
    void queryClient.invalidateQueries({ queryKey: ["decisions"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    toastApi.info({ title: "결정 업데이트", description: event.type });
    return;
  }
  if (event.type.startsWith("crawl.")) {
    void queryClient.invalidateQueries({ queryKey: ["operations"] });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    return;
  }
  if (event.type.startsWith("strategy.monitor.")) {
    void queryClient.invalidateQueries({ queryKey: ["strategy"] });
    return;
  }
  if (event.type === "bid.submitted") {
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    toastApi.success({ title: "투찰 제출 완료", description: "" });
    return;
  }
  if (event.type.startsWith("notification.")) {
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  }
}
