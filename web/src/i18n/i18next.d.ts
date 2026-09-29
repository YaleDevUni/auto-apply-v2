import "i18next";
import type { defaultNS, resources } from "@/i18n";

// 없는 키를 쓰면 컴파일 에러가 나게 한다.
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: typeof defaultNS;
    resources: (typeof resources)["ko"];
  }
}
