import { useEffect, useState } from "react";
import { useTheme } from "./theme";
import { ApiError, authRequest, type AccountProfile } from "./lib/api";
import AuthDialog from "./AuthDialog";
import Landing from "./screens/Landing";
import Connect from "./screens/Connect";
import Chat from "./screens/Chat";
import "./App.css";

type Screen = "landing" | "connect" | "chat";
type AuthMode = "login" | "register";

export default function App() {
  const [screen, setScreen] = useState<Screen>("landing");
  const [account, setAccount] = useState<AccountProfile | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [signingOut, setSigningOut] = useState(false);
  const [showAuth, setShowAuth] = useState(false);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [afterAuth, setAfterAuth] = useState<Screen>("connect");
  const [notice, setNotice] = useState("");
  const { theme, toggleTheme } = useTheme();

  useEffect(() => {
    let active = true;
    authRequest<AccountProfile>("me")
      .then((profile) => {
        if (active) { setAccount(profile); setScreen("chat"); }
      })
      .catch((error) => {
        if (active && !(error instanceof ApiError && error.status === 401)) {
          setNotice("Could not restore your session. Please try signing in.");
        }
      })
      .finally(() => { if (active) setCheckingSession(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const expireSession = () => {
      setAccount(null);
      setAfterAuth(screen === "connect" ? "connect" : "chat");
      setScreen("landing");
      setAuthMode("login");
      setShowAuth(true);
      setNotice("Your session has expired. Please sign in again.");
    };
    window.addEventListener("auth-expired", expireSession);
    return () => window.removeEventListener("auth-expired", expireSession);
  }, [screen]);

  const openScreen = (destination: Screen, mode: AuthMode = "login") => {
    if (checkingSession || signingOut) return;
    if (account) { setScreen(destination); return; }
    setAfterAuth(destination);
    setAuthMode(mode);
    setShowAuth(true);
  };

  const signOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await authRequest<void>("logout", {});
      setAccount(null);
      setScreen("landing");
      setNotice("");
    } catch {
      setNotice("Could not sign out. Please try again.");
    } finally {
      setSigningOut(false);
    }
  };

  return (
    <>
      {notice && <div role="status" className="flex items-center justify-between gap-4 border-b border-line bg-subtle px-6 py-3 text-sm text-fg">
        <span>{notice}</span>
        <button type="button" className="underline" onClick={() => setNotice("")}>Dismiss</button>
      </div>}
      {account && screen === "connect" ? (
        <Connect theme={theme} onToggleTheme={toggleTheme} account={account}
          onBack={() => setScreen("landing")} onContinue={() => setScreen("chat")}
          onSignOut={signOut} signingOut={signingOut} />
      ) : account && screen === "chat" ? (
        <Chat key={account.id} theme={theme} onToggleTheme={toggleTheme} account={account}
          onHome={() => setScreen("landing")} onConnect={() => setScreen("connect")}
          onSignOut={signOut} signingOut={signingOut} />
      ) : (
        <Landing theme={theme} onToggleTheme={toggleTheme} signedIn={Boolean(account)} checkingSession={checkingSession}
          onContinue={() => openScreen("connect")} onOpenChat={() => openScreen("chat")}
          onCreateAccount={() => openScreen("connect", "register")} />
      )}
      {showAuth && <AuthDialog key={authMode} initialMode={authMode} onClose={() => setShowAuth(false)}
        onSubmit={(profile) => {
          setAccount(profile);
          setShowAuth(false);
          setNotice("");
          setScreen(afterAuth);
        }} />}
    </>
  );
}
