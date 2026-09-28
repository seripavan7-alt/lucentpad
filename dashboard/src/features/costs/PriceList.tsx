import type { PriceTable } from "../../api/types";
import styles from "./PriceList.module.css";

const usd = (n: number) => `$${n.toFixed(n < 1 && n > 0 ? 3 : 2).replace(/(\.\d\d)0$/, "$1")}`;

/** The list prices behind every cost on the page, folded away by default. */
export function PriceList({ table }: { table: PriceTable }) {
  return (
    <details className={styles.details}>
      <summary className={styles.summary}>
        Prices used · list prices checked {table.checked}
      </summary>
      <div className={styles.wrap}>
        <table className={styles.table}>
          <caption className="visually-hidden">USD per million tokens</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Input</th>
              <th scope="col">Output</th>
              <th scope="col">Cache read</th>
              <th scope="col">Cache write</th>
            </tr>
          </thead>
          <tbody>
            {table.prices.map((p) => (
              <tr key={p.model}>
                <th scope="row" className="mono">
                  {p.model}
                </th>
                <td className="num">{usd(p.input)}</td>
                <td className="num">{usd(p.output)}</td>
                <td className="num">{usd(p.cache_read)}</td>
                <td className="num">{usd(p.cache_write)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className={styles.unit}>USD per million tokens.</p>
      </div>
    </details>
  );
}
