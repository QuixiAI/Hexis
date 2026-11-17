interface ProgressBarProps {
  value: number;
  max: number;
  label?: string;
  showValue?: boolean;
  color?: "accent" | "teal" | "green" | "amber" | "red";
  className?: string;
}

const colorClasses: Record<string, string> = {
  accent: "bg-[var(--accent)]",
  teal: "bg-[var(--teal)]",
  green: "bg-green-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
};

function formatCount(value: number): string {
  // Energy and similar scores arrive as floats; never show binary noise.
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function ProgressBar({
  value,
  max,
  label,
  showValue = true,
  color = "accent",
  className = "",
}: ProgressBarProps) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;

  return (
    <div className={`space-y-1 ${className}`}>
      {(label || showValue) && (
        <div className="flex items-center justify-between text-xs text-[var(--ink-soft)]">
          {label && <span>{label}</span>}
          {showValue && (
            <span>
              {formatCount(value)}/{formatCount(max)}
            </span>
          )}
        </div>
      )}
      <div className="h-1.5 w-full rounded-full bg-[var(--surface-strong)]">
        <div
          className={`h-1.5 rounded-full transition-all ${colorClasses[color]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
