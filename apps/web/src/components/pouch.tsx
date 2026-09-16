export function Pouch({
  variant = "green",
  title,
  small = false,
}: {
  variant?: "green" | "orange" | "cream";
  title?: string;
  small?: boolean;
}) {
  return (
    <div
      className={`pouch pouch-${variant} ${small ? "pouch-small" : ""}`}
      aria-label={`${title || "제주 말차"} 포장 디자인 예시`}
    >
      <div className="pouch-top" />
      <div className="pouch-brand">a little, everyday.</div>
      <div className="pouch-title">
        {title ||
          (variant === "orange"
            ? "햇살 담은\n감귤칩"
            : variant === "cream"
              ? "매일\n오트"
              : "제주\n말차")}
      </div>
      <div className="pouch-en">
        {variant === "orange"
          ? "JEJU TANGERINE"
          : variant === "cream"
            ? "DAILY OAT GRANOLA"
            : "PURE MATCHA"}
      </div>
      <svg className="pouch-art" viewBox="0 0 220 160" aria-hidden="true">
        <path
          d="M20 145C20 65 95 25 120 15C125 75 98 133 20 145Z"
          fill="currentColor"
          opacity=".75"
        />
        <path
          d="M64 145C80 73 139 36 199 55C189 113 143 152 64 145Z"
          fill="currentColor"
          opacity=".45"
        />
        <path
          d="M25 146L128 33M78 147L181 69"
          stroke="var(--pouch-bg)"
          strokeWidth="2.5"
          fill="none"
        />
      </svg>
      <div className="pouch-bottom">
        <span>나를 위한 작은 습관</span>
        <strong>
          {variant === "cream" ? "250" : "100"}
          <small> g</small>
        </strong>
      </div>
      <div className="pouch-seal" />
    </div>
  );
}
