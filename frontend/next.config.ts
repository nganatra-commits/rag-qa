import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Backend serves /api/images/{id}; we proxy them through Next so the browser
  // sees a single origin and we can add caching headers consistently.
  async rewrites() {
    const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
    return [
      { source: "/api/images/:image_id", destination: `${backend}/api/images/:image_id` },
    ];
  },
  typedRoutes: true,
};

export default nextConfig;
