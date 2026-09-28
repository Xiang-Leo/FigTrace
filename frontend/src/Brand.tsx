export function BrandMark() {
  return (
    <>
      <svg
        className="brand-mark"
        viewBox="0 0 44 44"
        fill="none"
        aria-hidden="true"
      >
        <rect width="44" height="44" rx="12" fill="#365f3a" />
        <path
          d="M15 9h18a3 3 0 0 1 3 3v18"
          stroke="#a9c99a"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <rect
          x="8"
          y="14"
          width="23"
          height="22"
          rx="3"
          stroke="#f5f9ef"
          strokeWidth="2"
        />
        <path
          d="m12 30 7-8 8 5 9-10"
          stroke="#d6ebaa"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="19" cy="22" r="2.5" fill="#d6ebaa" />
        <circle cx="27" cy="27" r="2.5" fill="#d6ebaa" />
        <circle cx="36" cy="17" r="2.5" fill="#d6ebaa" />
      </svg>
      <span>FigTrace</span>
    </>
  );
}
