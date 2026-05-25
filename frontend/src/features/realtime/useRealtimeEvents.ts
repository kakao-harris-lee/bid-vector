import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toastApi } from "@/shared/components/ui";
import type { RealtimeEvent } from "@/shared/types/notifications";
import type { AuthSession } from "@/app/layout/AuthGate";

interface InternalRealtimeEvent extends RealtimeEvent {
  event_id?: string;
}

interface WelcomeEnvelope {
  type: "welcome";
  replay?: {
    delivered_event_count?: number;
    after_event_id_found?: boolean;
  };
}

/**
 * Subscribe to the backend realtime stream and translate events to cache
 * invalidations + toasts. Tracks the last seen `event_id` so that on
 * reconnect the connection requests `?replay=true&after_event_id=...` —
 * the backend `app/services/realtime.py::_build_replay_plan` then ships any
 * retained events missed during the disconnect window.
 */
export function useRealtimeEvents(session: AuthSession | null): void {
  const queryClient = useQueryClient();
  const reconnectTimer = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  // lastEventIdRef survives reconnects within the same hook lifecycle so the
  // resume cursor doesn't reset just because the socket bounced.
  const lastEventIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!session?.token) return;
    if (typeof WebSocket === "undefined") return; // jsdom / tests without WS

    let cancelled = false;

    const buildUrl = (): string => {
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const params = new URLSearchParams();
      params.set("token", session.token);
      if (lastEventIdRef.current) {
        params.set("replay", "true");
        params.set("after_event_id", lastEventIdRef.current);
      }
      return `${proto}//${window.location.host}/api/v1/realtime/events?${params.toString()}`;
    };

    const connect = () => {
      if (cancelled) return;
      let socket: WebSocket;
      try {
        socket = new WebSocket(buildUrl());
      } catch {
        return;
      }
      wsRef.current = socket;

      socket.addEventListener("message", (event) => {
        let payload: InternalRealtimeEvent | WelcomeEnvelope | null = null;
        try {
          payload = JSON.parse(event.data) as InternalRealtimeEvent | WelcomeEnvelope;
        } catch {
          return;
        }
        if (!payload) return;

        if (isWelcome(payload)) {
          const replayed = payload.replay?.delivered_event_count ?? 0;
          if (replayed > 0) {
            toastApi.info({
              title: "재연결 — 누락 이벤트 복원",
              description: `${replayed}건`
            });
          }
          return;
        }

        if (!payload.type) return;
        if (payload.event_id) lastEventIdRef.current = payload.event_id;
        applyEvent(queryClient, payload);
      });

      socket.addEventListener("close", () => {
        if (cancelled) return;
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

function isWelcome(payload: InternalRealtimeEvent | WelcomeEnvelope): payload is WelcomeEnvelope {
  return (payload as WelcomeEnvelope).type === "welcome";
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
