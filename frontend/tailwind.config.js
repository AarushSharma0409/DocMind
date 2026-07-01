/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#0D1117",
        surface: {
          DEFAULT: "#161B22",
          raised: "#1C2129",
          sunken: "#0A0D12",
        },
        border: {
          DEFAULT: "#30363D",
          subtle: "#21262D",
        },
        primary: {
          DEFAULT: "#8B5CF6",
          hover: "#9D72F7",
          active: "#7C3AED",
          muted: "#8B5CF61A",
          subtle: "#8B5CF633",
        },
        foreground: {
          DEFAULT: "#F8FAFC",
          muted: "#9CA3AF",
          subtle: "#6B7280",
        },
        success: { DEFAULT: "#22C55E", muted: "#22C55E1A" },
        warning: { DEFAULT: "#F59E0B", muted: "#F59E0B1A" },
        error: { DEFAULT: "#EF4444", muted: "#EF44441A" },
      },
      fontFamily: {
        display: ["'Lexend'", "system-ui", "sans-serif"],
        body: ["'Source Sans 3'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      fontSize: {
        "display-lg": ["2.5rem", { lineHeight: "1.15", letterSpacing: "-0.02em", fontWeight: "600" }],
        "display-md": ["1.875rem", { lineHeight: "1.2", letterSpacing: "-0.015em", fontWeight: "600" }],
        "display-sm": ["1.375rem", { lineHeight: "1.3", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-lg": ["1.0625rem", { lineHeight: "1.65" }],
        body: ["0.9375rem", { lineHeight: "1.6" }],
        "body-sm": ["0.8125rem", { lineHeight: "1.5" }],
        data: ["0.75rem", { lineHeight: "1.4", letterSpacing: "0.01em" }],
      },
      borderRadius: {
        lg: "0.75rem",
        md: "0.5rem",
        sm: "0.375rem",
      },
      boxShadow: {
        panel: "0 1px 2px rgba(0,0,0,0.4), 0 4px 16px rgba(0,0,0,0.24)",
        floating: "0 8px 30px rgba(0,0,0,0.5)",
        "glow-primary": "0 0 0 1px #8B5CF633, 0 4px 20px #8B5CF61A",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.25s ease-out",
        "fade-in": "fade-in 0.2s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
