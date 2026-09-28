/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0b0e11',
        panel: '#15191e',
        panel2: '#1e2329',
        line: '#2a2f36',
        muted: '#848e9c',
        gold: '#f0b90b',
        up: '#0ecb81',
        down: '#f6465d',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', '"Segoe UI"', '"PingFang TC"', '"Microsoft JhengHei"', '"Noto Sans TC"', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}
