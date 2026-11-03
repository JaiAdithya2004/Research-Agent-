"use client";

import { useCallback, useSyncExternalStore } from "react";
import {
  THEME_CHANGE_EVENT,
  THEME_STORAGE_KEY,
  readPreference,
  savePreference,
  type ThemePreference,
} from "@/lib/theme";

function subscribe(onChange: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_STORAGE_KEY || e.key === null) onChange();
  };
  window.addEventListener(THEME_CHANGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onMedia = () => {
    if (readPreference() === "system") savePreference("system");
  };
  media.addEventListener("change", onMedia);
  return () => {
    window.removeEventListener(THEME_CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
    media.removeEventListener("change", onMedia);
  };
}

export function useTheme(): { preference: ThemePreference; setPreference: (p: ThemePreference) => void } {
  const preference = useSyncExternalStore<ThemePreference>(subscribe, readPreference, () => "system");
  const setPreference = useCallback((p: ThemePreference) => savePreference(p), []);
  return { preference, setPreference };
}
