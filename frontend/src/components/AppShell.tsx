import {
  Activity,
  BookOpenCheck,
  Boxes,
  CheckSquare2,
  LayoutDashboard,
  Menu,
  Shield,
  X,
} from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { useApproval } from "../context/ApprovalContext";
import { useDemoPersona, type Persona } from "../context/DemoPersonaContext";
import { LogoMark } from "./LogoMark";

const WORK_NAVIGATION = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/assistant", label: "Policy & Evidence", icon: BookOpenCheck },
  { to: "/access-requests", label: "Access Requests", icon: Shield },
  { to: "/approvals", label: "Approvals", icon: CheckSquare2 },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { persona, demoMode, setPersona } = useDemoPersona();
  const { pending, outcome } = useApproval();

  return (
    <div className="app-shell">
      <header className="mobile-header">
        <div className="brand-lockup">
          <LogoMark />
          <span>OpenWeight</span>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen((value) => !value)}
        >
          {mobileOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
      </header>

      <aside className={`sidebar ${mobileOpen ? "sidebar--open" : ""}`}>
        <div className="sidebar__brand brand-lockup">
          <LogoMark />
          <div className="sidebar__brand-copy">
            <strong>OpenWeight</strong>
            <span>Enterprise LLM<br />governance platform</span>
          </div>
        </div>
        <nav className="primary-nav" aria-label="Primary navigation">
          <div className="primary-nav__work">
            {WORK_NAVIGATION.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `primary-nav__link ${isActive ? "primary-nav__link--active" : ""}`
                }
                onClick={() => setMobileOpen(false)}
              >
                <Icon aria-hidden="true" />
                <span>{label}</span>
                {label === "Approvals" && pending && !outcome ? (
                  <span className="nav-indicator" aria-label="One pending approval">1</span>
                ) : null}
              </NavLink>
            ))}
          </div>
          <div className="primary-nav__system">
            <NavLink
              to="/system"
              className={({ isActive }) =>
                `primary-nav__link primary-nav__link--system ${isActive ? "primary-nav__link--active" : ""}`
              }
              onClick={() => setMobileOpen(false)}
            >
              <Boxes aria-hidden="true" />
              <span>System</span>
            </NavLink>
          </div>
        </nav>
        <div className="sidebar__assurance">
          <span className="sidebar__assurance-icon"><Shield aria-hidden="true" /></span>
          <div>
            <strong>Human-controlled</strong>
            <span>Models propose. Policy and people decide.</span>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="topbar__signal">
            <Activity aria-hidden="true" />
            <span>Enterprise access governance</span>
          </div>
          <div className="topbar__controls">
            {demoMode ? (
              <label
                className="persona-control"
                title="Demo personas preview role-specific interface behavior. Backend authorization remains authoritative."
              >
                <span>Demo persona</span>
                <select
                  aria-label="Demo persona"
                  value={persona}
                  onChange={(event) => setPersona(event.target.value as Persona)}
                >
                  <option value="Reader">Reader</option>
                  <option value="Approver">Approver</option>
                  <option value="Operator">Operator</option>
                </select>
              </label>
            ) : (
              <span className="persona-chip">Reader experience</span>
            )}
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
