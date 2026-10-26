import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { Badge, Button } from "./ui";

const MEMBER_LINKS = [
  { to: "/chat", label: "Chat" },
  { to: "/tools", label: "Tools" },
  { to: "/keys", label: "My API keys" },
];
const ADMIN_LINKS = [
  { to: "/servers", label: "MCP servers" },
  { to: "/team", label: "Team" },
  { to: "/permissions", label: "Permissions" },
  { to: "/audit", label: "Audit log" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  if (!user) return null;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>MCP Gateway</span>
        </div>
        <nav aria-label="Main">
          <p className="nav-heading">Workspace</p>
          {MEMBER_LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} className="nav-link">
              {link.label}
            </NavLink>
          ))}
          {user.role === "admin" ? (
            <>
              <p className="nav-heading">Administration</p>
              {ADMIN_LINKS.map((link) => (
                <NavLink key={link.to} to={link.to} className="nav-link">
                  {link.label}
                </NavLink>
              ))}
            </>
          ) : null}
        </nav>
        <div className="account">
          <div className="account-id" title={user.email}>
            <span className="account-email">{user.email}</span>
            <Badge tone={user.role === "admin" ? "blue" : "gray"}>{user.role}</Badge>
          </div>
          <Button variant="ghost" onClick={logout}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
