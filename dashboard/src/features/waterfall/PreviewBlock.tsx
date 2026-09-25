import { useEffect, useId, useState } from "react";
import { CopyIcon } from "../../components/icons";
import styles from "./PreviewBlock.module.css";

/** Collapsed height, in lines. */
export const PREVIEW_COLLAPSED_LINES = 6;
/** Rough characters per line in the inspector, used to decide whether "Show more" is needed. */
const CHARS_PER_LINE = 60;

function needsCollapse(text: string): boolean {
  const lines = text.split("\n");
  const visualLines = lines.reduce(
    (n, line) => n + Math.max(1, Math.ceil(line.length / CHARS_PER_LINE)),
    0,
  );
  return visualLines > PREVIEW_COLLAPSED_LINES;
}

/**
 * A captured prompt or response (`lucentpad.{input,output}.preview`): line breaks kept, collapsed
 * to about six lines with "Show more", a note when the SDK truncated it at capture, and copy.
 */
export function PreviewBlock({
  label,
  text,
  truncated,
}: {
  label: string;
  text: string;
  truncated: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const bodyId = useId();
  const collapsible = needsCollapse(text);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => {
      setCopied(false);
    }, 1500);
    return () => {
      clearTimeout(t);
    };
  }, [copied]);

  const copy = () => {
    // Undefined outside secure contexts (e.g. plain http on a LAN address).
    const clipboard = navigator.clipboard as Clipboard | undefined;
    void clipboard?.writeText(text).then(
      () => {
        setCopied(true);
      },
      () => undefined,
    );
  };

  return (
    <section className={styles.block} aria-label={label}>
      <div className={styles.head}>
        <h3 className={styles.title}>{label}</h3>
        <button
          type="button"
          className={styles.copy}
          onClick={copy}
          aria-label={`Copy ${label.toLowerCase()}`}
          title="Copy"
        >
          <CopyIcon width={12} height={12} />
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div
        id={bodyId}
        className={styles.text}
        data-collapsed={collapsible && !expanded ? "true" : undefined}
      >
        {text}
      </div>
      {(collapsible || truncated) && (
        <div className={styles.foot}>
          {collapsible && (
            <button
              type="button"
              className={styles.more}
              aria-expanded={expanded}
              aria-controls={bodyId}
              onClick={() => {
                setExpanded((v) => !v);
              }}
            >
              {expanded ? "Show less" : "Show more"}
            </button>
          )}
          {truncated && <span className={styles.note}>Truncated at capture</span>}
        </div>
      )}
    </section>
  );
}
