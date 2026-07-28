/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#182026",
        mint: "#4fb89c",
        coral: "#e96f5b",
        amber: "#d69c2f"
      }
    }
  },
  plugins: []
};
