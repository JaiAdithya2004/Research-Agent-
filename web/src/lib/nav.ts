export interface NavItem {
  href: string;
  label: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/", label: "Audits" },
  { href: "/sources", label: "Sources" },
  { href: "/review", label: "Review" },
  { href: "/evals", label: "Evals" },
];

/** Audits owns "/" and everything under /audits; the others own their own subtree. */
export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/" || pathname === "/audits" || pathname.startsWith("/audits/");
  return pathname === href || pathname.startsWith(`${href}/`);
}
