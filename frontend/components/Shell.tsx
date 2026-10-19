"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { User } from "@/lib/types";

const NAV: { href: string; label: string }[] = [
  { href: "/", label: "Overview" },
  { href: "/sources", label: "Source adapters" },
  { href: "/icp", label: "ICP editor" },
  { href: "/accounts", label: "Accounts & scoring" },
  { href: "/contacts", label: "Contacts" },
  { href: "/campaigns", label: "Campaigns" },
  { href: "/approvals", label: "Approvals" },
  { href: "/replies", label: "Reply triage" },
  { href: "/analytics", label: "Analytics" },
  { href: "/workflows", label: "Workflows" },
  { href: "/agents", label: "Agents" },
  { href: "/governance", label: "Governance" },
  { href: "/audit", label: "Audit log" },
];

const REPO_URL = "https://github.com/Dash-boop0/autonomous-b2b-leadgen-engine";
const HOME_URL = "https://dash-boop0.github.io/";

function StopControl() {
  const { system, can, refreshSystem } = useAuth();
  const [busy, setBusy] = useState(false);
  if (!system) return null;
  const on = system.emergency_stop;
  const allowed = can("system.stop");
  return (
    <button
      className={`btn btn-stop${on ? " is-on" : ""}`}
      disabled={!allowed || busy}
      title={allowed ? (on ? "Lift the emergency stop" : "Halt all workflows and sending") : "Only admins can toggle the emergency stop"}
      onClick={async () => {
        const reason = on ? null : window.prompt("Reason for the emergency stop?", "Manual stop");
        if (!on && !reason) return;
        setBusy(true);
        try {
          await api.post("/api/system/stop", { on: !on, reason });
          await refreshSystem();
        } catch (e) {
          window.alert(e instanceof Error ? e.message : String(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      <span className="stop-dot" aria-hidden />
      {on ? "STOPPED · resume" : "STOP"}
    </button>
  );
}

function RoleSwitcher() {
  const { user, login } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  useEffect(() => {
    api.get<User[]>("/api/auth/demo-users").then(setUsers).catch(() => setUsers([]));
  }, []);
  if (!user) return null;
  return (
    <label className="role-switch">
      <span className="sr-only">Signed in as (demo role switcher)</span>
      <select
        value={user.username}
        onChange={async (e) => {
          await login(e.target.value, "demo");
          window.location.reload();
        }}
        aria-label="Switch demo user"
      >
        {users.map((u) => (
          <option key={u.id} value={u.username}>
            {u.display_name} · {u.role}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, system, loading, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const isLogin = pathname === "/login";

  useEffect(() => {
    if (!loading && !user && !isLogin) router.replace("/login");
  }, [loading, user, isLogin, router]);

  useEffect(() => setMenuOpen(false), [pathname]);

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <button className="btn btn-ghost menu-toggle" aria-expanded={menuOpen} aria-controls="sidenav" onClick={() => setMenuOpen((o) => !o)}>
            Menu
          </button>
          <Link href="/" className="brand">
            <span className="brand-mark" aria-hidden>
              LG
            </span>
            <span className="brand-name">Lead-gen Engine</span>
          </Link>
          <span className="badge badge-warn mode-badge" title="No real email is ever sent; all providers are mocks">
            Dry-run · Mock providers
          </span>
          <div className="topbar-right">
            {system && system.sim_clock_offset_days > 0 && (
              <span className="badge badge-neutral" title="Simulated clock offset used by the dry-run sender">
                Sim clock +{system.sim_clock_offset_days}d
              </span>
            )}
            {user && <StopControl />}
            <RoleSwitcher />
            {user && (
              <button className="btn btn-ghost btn-sm" onClick={logout}>
                Sign out
              </button>
            )}
            <a className="btn btn-ghost btn-sm" href={HOME_URL}>
              All projects
            </a>
          </div>
        </div>
      </header>
      <div className="banner" role="note">
        <strong>Synthetic demo.</strong> Every company, person and outcome here is fictional (example.* domains). Providers are mocked, sending is
        dry-run only, and reply events are simulated.
        {system?.emergency_stop && (
          <span className="banner-stop">
            {" "}
            Emergency stop is ON: workflows and sending are halted ({system.stop_reason}).
          </span>
        )}
      </div>
      <div className="layout">
        {!isLogin && (
          <nav id="sidenav" className={`sidenav${menuOpen ? " open" : ""}`} aria-label="Main">
            <ul>
              {NAV.map((n) => (
                <li key={n.href}>
                  <Link href={n.href} aria-current={pathname === n.href ? "page" : undefined}>
                    {n.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}
        <main id="main" className="main" tabIndex={-1}>
          {loading && !isLogin ? <div className="state">Loading…</div> : !user && !isLogin ? null : children}
        </main>
      </div>
      <footer className="footer">
        <a href={REPO_URL}>Source on GitHub</a> · MIT · Synthetic data
      </footer>
    </>
  );
}
