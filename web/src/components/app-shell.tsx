"use client";

import { useState } from "react";
import { Menu } from "lucide-react";
import { NavLinks } from "@/components/nav-links";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

const APP_NAME = "AI Visibility Audit";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="min-h-screen md:grid md:grid-cols-[14rem_1fr]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[60] focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <aside className="hidden border-r bg-card md:flex md:flex-col md:gap-6 md:p-4" aria-label="Sidebar">
        <p className="px-3 text-sm font-semibold">{APP_NAME}</p>
        <NavLinks />
        <div className="mt-auto">
          <ThemeToggle />
        </div>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex items-center justify-between gap-2 border-b bg-card px-4 py-2 md:hidden">
          <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" aria-label="Open menu">
                <Menu aria-hidden="true" />
              </Button>
            </SheetTrigger>
            <SheetContent>
              <SheetTitle className="px-3 text-sm font-semibold">{APP_NAME}</SheetTitle>
              <SheetDescription className="sr-only">Site navigation</SheetDescription>
              <NavLinks onNavigate={() => setMenuOpen(false)} />
            </SheetContent>
          </Sheet>
          <p className="text-sm font-semibold">{APP_NAME}</p>
          <ThemeToggle />
        </header>
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 p-4 md:p-8">
          {children}
        </main>
      </div>
    </div>
  );
}
