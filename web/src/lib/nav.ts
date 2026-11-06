export interface NavItem {
  href: string;
  label: string;
}

const END_USER_ITEMS: readonly NavItem[] = [
  { href: "/", label: "Audits" },
  { href: "/sources", label: "Sources" },
  { href: "/review", label: "Review" },
];

/**
 * Evals compares prompt versions: a tool for the team, not for end users. It is left out of the
 * menu, and /evals answers 404, unless the app is built with NEXT_PUBLIC_SHOW_EVALS=1.
 */
export function evalsEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.NEXT_PUBLIC_SHOW_EVALS === "1";
}

export function navItems(env: Record<string, string | undefined> = process.env): readonly NavItem[] {
  return evalsEnabled(env) ? [...END_USER_ITEMS, { href: "/evals", label: "Evals" }] : END_USER_ITEMS;
}

export const NAV_ITEMS: readonly NavItem[] = navItems({ NEXT_PUBLIC_SHOW_EVALS: process.env.NEXT_PUBLIC_SHOW_EVALS });

/** Audits owns "/" and everything under /audits; the others own their own subtree. */
export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/" || pathname === "/audits" || pathname.startsWith("/audits/");
  return pathname === href || pathname.startsWith(`${href}/`);
}
