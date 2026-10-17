export function AcmeMark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="Acme">
      <rect width="32" height="32" rx="8" className="fill-accent" />
      <path
        d="M10 23 16 9l6 14h-3.2l-1.1-2.8h-3.5L13.1 23Zm5.1-5.4h1.9L16 15.1Z"
        className="fill-accent-foreground"
      />
    </svg>
  );
}
