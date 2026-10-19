/** @type {import('next').NextConfig} */
const nextConfig = {
  // "standalone" keeps the Docker image small; Vercel ignores it and uses its own output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
};

export default nextConfig;
