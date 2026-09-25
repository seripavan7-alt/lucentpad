import { Link } from "react-router";
import { Logo } from "../components/Logo";
import { ThemeToggle } from "../components/ThemeToggle";
import { DEMO_START, GITHUB_URL, siteHref } from "./links";
import styles from "./Site.module.css";

export function SiteHeader() {
  return (
    <header className={styles.header}>
      <Link to="/" className={styles.home} aria-label="LucentPad home">
        <Logo size={20} />
      </Link>
      <nav className={styles.headerNav} aria-label="Site">
        <Link to="/docs/getting-started">Docs</Link>
        <a href={siteHref(DEMO_START)}>Demo</a>
        <a href={GITHUB_URL}>GitHub</a>
        <span className={styles.theme}>
          <ThemeToggle />
        </span>
      </nav>
    </header>
  );
}
