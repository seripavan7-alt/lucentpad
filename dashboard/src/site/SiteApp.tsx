import { Navigate, Route, Routes } from "react-router";
import { GettingStarted } from "./GettingStarted";
import { Home } from "./home/Home";

export function SiteRoutes() {
  return (
    <Routes>
      <Route index element={<Home />} />
      <Route path="docs/getting-started" element={<GettingStarted />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
