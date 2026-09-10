import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type Persona = "Reader" | "Approver" | "Operator";
export type Capability = "query" | "propose" | "approve";

const CAPABILITIES: Record<Persona, ReadonlySet<Capability>> = {
  Reader: new Set(["query"]),
  Approver: new Set(["query", "approve"]),
  Operator: new Set(["query", "propose", "approve"]),
};

interface DemoPersonaContextValue {
  persona: Persona;
  demoMode: boolean;
  setPersona: (persona: Persona) => void;
  can: (capability: Capability) => boolean;
}

const DemoPersonaContext = createContext<DemoPersonaContextValue | null>(null);

export function DemoPersonaProvider({ children }: { children: ReactNode }) {
  const demoMode = import.meta.env.VITE_DEMO_MODE === "true";
  const [persona, setPersona] = useState<Persona>(demoMode ? "Operator" : "Reader");
  const value = useMemo<DemoPersonaContextValue>(
    () => ({
      persona,
      demoMode,
      setPersona,
      can: (capability) => CAPABILITIES[persona].has(capability),
    }),
    [demoMode, persona],
  );

  return (
    <DemoPersonaContext.Provider value={value}>
      {children}
    </DemoPersonaContext.Provider>
  );
}

export function useDemoPersona(): DemoPersonaContextValue {
  const value = useContext(DemoPersonaContext);
  if (!value) throw new Error("useDemoPersona must be used within its provider");
  return value;
}
