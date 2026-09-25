import { useState, type ComponentType, type SVGProps } from "react";
import { Link, NavLink, Outlet } from "react-router";
import { useHealth } from "../api/queries";
import { useDemoMode } from "../demo/context";
import { Logo } from "./Logo";
import { CostsIcon, EvalsIcon, GatewayIcon, GuardrailsIcon, TracesIcon } from "./icons";
import styles from "./Layout.module.css";
import { SidebarSlotContext } from "./SidebarSlot";
import { ThemeToggle } from "./ThemeToggle";

interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
  milestone?: string;
}

const NAV: NavItem[] = [
  { to: "/traces", label: "Traces", Icon: TracesIcon },
  { to: "/costs", label: "Costs", Icon: CostsIcon, milestone: "M3" },
  { to: "/gateway", label: "Gateway", Icon: GatewayIcon, milestone: "M2" },
  { to: "/guardrails", label: "Guardrails", Icon: GuardrailsIcon, milestone: "M3" },
  { to: "/evals", label: "Evals", Icon: EvalsIcon, milestone: "M3" },
];

function ApiStatus() {
  const health = useHealth();
  const state = health.isPending ? "pending" : health.isSuccess ? "up" : "down";
  const label = { pending: "Connecting…", up: "API connected", down: "API unreachable" }[state];
  return (
    <span className={styles.apiStatus} data-state={state}>
      <span className={styles.apiDot} aria-hidden="true" />
      {label}
    </span>
  );
}

function DemoBanner({ siteHref }: { siteHref: string }) {
  return (
    <div className={styles.demoBanner} role="note">
      <span>
        <strong>Demo</strong> · sample data, read-only
      </span>
      {/* _top: the demo may be embedded in the landing page. */}
      <a href={siteHref} target="_top">
        Back to LucentPad
      </a>
    </div>
  );
}

export function Layout() {
  const demo = useDemoMode();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        {demo ? (
          <a
            href={demo.siteHref}
            target="_top"
            className={styles.brand}
            aria-label="LucentPad home"
          >
            <Logo />
          </a>
        ) : (
          <Link to="/" className={styles.brand} aria-label="LucentPad home">
            <Logo />
          </Link>
        )}
        <nav aria-label="Main" className={styles.nav}>
          {NAV.map(({ to, label, Icon, milestone }) => (
            <NavLink key={to} to={to} className={styles.navItem}>
              <Icon className={styles.navIcon} />
              <span className={styles.navLabel}>{label}</span>
              {milestone && <span className={styles.milestone}>{milestone}</span>}
            </NavLink>
          ))}
        </nav>
        <div ref={setSlot} className={styles.slot} />
        <div className={styles.footer}>
          {demo ? (
            <span className={styles.apiStatus} data-state="demo">
              <span className={styles.apiDot} aria-hidden="true" />
              Demo data
            </span>
          ) : (
            <ApiStatus />
          )}
          <ThemeToggle />
        </div>
      </aside>
      <main className={styles.main}>
        {demo && <DemoBanner siteHref={demo.siteHref} />}
        <SidebarSlotContext.Provider value={slot}>
          <Outlet />
        </SidebarSlotContext.Provider>
      </main>
    </div>
  );
}
