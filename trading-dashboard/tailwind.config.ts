import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: 'class',
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          base:    '#020617',
          card:    '#0A0F1E',
          elevated:'#0F172A',
          border:  '#1E293B',
          hover:   '#162032',
        },
        brand: {
          cyan:    '#06B6D4',
          'cyan-dim': '#0891B2',
        },
        bull:  '#22C55E',
        bear:  '#EF4444',
        caution: '#F59E0B',
        muted:  '#64748B',
        subtle: '#94A3B8',
        primary: '#F1F5F9',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'card-glow': 'linear-gradient(135deg, rgba(6,182,212,0.05) 0%, rgba(2,6,23,0) 60%)',
      },
      boxShadow: {
        'card': '0 0 0 1px rgba(30,41,59,0.8), 0 4px 24px rgba(0,0,0,0.4)',
        'glow-cyan': '0 0 20px rgba(6,182,212,0.15)',
        'glow-bull': '0 0 20px rgba(34,197,94,0.15)',
        'glow-bear': '0 0 20px rgba(239,68,68,0.15)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}

export default config
