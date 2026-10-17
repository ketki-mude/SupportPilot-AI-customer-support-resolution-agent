import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge only knows Tailwind's default scales. Register the custom border width and
 * radius tokens, or it mistakes `border-hairline` for a border color and drops it whenever a
 * real color class such as `border-border` follows.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "border-w": [{ border: ["hairline"] }],
      "border-w-x": [{ "border-x": ["hairline"] }],
      "border-w-y": [{ "border-y": ["hairline"] }],
      "border-w-t": [{ "border-t": ["hairline"] }],
      "border-w-r": [{ "border-r": ["hairline"] }],
      "border-w-b": [{ "border-b": ["hairline"] }],
      "border-w-l": [{ "border-l": ["hairline"] }],
      rounded: [{ rounded: ["control", "card"] }],
    },
  },
});

/** Merge class names, letting later Tailwind classes override earlier ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** localStorage that never throws (private windows, blocked storage). */
export const safeStorage = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string | null): void {
    try {
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch {
      // Storage unavailable: the session simply won't survive a reload.
    }
  },
};
