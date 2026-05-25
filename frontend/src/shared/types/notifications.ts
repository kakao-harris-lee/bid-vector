export interface NotificationItem {
  id: number;
  title: string;
  message: string;
  type: string;
  is_read: boolean;
  created_at: string;
}

export interface RealtimeEvent {
  type: string;
  payload?: Record<string, unknown> | null;
  occurred_at?: string;
}
