import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AppProviders } from "@/app/providers";
import { AppRoutes } from "@/app/router";
import { Toaster } from "@/shared/components/ui";
import "@/styles/globals.css";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <AppProviders>
      <BrowserRouter>
        <AppRoutes />
        <Toaster />
      </BrowserRouter>
    </AppProviders>
  </React.StrictMode>
);
