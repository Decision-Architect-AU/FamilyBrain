/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg:      '#0f0f1a',
        surface: '#1a1a2e',
        border:  '#2a2a40',
        accent:  '#4A9EFF',
        muted:   '#888888',
      },
    },
  },
  plugins: [],
}
