import type { ComponentType, SVGProps } from "react";
import { NavLink, Outlet } from "react-router";
import { useHealth } from "../api/queries";
import { CostsIcon, EvalsIcon, GatewayIcon, GuardrailsIcon, TracesIcon } from "./icons";
import styles from "./Layout.module.css";
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

export function Layout() {
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <svg width="18" height="18" viewBox="0 0 32 32" aria-hidden="true">
            <path
              d="M16 4 28.5 26.5h-25z"
              fill="none"
              stroke="currentColor"
              strokeWidth="3"
              strokeLinejoin="round"
            />
          </svg>
          <span>Prism</span>
        </div>
        <nav aria-label="Main" className={styles.nav}>
          {NAV.map(({ to, label, Icon, milestone }) => (
            <NavLink key={to} to={to} className={styles.navItem}>
              <Icon className={styles.navIcon} />
              <span className={styles.navLabel}>{label}</span>
              {milestone && <span className={styles.milestone}>{milestone}</span>}
            </NavLink>
          ))}
        </nav>
        <div className={styles.footer}>
          <ApiStatus />
          <ThemeToggle />
        </div>
      </aside>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
