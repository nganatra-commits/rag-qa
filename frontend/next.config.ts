import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export — produces /out at build time, deployable to S3+CloudFront.
  output: "export",
  // The page is purely client-rendered chat; we don't use Next/image so this
  // is just defensive in case anyone adds <Image>.
  images: { unoptimized: true },
  reactStrictMode: true,
  trailingSlash: true,
  typedRoutes: true,
};

export default nextConfig;
