import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    const apiBase = process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8081/api";
    return [{ source: "/api/:path*", destination: `${apiBase}/:path*` }];
  },
};

export default nextConfig;
