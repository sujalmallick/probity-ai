import { createContext, useContext } from "react";
import type { Me } from "./api";

export interface AuthState {
  user: Me | null;
  mode: "local" | "clerk";
  setUser: (u: Me | null) => void;
  signOut: () => void;
}
export const AuthCtx = createContext<AuthState>({ user: null, mode: "local", setUser: () => {}, signOut: () => {} });
export const useAuth = () => useContext(AuthCtx);
