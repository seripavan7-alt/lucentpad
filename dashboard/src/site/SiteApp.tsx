import { Navigate, Route, Routes } from "react-router";
import { GettingStarted } from "./GettingStarted";
import { Landing } from "./Landing";

export function SiteRoutes() {
  return (
    <Routes>
      <Route index element={<Landing />} />
      <Route path="docs/getting-started" element={<GettingStarted />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
