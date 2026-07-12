import { NavLink, Outlet } from "react-router-dom";
const links = [
  ["/", "Dashboard"],
  ["/projects", "Projects"],
  ["/jobs", "Jobs"],
  ["/providers", "Providers"],
  ["/renders", "Renders"],
  ["/settings", "Settings"],
];
export function Layout() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-700 bg-slate-950">
        <div className="mx-auto flex max-w-7xl items-center gap-8 p-4">
          <span className="text-xl font-black text-accent">
            FlipThisVideoMaker
          </span>
          <nav className="flex gap-2" aria-label="Main navigation">
            {links.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `rounded px-3 py-2 ${isActive ? "bg-slate-700" : "hover:bg-slate-800"}`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl p-6">
        <Outlet />
      </main>
    </div>
  );
}
