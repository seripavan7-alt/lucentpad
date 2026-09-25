import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { createQueryClient } from "./api/queryClient";
import { Layout } from "./components/Layout";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { TraceDetailPage } from "./pages/TraceDetailPage";
import { TracesPage } from "./pages/TracesPage";

export function AppProviders({ children }: { children: ReactNode }) {
  const [client] = useState(createQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/traces" replace />} />
        <Route path="traces" element={<TracesPage />} />
        <Route path="traces/:traceId" element={<TraceDetailPage />} />
        <Route
          path="costs"
          element={
            <PlaceholderPage
              title="Costs"
              milestone="M3"
              description="Spend over time by model, client and agent, with budget alerts."
            />
          }
        />
        <Route
          path="gateway"
          element={
            <PlaceholderPage
              title="Gateway"
              milestone="M2"
              description="A live feed of Claude Code and Copilot requests through the LucentPad gateway, with tokens, cost and failovers per turn."
            />
          }
        />
        <Route
          path="guardrails"
          element={
            <PlaceholderPage
              title="Guardrails"
              milestone="M3"
              description="Redactions and blocked prompts, and the rules that caught them."
            />
          }
        />
        <Route
          path="evals"
          element={
            <PlaceholderPage
              title="Evals"
              milestone="M3"
              description="Results of lucentpad eval runs against the baseline, including CI regressions."
            />
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
