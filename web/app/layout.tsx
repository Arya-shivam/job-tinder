import type { Metadata } from "next";
import type { ReactNode } from "react";
import "@fontsource-variable/instrument-sans/wght.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Job Match",
  description: "A private job-search workspace with an evidence-backed job digest.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
