/** @type {import('next').NextConfig} */
// NOTE: no `env` block here on purpose. TRADING_BOT_API_URL is read at runtime
// in lib/bot-api.ts (server-side only) — inlining it at build time freezes the
// localhost fallback into cached builds and breaks Vercel deployments.
const nextConfig = {
  reactStrictMode: true,
}

module.exports = nextConfig
