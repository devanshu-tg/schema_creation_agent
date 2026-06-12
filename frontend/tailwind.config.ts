import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Light Savanna palette — matches TG Cloud chrome
        tg: {
          orange: '#F47B20',
          purple: {
            // Kept as accent colors (used in highlights / focus rings)
            DEFAULT: '#F47B20',
            50: 'rgba(244,123,32,0.08)',
            100: 'rgba(244,123,32,0.15)',
            500: '#F47B20',
            600: '#E66A0E',
            700: '#C2410C',
          },
          // light surfaces
          bg:      '#FFFFFF',  // app background
          panel:   '#FFFFFF',  // sidebar / chat panel / canvas all white
          card:    '#FFFFFF',  // cards / inputs on white
          hover:   '#F5F6F8',  // subtle hover wash
          // borders
          border:  '#E5E7EB',  // panel dividers
          line:    '#EEF0F2',  // card border (a touch lighter)
          // text
          ink:     '#1F2937',  // primary text
          mute:    '#6B7280',  // secondary text
          subtle:  '#9CA3AF',  // tertiary / placeholder
          // legacy alias (some components still use sidebar)
          sidebar: '#FAFAFB',
        },
        // Light Savanna palette — used by chat panel to match TG Cloud chrome.
        tgl: {
          panel:    '#FFFFFF',
          bubble:   '#F1F2F4',   // agent bubble background
          card:     '#FFFFFF',   // user bubble background
          border:   '#E5E7EB',
          line:     '#EEF0F2',
          ink:      '#1F2937',   // primary text on white
          mute:     '#6B7280',
          subtle:   '#9CA3AF',
          chip:     '#FCE8D0',   // peach pill background
          chipInk:  '#C2410C',   // peach pill text (dark orange)
          chipHover:'#FBD9B3',
          activeBg: '#DCFCE7',   // "AGENT ACTIVE" pill bg
          activeInk:'#15803D',   // green text
          activeDot:'#22C55E',
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
