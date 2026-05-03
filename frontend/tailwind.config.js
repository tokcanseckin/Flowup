/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      colors: {
        // App-specific palette
        surface: {
          DEFAULT: '#0d0d14',
          card:    '#12121f',
          raised:  '#1a1a2e',
        },
      },
      keyframes: {
        'tooltip-enter': {
          '0%':   { opacity: '0', transform: 'translate(-50%, calc(-100% - 6px)) scale(0.92)' },
          '100%': { opacity: '1', transform: 'translate(-50%, calc(-100% - 14px)) scale(1)'   },
        },
        'tooltip-exit': {
          '0%':   { opacity: '1' },
          '100%': { opacity: '0' },
        },
        'line-pop': {
          '0%':   { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)'   },
        },
      },
      animation: {
        'tooltip-enter': 'tooltip-enter 0.15s cubic-bezier(0.22, 1, 0.36, 1) forwards',
        'tooltip-exit':  'tooltip-exit  0.25s ease-in forwards',
        'line-pop':      'line-pop      0.2s ease-out forwards',
      },
    },
  },
  plugins: [],
}
