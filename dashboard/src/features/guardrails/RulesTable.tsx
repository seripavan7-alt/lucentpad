import type { CSSProperties } from "react";
import type { GuardrailRule, GuardrailSummary } from "../../api/types";
import table from "../../components/DataTable.module.css";
import { formatInteger } from "../../lib/format";
import styles from "./Guardrails.module.css";

/** What a rule matches, in words: keywords or a regex for prompts, a tool and a condition. */
export function RuleMatch({ rule }: { rule: GuardrailRule }) {
  if (rule.type === "tool") {
    return (
      <>
        <span className="mono">{rule.tool ?? "any tool"}</span>
        {rule.condition && (
          <>
            <span className={table.tertiary}> when </span>
            <span className="mono">{rule.condition}</span>
          </>
        )}
      </>
    );
  }
  const parts: string[] = [];
  if (rule.keywords?.length) parts.push(rule.keywords.map((k) => `“${k}”`).join(", "));
  return (
    <>
      {parts.length > 0 && (
        <>
          <span className={table.tertiary}>prompt mentions </span>
          {parts[0]}
        </>
      )}
      {parts.length > 0 && rule.pattern && <span className={table.tertiary}> or </span>}
      {rule.pattern && (
        <>
          {parts.length === 0 && <span className={table.tertiary}>prompt matches </span>}
          <span className="mono">/{rule.pattern}/</span>
        </>
      )}
      {parts.length === 0 && !rule.pattern && <span className={table.tertiary}>–</span>}
    </>
  );
}

const matchTitle = (rule: GuardrailRule): string =>
  rule.type === "tool"
    ? `${rule.tool ?? "any tool"}${rule.condition ? ` when ${rule.condition}` : ""}`
    : [rule.keywords?.join(", "), rule.pattern && `/${rule.pattern}/`].filter(Boolean).join(" or ");

interface Props {
  rules: GuardrailRule[];
  summary: GuardrailSummary | undefined;
}

/**
 * The active rules with how often each blocked in the range. Rules that blocked in the range
 * but are no longer in the rules file are listed after them.
 */
export function RulesTable({ rules, summary }: Props) {
  const blocks = new Map(summary?.blocks.map((b) => [b.rule, b.blocks]) ?? []);
  const active = new Set(rules.map((r) => r.id));
  const retired = (summary?.blocks ?? []).filter((b) => !active.has(b.rule));
  const count = (n: number | undefined) =>
    summary === undefined ? (
      <span className={table.tertiary}>–</span>
    ) : n ? (
      formatInteger(n)
    ) : (
      <span className={table.tertiary}>0</span>
    );

  return (
    <div className={table.wrap}>
      <table className={table.table} style={{ "--table-min": "760px" } as CSSProperties}>
        <thead>
          <tr>
            <th scope="col" style={{ width: "22%" }}>
              Rule
            </th>
            <th scope="col" style={{ width: 72 }}>
              Type
            </th>
            <th scope="col" style={{ width: "30%" }}>
              Blocks when
            </th>
            <th scope="col">Message</th>
            <th scope="col" className={table.right} style={{ width: 80 }}>
              Blocks
            </th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.id} data-rule={rule.id}>
              <th scope="row" className={table.clip} title={rule.description ?? undefined}>
                <span className={`mono ${styles.ruleId}`}>{rule.id}</span>
              </th>
              <td className={table.secondary}>{rule.type === "tool" ? "Tool" : "Prompt"}</td>
              <td className={table.clip} title={matchTitle(rule)}>
                <RuleMatch rule={rule} />
              </td>
              <td className={`${table.clip} ${table.secondary}`} title={rule.message}>
                {rule.message}
              </td>
              <td className={`${table.right} num`}>{count(blocks.get(rule.id))}</td>
            </tr>
          ))}
          {retired.map((b) => (
            <tr key={b.rule} data-rule={b.rule}>
              <th scope="row" className={table.clip}>
                <span className={`mono ${styles.ruleId} ${table.secondary}`}>{b.rule}</span>
              </th>
              <td className={table.tertiary}>–</td>
              <td className={table.tertiary} colSpan={2}>
                No longer in the rules
              </td>
              <td className={`${table.right} num`}>{count(b.blocks)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
