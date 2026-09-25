import { Link } from "react-router";
import { LogoMark } from "../components/Logo";
import { DEMO_START, siteHref } from "./links";
import styles from "./Site.module.css";
import { SiteHeader } from "./SiteHeader";

export function Landing() {
  return (
    <div className={styles.page}>
      <SiteHeader />
      <main className={styles.hero}>
        <LogoMark size={56} />
        <p className={styles.eyebrow}>Open source · LLM observability</p>
        <h1 className={styles.title}>See every step your AI agents take.</h1>
        <p className={styles.lede}>
          LucentPad traces every LLM call your agents and coding assistants make, and draws each run
          as a live waterfall with tokens, latency and cost per step.
        </p>
        <div className={styles.actions}>
          <Link to="/docs/getting-started" className={styles.buttonSecondary}>
            How to set up and use
          </Link>
          <a href={siteHref(DEMO_START)} className={styles.buttonPrimary}>
            Try the demo
          </a>
        </div>
        <p className={styles.note}>The demo runs in your browser on sample data. No sign-up.</p>
      </main>
    </div>
  );
}
