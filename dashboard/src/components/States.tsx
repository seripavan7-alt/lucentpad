import type { ReactNode } from "react";
import styles from "./States.module.css";

interface StateProps {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ title, children, action }: StateProps) {
  return (
    <div className={styles.state}>
      <p className={styles.title}>{title}</p>
      {children && <div className={styles.body}>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}

export function ErrorState({ title, children, action }: StateProps) {
  return (
    <div className={styles.state} role="alert" data-tone="error">
      <p className={styles.title}>{title}</p>
      {children && <div className={styles.body}>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = "secondary",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary";
}) {
  return (
    <button
      type="button"
      className={styles.button}
      data-variant={variant}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
