import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  agentRules: false,
  async rewrites() {
    const api = process.env.INTERNAL_API_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/v1/:path*", destination: `${api}/api/v1/:path*` }];
  },
};

export default config;
