import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { GalleryProvider } from "@/components/image-gallery";
import { PdfViewerProvider } from "@/components/pdf-viewer";
import "./globals.css";

// Inter — matches Insights' design spec. Exposed via CSS var --font-inter
// which globals.css feeds into --font-sans.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "NWA QA Assistant",
  description: "Image-friendly RAG over the NWA Quality Analyst documentation set.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-dvh">
        <PdfViewerProvider>
          <GalleryProvider>{children}</GalleryProvider>
        </PdfViewerProvider>
      </body>
    </html>
  );
}
