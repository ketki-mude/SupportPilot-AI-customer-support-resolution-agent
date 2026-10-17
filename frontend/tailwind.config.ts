import type { Config } from "tailwindcss";

const token = (name: string) => `hsl(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: token("background"),
        surface: token("surface"),
        muted: token("muted"),
        border: token("border"),
        input: token("input"),
        foreground: token("foreground"),
        subtle: token("subtle"),
        accent: { DEFAULT: token("accent"), foreground: token("accent-foreground"), soft: token("accent-soft") },
        success: { DEFAULT: token("success"), soft: token("success-soft") },
        warning: { DEFAULT: token("warning"), soft: token("warning-soft") },
        danger: { DEFAULT: token("danger"), soft: token("danger-soft") },
        ring: token("ring"),
      },
      borderRadius: { control: "var(--radius-control)", card: "var(--radius-card)" },
      borderWidth: { hairline: "var(--hairline)" },
      fontFamily: { sans: ['"Inter Variable"', "Inter", "system-ui", "sans-serif"] },
    },
    // The design has no shadows and no gradients; removing the utilities keeps it that way.
    boxShadow: { none: "none" },
    backgroundImage: {},
  },
  plugins: [],
} satisfies Config;
