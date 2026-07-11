// Entry point for the standalone demo landing page (no data layer needed).
import React from "react";
import ReactDOM from "react-dom/client";
import DemoLanding from "./DemoLanding";

ReactDOM.createRoot(document.getElementById("demo-root")!).render(
  <React.StrictMode>
    <DemoLanding />
  </React.StrictMode>,
);
