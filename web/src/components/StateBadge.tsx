import { Badge } from "@/components/ui/badge";
import type { ApplicationState } from "@/lib/api";
import { STATE_LABELS, stateTone } from "@/lib/state-labels";

const TONE_CLASS: Record<string, string> = {
  success: "border-emerald-500/40 text-emerald-600 dark:text-emerald-400",
  warning: "border-amber-500/40 text-amber-600 dark:text-amber-400",
  default: "",
};

export function StateBadge({ state }: { state: ApplicationState }) {
  const tone = stateTone(state);
  if (tone === "destructive") {
    return <Badge variant="destructive">{STATE_LABELS[state]}</Badge>;
  }
  return (
    <Badge variant="outline" className={TONE_CLASS[tone]}>
      {STATE_LABELS[state]}
    </Badge>
  );
}
