import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { AppShell } from "@/components/shell";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Burnrate — AI spend intelligence", template: "%s · Burnrate" },
  description: "Track, attribute, forecast and reconcile LLM spend across OpenAI, Anthropic, Google and more.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#09090b" },
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
  ],
};

// Runs before paint: stored choice → OS preference → dark.
const themeScript = `(function(){try{var t=localStorage.getItem('burnrate-theme');if(t!=='light'&&t!=='dark'){t=window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme='dark'}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="font-sans antialiased">
        <Suspense fallback={null}>
          <AppShell>{children}</AppShell>
        </Suspense>
      </body>
    </html>
  );
}
