import { useEffect, useState } from "react";
import { useTheme } from "./theme";
import { ApiError, authRequest, type AccountProfile, type ConnectorProvider } from "./lib/api";
import AuthDialog from "./AuthDialog";
import SetupScreen from "./components/SetupScreen";
import Landing from "./screens/Landing";
import Connect from "./screens/Connect";
import Chat from "./screens/Chat";
import "./App.css";
import { AssistantConsent } from "./components/AssistantConsent";

type Screen = "landing" | "connect" | "chat";
type AuthMode = "login" | "register";

export default function App() {
  const [screen, setScreen] = useState<Screen>("landing");
  const [account, setAccount] = useState<AccountProfile | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [signingOut, setSigningOut] = useState(false);
  const [resumeGoogle, setResumeGoogle] = useState(() => ["ready", "error"].includes(new URLSearchParams(window.location.search).get("google_signin") || ""));
  const [showAuth, setShowAuth] = useState(resumeGoogle);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [afterAuth, setAfterAuth] = useState<Screen>(resumeGoogle ? "chat" : "connect");
  const [notice, setNotice] = useState("");
  const [selectedConnectors, setSelectedConnectors] = useState<ConnectorProvider[]>([]);
  // After Sign in with USC the backend connects sources in the background; show its progress.
  const [settingUp, setSettingUp] = useState(false);
  const [googleSetup, setGoogleSetup] = useState(false);
  const { theme, toggleTheme } = useTheme();
  const [assistantRequest] = useState(() => new URLSearchParams(window.location.search).get("mcp_request"));

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("google_signin")) {
      url.searchParams.delete("google_signin");
      window.history.replaceState(window.history.state, "", url);
    }
  }, []);

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
      setSelectedConnectors([]);
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
      setSelectedConnectors([]);
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
      {assistantRequest && !account && !checkingSession && <div className="flex items-center justify-between gap-4 border-b border-line bg-subtle px-6 py-3 text-sm text-fg">
        <span>Sign in to review your assistant's request to connect to TrojanClaw.</span>
        <button type="button" className="underline" onClick={() => { setAuthMode("login"); setShowAuth(true); }}>Sign in</button>
      </div>}
      {notice && <div role="status" className="flex items-center justify-between gap-4 border-b border-line bg-subtle px-6 py-3 text-sm text-fg">
        <span>{notice}</span>
        <button type="button" className="underline" onClick={() => setNotice("")}>Dismiss</button>
      </div>}
      {account && assistantRequest ? (
        <AssistantConsent key={account.id} request={assistantRequest} account={account} />
      ) : account && screen === "connect" ? (
        <Connect theme={theme} onToggleTheme={toggleTheme} account={account}
          selectedConnectors={selectedConnectors} onConnectorsChange={setSelectedConnectors}
          onBack={() => setScreen("landing")} onContinue={() => setScreen("chat")}
          onSignOut={signOut} signingOut={signingOut} />
      ) : account && screen === "chat" ? (
        <Chat key={account.id} theme={theme} onToggleTheme={toggleTheme} account={account}
          selectedConnectors={selectedConnectors} onConnectorsChange={setSelectedConnectors}
          onHome={() => setScreen("landing")} onConnect={() => setScreen("connect")}
          onSignOut={signOut} signingOut={signingOut} />
      ) : (
        <Landing theme={theme} onToggleTheme={toggleTheme} signedIn={Boolean(account)} checkingSession={checkingSession}
          onContinue={() => openScreen("connect")} onOpenChat={() => openScreen("chat")}
          onCreateAccount={() => openScreen("connect", "register")} />
      )}
      {showAuth && <AuthDialog key={authMode} initialMode={authMode} resumeGoogle={resumeGoogle} onClose={() => { setShowAuth(false); setResumeGoogle(false); }}
        onSubmit={(profile, viaUsc, signInNotice) => {
          setGoogleSetup(resumeGoogle);
          setSelectedConnectors([]);
          setAccount(profile);
          setShowAuth(false);
          setResumeGoogle(false);
          setNotice(signInNotice || "");
          setScreen(afterAuth);
          setSettingUp(viaUsc);
        }} />}
      {settingUp && <SetupScreen includeCalendar={googleSetup} onDone={(connected) => {
        setSettingUp(false);
        setSelectedConnectors(connected);
        setScreen("chat");
      }} />}
    </>
  );
}
