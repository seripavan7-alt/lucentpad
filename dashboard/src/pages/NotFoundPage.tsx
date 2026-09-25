import { Link } from "react-router";
import { PageHeader } from "../components/PageHeader";
import { EmptyState } from "../components/States";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Not found" />
      <EmptyState title="This page doesn't exist" action={<Link to="/traces">Go to Traces →</Link>}>
        Check the URL, or head back to the traces list.
      </EmptyState>
    </>
  );
}
