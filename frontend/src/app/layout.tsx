import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/sidebar";

export const metadata: Metadata = {
  title: "电商经营数据平台",
  description: "连接吉客云 + 1688 + 浙江农信的经营数据平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 overflow-y-auto px-8 py-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
