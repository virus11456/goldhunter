export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="gh-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffd54a" />
          <stop offset="1" stopColor="#d99a00" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="8" fill="url(#gh-g)" />
      <path d="M8 21l5-6 4 3.5 7-8.5" fill="none" stroke="#0b0e11" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="24" cy="10" r="2.2" fill="#0b0e11" />
    </svg>
  )
}
