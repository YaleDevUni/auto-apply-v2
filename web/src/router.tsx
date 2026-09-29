// 코드 기반 라우트 트리 (D16). 화면이 늘면 파일 기반(@tanstack/router-plugin)으로 옮길 수 있다.
import { createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { ProfilePage } from "@/features/profile/ProfilePage";
import { ComingSoon, NotFound } from "@/routes/placeholders";

const rootRoute = createRootRoute({ component: AppLayout, notFoundComponent: NotFound });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/profile" });
  },
});

const profileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/profile",
  component: ProfilePage,
});

const experiencesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/experiences",
  component: () => <ComingSoon title="experiences" />,
});

const answersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/answers",
  component: () => <ComingSoon title="answers" />,
});

const documentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents",
  component: () => <ComingSoon title="documents" />,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  profileRoute,
  experiencesRoute,
  answersRoute,
  documentsRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
