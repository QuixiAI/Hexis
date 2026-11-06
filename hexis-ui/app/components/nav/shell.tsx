"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ActivityBanner, BackgroundActivityProvider } from "./background-activity";
import { Sidebar } from "./sidebar";

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const hideNav = pathname.startsWith("/init");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  if (hideNav) return <>{children}</>;

  return (
    <BackgroundActivityProvider>
    <div className="min-h-screen bg-[var(--background)]">
      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center border-b border-[var(--outline)] bg-white px-4 lg:hidden">
        <button
          type="button"
          aria-label="Open navigation menu"
          aria-expanded={mobileNavOpen}
          onClick={() => setMobileNavOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--foreground)] hover:bg-[var(--surface-strong)]"
        >
          <Menu size={20} />
        </button>
        <span className="ml-3 text-sm font-semibold">Hexis</span>
      </header>

      {mobileNavOpen ? (
        <button
          type="button"
          aria-label="Close navigation menu"
          className="fixed inset-0 z-30 bg-black/35 lg:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      ) : null}

      <Sidebar open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <main className="min-h-screen pt-14 lg:ml-60 lg:pt-0">
        <ActivityBanner />
        {children}
      </main>
    </div>
    </BackgroundActivityProvider>
  );
}
