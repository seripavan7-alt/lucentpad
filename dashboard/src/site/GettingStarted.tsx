import { marked } from "marked";
import { useMemo } from "react";
import guide from "../../../docs/getting-started.md?raw";
import styles from "./Site.module.css";
import { SiteHeader } from "./SiteHeader";

export function GettingStarted() {
  // Our own markdown from the repo (docs/getting-started.md), nothing user-supplied.
  const html = useMemo(() => marked.parse(guide, { async: false }), []);
  return (
    <div className={styles.page}>
      <SiteHeader />
      <main className={styles.doc}>
        <article className={styles.prose} dangerouslySetInnerHTML={{ __html: html }} />
      </main>
    </div>
  );
}
