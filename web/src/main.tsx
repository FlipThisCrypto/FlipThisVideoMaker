import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Placeholder } from "./components/Placeholder";
import { Dashboard } from "./pages/Dashboard";
import { Characters } from "./pages/Characters";
import { Projects } from "./pages/Projects";
import { ProjectEditor } from "./pages/ProjectEditor";
import { Jobs } from "./pages/Jobs";
import { Providers } from "./pages/Providers";
import { Renders } from "./pages/Renders";
import "./index.css";
const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "projects", element: <Projects /> },
      { path: "projects/:id", element: <ProjectEditor /> },
      { path: "projects/:id/characters", element: <Characters /> },
      { path: "jobs", element: <Jobs /> },
      { path: "providers", element: <Providers /> },
      { path: "renders", element: <Renders /> },
      { path: "settings", element: <Placeholder title="Settings" /> },
    ],
  },
]);
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
