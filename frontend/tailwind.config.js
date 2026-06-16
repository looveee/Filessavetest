/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Space Mono"', 'ui-monospace', 'monospace'],
        sans: ['"Inter"', 'system-ui', 'sans-serif'],
      },
      colors: {
        ink: {
          50:  '#f6f7f9',
          100: '#eceef2',
          200: '#d5d9e1',
          300: '#abb2bf',
          400: '#7a8294',
          500: '#525a6c',
          600: '#373e4d',
          700: '#252a36',
          800: '#171b25',
          900: '#0c0f17',
        },
        accent: {
          DEFAULT: '#ff5436',
          400: '#ff7256',
          600: '#e64528',
        },
        lime: {
          DEFAULT: '#c6f432',
        },
      },
      boxShadow: {
        'hard': '4px 4px 0 0 rgba(12,15,23,1)',
        'hard-sm': '2px 2px 0 0 rgba(12,15,23,1)',
      },
    },
  },
  plugins: [],
};
