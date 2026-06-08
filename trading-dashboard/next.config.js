/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    TRADING_BOT_API_URL: process.env.TRADING_BOT_API_URL || 'http://localhost:8000',
  },
}

module.exports = nextConfig
