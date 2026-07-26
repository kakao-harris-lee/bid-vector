import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type { SyntheticExperimentPreset } from "@/shared/types/synthetic";

export function PresetPanel({
  presets,
  loading,
  saving,
  savingName,
  onSave
}: {
  presets: SyntheticExperimentPreset[];
  loading: boolean;
  saving: boolean;
  savingName: string | null;
  onSave: (name: string) => void;
}) {
  return (
    <Card aria-label="G-1 preset">
      <CardHeader>
        <CardTitle>G-1 preset</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {loading ? <p className="text-[var(--color-muted)]">불러오는 중…</p> : null}
        {presets.map((preset) => (
          <div
            key={preset.name}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--color-border)] px-2 py-1.5"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate font-medium text-[var(--color-fg)]">{preset.name}</span>
              <span className="truncate text-[var(--color-muted)]">
                {preset.params.category ?? "전체"} · limit {preset.params.limit}
              </span>
            </span>
            <span className="flex items-center gap-2">
              {preset.latest_run_status ? (
                <Badge tone={preset.latest_run_status === "completed" ? "healthy" : "info"}>
                  {preset.latest_run_status}
                </Badge>
              ) : preset.experiment_id ? (
                <Badge tone="muted">saved</Badge>
              ) : null}
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => onSave(preset.name)}
                disabled={saving}
              >
                {saving && savingName === preset.name ? "저장 중…" : "저장"}
              </Button>
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
