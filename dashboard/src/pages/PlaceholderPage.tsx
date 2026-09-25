import { PageHeader } from "../components/PageHeader";
import styles from "./PlaceholderPage.module.css";

export function PlaceholderPage({
  title,
  milestone,
  description,
}: {
  title: string;
  milestone: string;
  description: string;
}) {
  return (
    <>
      <PageHeader title={title} />
      <div className={styles.wrap}>
        <div className={styles.card}>
          <span className={styles.badge}>Arrives in {milestone}</span>
          <h2 className={styles.title}>{title}</h2>
          <p className={styles.description}>{description}</p>
        </div>
      </div>
    </>
  );
}
