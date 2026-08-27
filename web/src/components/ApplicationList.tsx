import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StateBadge } from "@/components/StateBadge";
import { TERMINAL_STATES, type ApplicationListItem } from "@/lib/api";
import { cn } from "@/lib/utils";

function Row({
  item,
  selected,
  onSelect,
}: {
  item: ApplicationListItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex w-full flex-col gap-1 rounded-md border p-3 text-left transition-colors hover:bg-muted",
        selected && "border-primary bg-muted",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium">
          {item.company ?? item.application_id}
          {item.title ? ` · ${item.title}` : ""}
        </span>
        <StateBadge state={item.state} />
      </div>
      {item.reason && <span className="text-muted-foreground text-sm">{item.reason}</span>}
    </button>
  );
}

export function ApplicationList({
  items,
  selectedId,
  onSelect,
}: {
  items: ApplicationListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const inProgress = items.filter((i) => !TERMINAL_STATES.has(i.state));
  const history = items.filter((i) => TERMINAL_STATES.has(i.state));

  return (
    <Tabs defaultValue="progress" className="flex h-full flex-col">
      <TabsList>
        <TabsTrigger value="progress">진행 중 ({inProgress.length})</TabsTrigger>
        <TabsTrigger value="history">히스토리 ({history.length})</TabsTrigger>
      </TabsList>
      <TabsContent value="progress" className="min-h-0 flex-1">
        <List items={inProgress} selectedId={selectedId} onSelect={onSelect} empty="진행 중인 지원 건이 없습니다." />
      </TabsContent>
      <TabsContent value="history" className="min-h-0 flex-1">
        <List items={history} selectedId={selectedId} onSelect={onSelect} empty="히스토리가 없습니다." />
      </TabsContent>
    </Tabs>
  );
}

function List({
  items,
  selectedId,
  onSelect,
  empty,
}: {
  items: ApplicationListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  empty: string;
}) {
  if (items.length === 0) {
    return <p className="text-muted-foreground p-4 text-sm">{empty}</p>;
  }
  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-2 p-1">
        {items.map((item) => (
          <Row
            key={item.application_id}
            item={item}
            selected={item.application_id === selectedId}
            onSelect={() => onSelect(item.application_id)}
          />
        ))}
      </div>
    </ScrollArea>
  );
}
