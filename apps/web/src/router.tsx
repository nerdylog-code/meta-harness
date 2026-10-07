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
import { AgentPage } from "./routes/agent";
import { EventInspectorPage } from "./routes/events";
import { MissionOverviewPage } from "./routes/index";
import { MissionsPage } from "./routes/missions";
import { MissionPage } from "./routes/mission";
import { ApprovalsPage } from "./routes/approvals";
import { ArtifactRoute } from "./routes/artifact";
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

const missionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/missions/$missionId",
  component: MissionDetailRoute,
});

function MissionDetailRoute() {
  const { missionId } = missionRoute.useParams();
  return <MissionPage missionId={missionId} />;
}

const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents",
  component: AgentsPage,
});

const agentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents/$agentId",
  component: AgentDetailRoute,
});

function AgentDetailRoute() {
  const { agentId } = agentRoute.useParams();
  return <AgentPage agentId={agentId} />;
}

const eventsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/events",
  component: EventInspectorPage,
});

const approvalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/approvals",
  component: ApprovalsPage,
});

const artifactRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/artifacts/$artifactId",
  component: ArtifactRoute,
});

const systemRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/system",
  component: SystemPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  missionsRoute,
  missionRoute,
  agentsRoute,
  agentRoute,
  eventsRoute,
  approvalsRoute,
  artifactRoute,
  systemRoute,
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
