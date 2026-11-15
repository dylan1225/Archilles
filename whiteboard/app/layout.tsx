import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Margin — A little space to think it through",
  description: "Write your math, find your mistakes, and learn along the way. A personal whiteboard with gentle, live feedback powered by Gemini.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
