import type { ReactNode } from "react";
import styles from "./PageHeader.module.css";

export function PageHeader({
  title,
  children,
  actions,
}: {
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.header}>
      <div className={styles.titles}>
        <h1 className={styles.title}>{title}</h1>
        {children}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
