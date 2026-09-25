import styles from "./Logo.module.css";

/** The LucentPad mark: two overlapping rounded-square outlines. Drawn in the accent colour. */
export function LogoMark({ size = 18 }: { size?: number }) {
  return (
    <svg
      className={styles.mark}
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      aria-hidden="true"
    >
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <rect x="11" y="11" width="18" height="18" rx="5" />
    </svg>
  );
}

/** Mark plus wordmark: "Lucent" in ink, "Pad" in accent. */
export function Logo({ size = 18 }: { size?: number }) {
  return (
    <span className={styles.logo} style={{ fontSize: size * 0.8 }}>
      <LogoMark size={size} />
      <span className={styles.word}>
        Lucent<span className={styles.accent}>Pad</span>
      </span>
    </span>
  );
}
