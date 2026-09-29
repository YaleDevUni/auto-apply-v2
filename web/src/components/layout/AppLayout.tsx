import { Link, Outlet } from "@tanstack/react-router";
import { BookText, Briefcase, FileText, MessageSquareText, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

type NavKey = "profile" | "experiences" | "answers" | "documents";

const NAV: { to: `/${NavKey}`; key: NavKey; icon: LucideIcon }[] = [
  { to: "/profile", key: "profile", icon: BookText },
  { to: "/experiences", key: "experiences", icon: Briefcase },
  { to: "/answers", key: "answers", icon: MessageSquareText },
  { to: "/documents", key: "documents", icon: FileText },
];

export function AppLayout() {
  const { t } = useTranslation();
  return (
    <div className="flex h-svh">
      <aside className="bg-sidebar text-sidebar-foreground border-sidebar-border flex w-52 shrink-0 flex-col gap-4 border-r p-3">
        <div className="px-2 pt-1">
          <p className="font-semibold">{t("app.title")}</p>
          <p className="text-muted-foreground text-xs">{t("app.subtitle")}</p>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ to, key, icon: Icon }) => (
            <Link
              key={key}
              to={to}
              className="hover:bg-sidebar-accent flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm"
              activeProps={{ className: "bg-sidebar-accent text-sidebar-accent-foreground font-medium" }}
            >
              <Icon className="size-4" />
              {t(`nav.${key}`)}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
