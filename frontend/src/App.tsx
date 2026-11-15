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

// Signed out, "home" is the landing page; signed in, it is the chat.
type Screen = "home" | "how";
type AuthMode = "login" | "register";

export default function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [account, setAccount] = useState<AccountProfile | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [signingOut, setSigningOut] = useState(false);
  const [resumeGoogle, setResumeGoogle] = useState(() => ["ready", "error"].includes(new URLSearchParams(window.location.search).get("google_signin") || ""));
  const [showAuth, setShowAuth] = useState(resumeGoogle);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [notice, setNotice] = useState("");
  const [selectedConnectors, setSelectedConnectors] = useState<ConnectorProvider[]>([]);
  // After Sign in with USC the backend connects sources in the background; show its progress.
  const [settingUp, setSettingUp] = useState(false);
  const [googleSetup, setGoogleSetup] = useState(false);
  const { theme, toggleTheme, setTheme } = useTheme();

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
      setAuthMode("login");
      setShowAuth(true);
      setNotice("Your session has expired. Please sign in again.");
    };
    window.addEventListener("auth-expired", expireSession);
    return () => window.removeEventListener("auth-expired", expireSession);
  }, []);

  const openAuth = (mode: AuthMode = "login") => {
    if (checkingSession || signingOut) return;
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
      {notice && <div role="status" className="flex items-center justify-between gap-4 border-b border-line bg-subtle px-6 py-3 text-sm text-fg">
        <span>{notice}</span>
        <button type="button" className="underline" onClick={() => setNotice("")}>Dismiss</button>
      </div>}
      {screen === "how" ? (
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
          onSignIn={() => openAuth()} onCreateAccount={() => openAuth("register")}
          onHowItWorks={() => setScreen("how")} />
      )}
      {showAuth && <AuthDialog key={authMode} initialMode={authMode} resumeGoogle={resumeGoogle} onClose={() => { setShowAuth(false); setResumeGoogle(false); }}
        onSubmit={(profile, viaUsc, signInNotice) => {
          setGoogleSetup(resumeGoogle);
          setSelectedConnectors([]);
          setAccount(profile);
          setShowAuth(false);
          setResumeGoogle(false);
          setNotice(signInNotice || "");
          setScreen("home");
          setSettingUp(viaUsc);
        }} />}
      {settingUp && <SetupScreen includeCalendar={googleSetup} onDone={(connected) => {
        setSettingUp(false);
        setSelectedConnectors(connected);
      }} />}
      <Toaster />
    </>
  );
}
