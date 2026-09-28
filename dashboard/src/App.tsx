import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { createQueryClient } from "./api/queryClient";
import { Layout } from "./components/Layout";
import { CostsPage } from "./pages/CostsPage";
import { EvalRunPage } from "./pages/EvalRunPage";
import { EvalsPage } from "./pages/EvalsPage";
import { GatewayPage } from "./pages/GatewayPage";
import { GuardrailsPage } from "./pages/GuardrailsPage";
import { NotFoundPage } from "./pages/NotFoundPage";
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
        <Route path="costs" element={<CostsPage />} />
        <Route path="gateway" element={<GatewayPage />} />
        <Route path="guardrails" element={<GuardrailsPage />} />
        <Route path="evals" element={<EvalsPage />} />
        <Route path="evals/:runId" element={<EvalRunPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
