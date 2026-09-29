import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { ko } from "@/i18n/ko";

export const defaultNS = "translation";
export const resources = { ko: { translation: ko } } as const;

void i18n.use(initReactI18next).init({
  lng: "ko",
  fallbackLng: "ko",
  defaultNS,
  resources,
  // React 가 이미 이스케이프한다.
  interpolation: { escapeValue: false },
});

export default i18n;
