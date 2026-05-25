import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { NotificationItem } from "@/shared/types/notifications";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export function fetchNotifications(
  options: { limit?: number; unreadOnly?: boolean; type?: string } = {},
  token?: string | null
): Promise<NotificationItem[]> {
  const search = new URLSearchParams();
  if (typeof options.limit === "number") search.set("limit", String(options.limit));
  if (options.unreadOnly) search.set("unread_only", "true");
  if (options.type) search.set("notification_type", options.type);
  const qs = search.toString();
  const path = qs ? `/api/v1/operator/notifications?${qs}` : "/api/v1/operator/notifications";
  return wrap(
    apiRequest<NotificationItem[]>(path, { token }),
    "알림을 불러오지 못했습니다."
  );
}

export function markNotificationRead(
  notificationId: number,
  token?: string | null
): Promise<NotificationItem> {
  return wrap(
    apiRequest<NotificationItem>(
      `/api/v1/operator/notifications/${notificationId}/read`,
      { method: "PUT", token }
    ),
    "알림 읽음 처리에 실패했습니다."
  );
}
