/**
 * The sidebar: the information architecture of BOOK §59, with the parts that do not exist
 * yet shown as explicitly unavailable rather than as links to nowhere.
 *
 * The distinction matters. A disabled entry with a `soon` tag tells the truth about the
 * system's maturity; a nav item that opens an empty page pretends the feature is there.
 */

import { Link } from "@tanstack/react-router";

const ACTIVE = { className: "nav-link active" };

function NavLink({ to, label, hint }: { to: string; label: string; hint?: string }) {
  return (
    <Link to={to} className="nav-link" activeProps={ACTIVE} activeOptions={{ exact: true }}>
      <span>{label}</span>
      {hint ? <span className="tag">{hint}</span> : null}
    </Link>
  );
}

function Pending({ label }: { label: string }) {
  return (
    <span className="nav-link disabled" title="not built yet">
      <span>{label}</span>
      <span className="tag">soon</span>
    </span>
  );
}

export function Sidebar() {
  return (
    <nav className="sidebar">
      <div className="brand">
        <strong>Meta-Harness</strong>
        <span>control plane</span>
      </div>

      <div className="nav-group">
        <Pending label="New mission" />
      </div>

      <div className="nav-group">
        <h3>Missions</h3>
        <NavLink to="/" label="Overview" />
        <NavLink to="/missions" label="Missions" />
      </div>

      <div className="nav-group">
        <h3>Agents</h3>
        <NavLink to="/agents" label="Roster" />
      </div>

      <div className="nav-group">
        <h3>Labs</h3>
        <Pending label="Factory" />
        <Pending label="RAG" />
        <Pending label="Benchmarks" />
        <Pending label="Evals" />
      </div>

      <div className="nav-group">
        <h3>System</h3>
        <NavLink to="/events" label="Event stream" />
        <NavLink to="/system" label="Runtime" />
        <Pending label="Plugins" />
        <Pending label="Runtimes" />
        <Pending label="Usage" />
      </div>
    </nav>
  );
}
