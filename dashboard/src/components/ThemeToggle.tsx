import type { ComponentType, SVGProps } from "react";
import { useThemePreference, type ThemePreference } from "../lib/theme";
import { MoonIcon, SunIcon, SystemIcon } from "./icons";
import styles from "./ThemeToggle.module.css";

const OPTIONS: {
  value: ThemePreference;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
}[] = [
  { value: "light", label: "Light", Icon: SunIcon },
  { value: "dark", label: "Dark", Icon: MoonIcon },
  { value: "system", label: "System", Icon: SystemIcon },
];

export function ThemeToggle() {
  const [pref, setPref] = useThemePreference();
  return (
    <div className={styles.group} role="group" aria-label="Theme">
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          className={styles.option}
          aria-pressed={pref === value}
          aria-label={`${label} theme`}
          title={`${label} theme`}
          onClick={() => {
            setPref(value);
          }}
        >
          <Icon width={14} height={14} />
        </button>
      ))}
    </div>
  );
}
