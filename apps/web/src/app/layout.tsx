import type { Metadata } from "next";
import "./globals.css";
import "./workspace-tools.css";
import { AcquisitionCapture } from "@/components/acquisition-capture";
export const metadata: Metadata = {
  title: {
    default: "Phoenix Packaging — 좋은 제품의 다음 모습",
    template: "%s · Phoenix Packaging",
  },
  description:
    "상품의 이야기를 담은 포장 디자인. 한글 편집부터 규격 확인, 검토용 PDF까지 한곳에서 시작하세요.",
  robots: { index: false, follow: false },
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body><AcquisitionCapture />{children}</body>
    </html>
  );
}
