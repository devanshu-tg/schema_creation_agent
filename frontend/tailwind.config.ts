import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Dark Savanna palette
        tg: {
          orange: '#F47B20',
          purple: {
            DEFAULT: '#6C4EE3',
            50: 'rgba(108,78,227,0.10)',
            100: 'rgba(108,78,227,0.20)',
            500: '#6C4EE3',
            600: '#7C61E7',
            700: '#9684EE',
          },
          // dark surfaces (all panels identical — only cards elevate)
          bg:      '#17181C',
          panel:   '#17181C',  // identical to bg — sidebar / chat panel / canvas all match
          card:    '#22242A',  // subtle elevation for buttons / cards / inputs
          hover:   '#2E3138',
          // borders
          border:  '#272A30',  // slightly visible divider between panels
          line:    '#34373E',  // card border (a touch lighter)
          // text
          ink:     '#E8EAED',
          mute:    '#9CA3AF',
          subtle:  '#6B7280',
          // legacy alias (some components still use sidebar)
          sidebar: '#1F2026',
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgba(0,0,0,0.04), 0 1px 6px 0 rgba(0,0,0,0.04)',
        'card-hover': '0 4px 12px 0 rgba(108, 78, 227, 0.08)',
      },
    },
  },
  plugins: [],
};

export default config;
