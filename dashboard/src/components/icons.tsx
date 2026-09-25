import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={16}
      height={16}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export const TracesIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 3.5h7M4.5 8h8M6.5 12.5h7" />
  </Icon>
);

export const CostsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 13.5h11M4.5 11V8M8 11V4.5M11.5 11V6.5" />
  </Icon>
);

export const GatewayIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 8h11M10 4.5 13.5 8 10 11.5M6 4.5 2.5 8 6 11.5" />
  </Icon>
);

export const GuardrailsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 2 13 4v4c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4z" />
  </Icon>
);

export const EvalsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m3 8.5 3 3 7-7" />
  </Icon>
);

export const SunIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="2.75" />
    <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" />
  </Icon>
);

export const MoonIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7z" />
  </Icon>
);

export const SystemIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="2" y="3" width="12" height="8.5" rx="1.5" />
    <path d="M5.5 14h5" />
  </Icon>
);

export const ChevronLeftIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 3.5 5.5 8l4.5 4.5" />
  </Icon>
);

export const CloseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m4 4 8 8M12 4l-8 8" />
  </Icon>
);

export const RefreshIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13 8a5 5 0 1 1-1.46-3.54M13 2.5v3h-3" />
  </Icon>
);

export const FilterIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 4h11M4.5 8h7M6.5 12h3" />
  </Icon>
);

export const ArrowDownIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 3v10M4.5 9.5 8 13l3.5-3.5" />
  </Icon>
);

export const ArrowUpIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 13V3M4.5 6.5 8 3l3.5 3.5" />
  </Icon>
);

export const ChevronDownIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m4 6 4 4 4-4" />
  </Icon>
);

export const CopyIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
    <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" />
  </Icon>
);

/* Filter facets (sidebar). */
export const TagIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 3.5v4l6 6 5-5-6-6h-4a1 1 0 0 0-1 1Z" />
    <circle cx="5.5" cy="5.5" r="0.75" />
  </Icon>
);

export const StatusIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="5.5" />
    <circle cx="8" cy="8" r="2" />
  </Icon>
);

export const SourceIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 0 1-7 0V5.5ZM8 11.5v2" />
  </Icon>
);

export const ClientIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="2" y="3" width="12" height="10" rx="1.5" />
    <path d="m5 7 1.75 1.5L5 10M8.5 10h2.5" />
  </Icon>
);

export const ModelIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="4" y="4" width="8" height="8" rx="1.5" />
    <path d="M6.5 2v2M9.5 2v2M6.5 12v2M9.5 12v2M2 6.5h2M2 9.5h2M12 6.5h2M12 9.5h2" />
  </Icon>
);

export const ServiceIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m8 2 5.5 3v6L8 14l-5.5-3V5L8 2ZM2.5 5 8 8l5.5-3M8 8v6" />
  </Icon>
);
