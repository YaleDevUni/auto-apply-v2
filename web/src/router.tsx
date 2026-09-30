// 코드 기반 라우트 트리 (D16). 화면이 늘면 파일 기반(@tanstack/router-plugin)으로 옮길 수 있다.
import { createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { NotFound } from "@/components/layout/NotFound";
import { AnswersPage } from "@/features/answers/AnswersPage";
import { DocumentsPage } from "@/features/documents/DocumentsPage";
import { ExperiencesPage } from "@/features/experiences/ExperiencesPage";
import { DraftReviewPage } from "@/features/onboarding/DraftReviewPage";
import { OnboardingPage } from "@/features/onboarding/OnboardingPage";
import { StartRedirect } from "@/features/onboarding/StartRedirect";
import { ProfilePage } from "@/features/profile/ProfilePage";

const rootRoute = createRootRoute({ component: AppLayout, notFoundComponent: NotFound });

// 경로 문자열이 리터럴 타입으로 남아야 `<Link to>`·`navigate` 가 타입 검사된다.
const page = <P extends string>(path: P, component: () => React.ReactNode) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

const routeTree = rootRoute.addChildren([
  page("/", StartRedirect),
  page("/onboarding", OnboardingPage),
  page("/onboarding/$draftId", DraftReviewPage),
  page("/profile", ProfilePage),
  page("/experiences", ExperiencesPage),
  page("/answers", AnswersPage),
  page("/documents", DocumentsPage),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
