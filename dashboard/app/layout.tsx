import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DART 감사보고서 · KAM 대시보드",
  description: "OPENDART 기반 감사의견 및 핵심감사사항 요약(비공식·참고용)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
