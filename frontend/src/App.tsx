import { useEffect, useState } from "react";
import { useTheme } from "./theme";
import { ApiError, authRequest, type AccountProfile, type ConnectorProvider } from "./lib/api";
import AuthDialog from "./AuthDialog";
import SetupScreen from "./components/SetupScreen";
import Toaster from "./components/Toaster";
import Landing from "./screens/Landing";
import HowItWorks from "./screens/HowItWorks";
import Chat from "./screens/Chat";
import "./App.css";
import { AssistantConsent } from "./components/AssistantConsent";

// Signed out, "home" is the landing page; signed in, it is the chat.
type Screen = "home" | "how";

export default function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [account, setAccount] = useState<AccountProfile | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [signingOut, setSigningOut] = useState(false);
  // Back from Google (?google_signin=ready|error): reopen sign-in to finish the USC step.
  const [resumeGoogle, setResumeGoogle] = useState(() => ["ready", "error"].includes(new URLSearchParams(window.location.search).get("google_signin") || ""));
  const [showAuth, setShowAuth] = useState(resumeGoogle);
  const [notice, setNotice] = useState("");
  const [selectedConnectors, setSelectedConnectors] = useState<ConnectorProvider[]>([]);
  // After signing in, the backend connects sources in the background; show its progress.
  const [settingUp, setSettingUp] = useState(false);
  const { theme, toggleTheme, setTheme } = useTheme();
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
        if (active) setAccount(profile);
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
      setScreen("home");
      setShowAuth(true);
      setNotice("Your session has expired. Please sign in again.");
    };
    window.addEventListener("auth-expired", expireSession);
    return () => window.removeEventListener("auth-expired", expireSession);
  }, []);

  const openAuth = () => {
    if (checkingSession || signingOut) return;
    setShowAuth(true);
  };

  const signOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await authRequest<void>("logout", {});
      setSelectedConnectors([]);
      setAccount(null);
      setScreen("home");
      setNotice("");
      window.history.replaceState(null, "", "/"); // don't reopen this account's chat after the next sign-in
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
        <button type="button" className="underline" onClick={openAuth}>Sign in</button>
      </div>}
      {notice && <div role="status" className="flex items-center justify-between gap-4 border-b border-line bg-subtle px-6 py-3 text-sm text-fg">
        <span>{notice}</span>
        <button type="button" className="underline" onClick={() => setNotice("")}>Dismiss</button>
      </div>}
      {account && assistantRequest ? (
        <AssistantConsent key={account.id} request={assistantRequest} account={account} />
      ) : screen === "how" ? (
        <HowItWorks theme={theme} onToggleTheme={toggleTheme} signedIn={Boolean(account)}
          onBack={() => setScreen("home")}
          onStart={() => { setScreen("home"); if (!account) openAuth(); }} />
      ) : account ? (
        <Chat key={account.id} theme={theme} onToggleTheme={toggleTheme} onTheme={setTheme} account={account}
          selectedConnectors={selectedConnectors} onConnectorsChange={setSelectedConnectors}
          onHowItWorks={() => setScreen("how")}
          onSignOut={signOut} signingOut={signingOut} />
      ) : (
        <Landing theme={theme} onToggleTheme={toggleTheme} checkingSession={checkingSession}
          onSignIn={openAuth}
          onHowItWorks={() => setScreen("how")} />
      )}
      {showAuth && <AuthDialog resumeGoogle={resumeGoogle} onClose={() => { setShowAuth(false); setResumeGoogle(false); }}
        onSubmit={(profile, signInNotice) => {
          setSelectedConnectors([]);
          setAccount(profile);
          setShowAuth(false);
          setResumeGoogle(false);
          setNotice(signInNotice || "");
          setScreen("home");
          setSettingUp(true);
        }} />}
      {settingUp && <SetupScreen includeCalendar onDone={(connected) => {
        setSettingUp(false);
        setSelectedConnectors(connected);
      }} />}
      <Toaster />
    </>
  );
}
