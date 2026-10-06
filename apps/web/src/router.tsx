/**
 * The route tree, built in code rather than by a codegen plugin.
 *
 * The skeleton needs four destinations and a shell; a generated route tree would add a
 * build step and a source of truth for something this small. When the surface grows to the
 * full information architecture of BOOK §59 (labs, evaluations, workboard), this decision is
 * worth revisiting -- it is recorded in the WP-006 status rather than left implicit.
 */

import {
  Link,
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { AgentsPage } from "./routes/agents";
import { EventInspectorPage } from "./routes/events";
import { MissionOverviewPage } from "./routes/index";
import { MissionsPage } from "./routes/missions";
import { SystemPage } from "./routes/system";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { EventStreamProvider } from "./stream";

function RootLayout() {
  return (
    <EventStreamProvider>
      <div className="app">
        <Sidebar />
        <div className="workspace">
          <TopBar />
          <main className="surface">
            <Outlet />
          </main>
        </div>
      </div>
    </EventStreamProvider>
  );
}

function NotFound() {
  return (
    <section className="panel">
      <h2>not found</h2>
      <p className="muted">
        This path does not exist in the control plane. <Link to="/">Back to the overview</Link>.
      </p>
    </section>
  );
}

const rootRoute = createRootRoute({ component: RootLayout, notFoundComponent: NotFound });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: MissionOverviewPage,
});

const missionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/missions",
  component: MissionsPage,
});

const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents",
  component: AgentsPage,
});

const eventsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/events",
  component: EventInspectorPage,
});

const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  component: SystemPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  missionsRoute,
  agentsRoute,
  eventsRoute,
  systemRoute,
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
