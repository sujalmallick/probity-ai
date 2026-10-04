import { createContext, useContext } from "react";
import type { Me } from "./api";

export interface AuthState {
  user: Me | null;
  setUser: (u: Me | null) => void;
}
export const AuthCtx = createContext<AuthState>({ user: null, setUser: () => {} });
export const useAuth = () => useContext(AuthCtx);
