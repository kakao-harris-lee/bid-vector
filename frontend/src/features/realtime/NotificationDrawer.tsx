import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, X } from "lucide-react";
import type { AuthSession } from "@/app/layout/AuthGate";
import { fetchNotifications, markNotificationRead } from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatDateTime } from "@/shared/lib";

const NOTIFICATIONS_KEY = ["notifications", "list"] as const;

export function NotificationDrawer({
  open,
  onClose,
  session
}: {
  open: boolean;
  onClose: () => void;
  session: AuthSession | null;
}) {
  const queryClient = useQueryClient();

  const notifications = useQuery({
    queryKey: NOTIFICATIONS_KEY,
    queryFn: () => fetchNotifications({ limit: 20 }, session?.token),
    enabled: Boolean(session?.token) && open
  });

  const markRead = useMutation({
    mutationFn: (id: number) => markNotificationRead(id, session?.token),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err) =>
      toastApi.danger({
        title: "읽음 처리 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="알림 서랍"
      className="fixed inset-0 z-40 flex justify-end bg-black/30"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside className="flex h-full w-full max-w-sm flex-col gap-3 bg-[var(--color-bg)] p-4 shadow-lg">
        <header className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-[var(--color-fg)]">
            <Bell size={16} />
            알림
          </h2>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label="닫기">
            <X size={18} />
          </Button>
        </header>

        {notifications.isPending && !notifications.data ? (
          <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {notifications.error ? (
          <p
            role="alert"
            className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-2 py-1 text-xs text-[var(--color-danger)]"
          >
            {notifications.error.message ?? "알림을 불러오지 못했습니다."}
          </p>
        ) : null}

        {notifications.data && notifications.data.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">새 알림이 없습니다.</p>
        ) : null}

        <ul className="flex flex-col gap-2 overflow-y-auto" aria-label="알림 리스트">
          {notifications.data?.map((item) => (
            <li key={item.id}>
              <Card className={item.is_read ? "opacity-70" : ""}>
                <CardHeader className="flex-row items-center justify-between">
                  <CardTitle className="text-sm">{item.title}</CardTitle>
                  <Badge tone={item.is_read ? "muted" : "info"}>{item.type}</Badge>
                </CardHeader>
                <CardContent className="flex flex-col gap-1 text-xs">
                  <p className="text-[var(--color-fg)]">{item.message}</p>
                  <div className="flex items-center justify-between text-[var(--color-muted)]">
                    <span>{formatDateTime(item.created_at)}</span>
                    {!item.is_read ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => markRead.mutate(item.id)}
                        disabled={markRead.isPending}
                      >
                        읽음 처리
                      </Button>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
