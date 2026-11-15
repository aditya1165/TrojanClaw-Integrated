import { useEffect, useRef, useState, type FormEvent } from "react";
import { authRequest, type AccountProfile, type UscLoginStatus } from "./lib/api";
import { CloseIcon } from "./components/ui";

interface AuthDialogProps {
  onClose: () => void;
  onSubmit: (profile: AccountProfile, viaUsc: boolean, notice?: string) => void;
  initialMode?: "login" | "register";
  resumeGoogle?: boolean;
}

export default function AuthDialog({ onClose, onSubmit, initialMode = "login", resumeGoogle = false }: AuthDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [isCreatingAccount, setIsCreatingAccount] = useState(initialMode === "register");
  const [passwordError, setPasswordError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [uscWaiting, setUscWaiting] = useState(false);
  const [uscError, setUscError] = useState("");
  const uscAttemptRef = useRef<string | null>(null);
  const [googleConfigured, setGoogleConfigured] = useState(false);
  const [googleStarting, setGoogleStarting] = useState(false);
  const [googleResuming, setGoogleResuming] = useState(resumeGoogle);
  const resumeStarted = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => {
      mounted.current = false;
      uscAttemptRef.current = null; // stop polling if the dialog closes
      dialog?.close();
    };
  }, []);

  // Put the typing cursor in the first field, so you can type without clicking.
  // (showModal() would otherwise focus the dialog's first button.)
  useEffect(() => {
    if (resumeGoogle) return;
    dialogRef.current?.querySelector<HTMLInputElement>(isCreatingAccount ? "#auth-name" : "#auth-username")?.focus();
  }, [isCreatingAccount, resumeGoogle]);

  useEffect(() => {
    let active = true;
    authRequest<{ configured: boolean }>("google/config")
      .then((result) => { if (active) setGoogleConfigured(result.configured); })
      .catch(() => { if (active) setGoogleConfigured(false); });
    return () => { active = false; };
  }, []);

  const signInWithGoogle = async () => {
    setGoogleStarting(true);
    setUscError("");
    try {
      const { url } = await authRequest<{ url: string }>("google/start", {});
      window.location.assign(url);
    } catch (reason) {
      setUscError(reason instanceof Error ? reason.message : "Could not start Google sign-in.");
      setGoogleStarting(false);
    }
  };

  // Sign in with USC: the backend opens a Chrome window for the USC login; poll until it finishes.
  const signInWithUsc = async (fromGoogle = false) => {
    setSubmitError("");
    setUscError("");
    setUscWaiting(true);
    try {
      // Center the 520×720 sign-in popup over this browser window.
      const position = {
        left: Math.round(window.screenX + (window.outerWidth - 520) / 2),
        top: Math.round(window.screenY + (window.outerHeight - 720) / 2),
      };
      const { attempt_id } = await authRequest<{ attempt_id: string }>(fromGoogle ? "google/finish" : "usc/start", position);
      if (!mounted.current) return;
      uscAttemptRef.current = attempt_id;
      while (uscAttemptRef.current === attempt_id) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        if (uscAttemptRef.current !== attempt_id) return;
        const result = await authRequest<UscLoginStatus>(fromGoogle ? "google/status" : "usc/status", { attempt_id });
        if (!mounted.current || uscAttemptRef.current !== attempt_id) return;
        if (result.status === "done" && result.account) {
          onSubmit(result.account, true, result.detail || undefined);
          return;
        }
        if (result.status === "failed") throw new Error(result.detail ?? "The USC login did not finish.");
      }
    } catch (error) {
      setUscError(error instanceof Error ? error.message : "Could not sign in with USC. Please try again.");
    } finally {
      uscAttemptRef.current = null;
      setUscWaiting(false);
      setGoogleResuming(false);
    }
  };

  useEffect(() => {
    if (resumeGoogle && !resumeStarted.current) {
      resumeStarted.current = true;
      void signInWithUsc(true);
    }
  }, [resumeGoogle]); // eslint-disable-line react-hooks/exhaustive-deps

  const cancelUsc = () => {
    uscAttemptRef.current = null;
    setUscWaiting(false);
    if (googleResuming) void authRequest<void>("google/cancel", {}).catch(() => {});
    setGoogleResuming(false);
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || uscWaiting || googleStarting) return;
    const data = new FormData(event.currentTarget);
    if (isCreatingAccount && data.get("password") !== data.get("confirmPassword")) {
      setPasswordError("Passwords do not match.");
      return;
    }
    setSubmitError("");
    setIsSubmitting(true);
    try {
      const credentials = {
        username: String(data.get("username") || "").trim(),
        password: String(data.get("password") || ""),
      };
      const profile = await authRequest<AccountProfile>(isCreatingAccount ? "register" : "login", {
        ...credentials,
        ...(isCreatingAccount ? {
          name: String(data.get("name") || "").trim(),
          confirm_password: String(data.get("confirmPassword") || ""),
        } : {}),
      });
      onSubmit(profile, false);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Could not sign in. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <dialog ref={dialogRef} className="auth-dialog" aria-labelledby="auth-title" onCancel={(event) => { if (isSubmitting) event.preventDefault(); else onClose(); }}>
      <button type="button" disabled={isSubmitting} className="auth-close" aria-label="Close sign-in" onClick={onClose}>
        <CloseIcon />
      </button>
      <div className="auth-brand">
        <img src="/logo.png" alt="" />
        <span>TrojanClaw</span>
      </div>
      <h2 id="auth-title">{isCreatingAccount ? "Create your account" : "Welcome back"}</h2>
      <p className="auth-description">
        {isCreatingAccount ? "A little less planning. A little more doing." : "Sign in to your academic copilot."}
      </p>
      <div className="auth-usc">
        <button type="button" className="auth-submit" onClick={signInWithGoogle} disabled={!googleConfigured || isSubmitting || uscWaiting || googleStarting}>
          {googleStarting ? "Opening Google sign-in…" : "Continue with USC Google"}
        </button>
        <p className="auth-description">{googleConfigured ? "Connect Google Calendar, then finish connecting Brightspace. USC may ask you to sign in and approve Duo." : "Google sign-in is not available yet. You can sign in with USC below."}</p>
        <button type="button" className="auth-submit" onClick={() => signInWithUsc()} disabled={isSubmitting || uscWaiting || googleStarting}>
          {uscWaiting ? "Waiting for your USC login..." : "Sign in with USC"}
        </button>
        {uscWaiting && (
          <p className="auth-description" role="status">
            {googleResuming ? "Google sign-in finished. Complete USC sign-in in the new window to connect Brightspace using the same USC account." : "Log in and approve Duo in the USC sign-in window."}{" "}
            <button type="button" className="auth-link" onClick={cancelUsc}>Cancel</button>
          </p>
        )}
        {uscError && <p className="auth-error" role="alert">{uscError}</p>}
        <p className="auth-divider"><span>or use a TrojanClaw username</span></p>
      </div>
      <form key={String(isCreatingAccount)} className="auth-form" onSubmit={handleSubmit}>
        {isCreatingAccount && (
          <label htmlFor="auth-name">Name
            <input id="auth-name" name="name" autoComplete="name" placeholder="Your name" pattern=".*\S.*" maxLength={100} required />
          </label>
        )}
        <label htmlFor="auth-username">Username
          <input id="auth-username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Your username" pattern="[a-zA-Z0-9_]{3,32}" title="3–32 letters, numbers, or underscores" minLength={3} maxLength={32} required />
        </label>
        <label htmlFor="auth-password">Password
          <input id="auth-password" name="password" type="password" autoComplete={isCreatingAccount ? "new-password" : "current-password"} placeholder={isCreatingAccount ? "At least 8 characters" : "Your password"} minLength={isCreatingAccount ? 8 : 1} maxLength={128} required onChange={() => setPasswordError("")} />
        </label>
        {isCreatingAccount && (
          <label htmlFor="auth-confirm-password">Confirm password
            <input id="auth-confirm-password" name="confirmPassword" type="password" autoComplete="new-password" placeholder="Re-enter your password" required aria-invalid={Boolean(passwordError)} aria-describedby={passwordError ? "password-error" : undefined} onChange={() => setPasswordError("")} />
          </label>
        )}
        {passwordError && <p id="password-error" className="auth-error" role="alert">{passwordError}</p>}
        {submitError && <p className="auth-error" role="alert">{submitError}</p>}
        <button className="auth-submit" type="submit" disabled={isSubmitting || uscWaiting}>{isSubmitting ? "Please wait..." : isCreatingAccount ? "Create account" : "Sign in"}</button>
      </form>
      <p className="auth-switch">
        {isCreatingAccount ? "Already have an account?" : "New to TrojanClaw?"}{" "}
        <button type="button" disabled={isSubmitting} onClick={() => {
          setIsCreatingAccount(!isCreatingAccount);
          setPasswordError("");
          setSubmitError("");
        }}>{isCreatingAccount ? "Sign in" : "Create account"}</button>
      </p>
    </dialog>
  );
}
