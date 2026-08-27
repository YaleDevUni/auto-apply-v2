import { useState } from "react";
import { ApplicationDetail } from "@/components/ApplicationDetail";
import { ApplicationList } from "@/components/ApplicationList";
import { ApplyBar } from "@/components/ApplyBar";
import { useApplicationList } from "@/lib/queries";

function App() {
  const { data: items } = useApplicationList();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selectedItem = items?.find((i) => i.application_id === selectedId);

  return (
    <div className="mx-auto flex h-svh max-w-6xl flex-col gap-4 p-4">
      <header className="flex flex-col gap-2">
        <h1 className="text-xl font-semibold">auto-apply 콘솔</h1>
        <ApplyBar onStarted={setSelectedId} />
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <section className="min-h-0">
          <ApplicationList
            items={items ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </section>
        <section className="min-h-0 overflow-y-auto">
          {selectedId ? (
            <ApplicationDetail applicationId={selectedId} listItem={selectedItem} />
          ) : (
            <p className="text-muted-foreground p-4 text-sm">
              왼쪽 목록에서 지원 건을 선택하세요.
            </p>
          )}
        </section>
      </main>
    </div>
  );
}

export default App;
