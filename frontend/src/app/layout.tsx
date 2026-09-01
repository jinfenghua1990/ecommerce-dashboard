import type { Metadata } from "next";
import "./globals.css";
import AuthShell from "@/components/auth-shell";

export const metadata: Metadata = {
  title: "电商经营数据平台",
  description: "连接吉客云 + 1688 + 浙江农信的经营数据平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AuthShell>{children}</AuthShell>
      </body>
    </html>
  );
}
