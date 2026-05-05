import type { Metadata } from "next";
import { GalleryProvider } from "@/components/image-gallery";
import { PdfViewerProvider } from "@/components/pdf-viewer";
import "./globals.css";

export const metadata: Metadata = {
  title: "NWA QA Assistant",
  description: "Image-friendly RAG over the NWA Quality Analyst documentation set.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-dvh">
        <PdfViewerProvider>
          <GalleryProvider>{children}</GalleryProvider>
        </PdfViewerProvider>
      </body>
    </html>
  );
}
