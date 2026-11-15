import { useEffect, useRef, useState, type FormEvent } from "react";
import { authRequest, type AccountProfile } from "./lib/api";

interface AuthDialogProps {
  onClose: () => void;
  onSubmit: (profile: AccountProfile) => void;
}

export default function AuthDialog({ onClose, onSubmit }: AuthDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [isCreatingAccount, setIsCreatingAccount] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting) return;
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
      onSubmit(profile);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Could not sign in. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <dialog ref={dialogRef} className="auth-dialog" aria-labelledby="auth-title" onCancel={(event) => { if (isSubmitting) event.preventDefault(); else onClose(); }}>
      <button type="button" disabled={isSubmitting} className="auth-close" aria-label="Close sign-in" onClick={onClose}>
        <span className="material-symbols-outlined" aria-hidden="true">close</span>
      </button>
      <div className="auth-brand">
        <img src="/logo.png" alt="" />
        <span>TrojanClaw</span>
      </div>
      <h2 id="auth-title">{isCreatingAccount ? "Create your account" : "Welcome back"}</h2>
      <p className="auth-description">
        {isCreatingAccount ? "A little less planning. A little more doing." : "Sign in to your academic copilot."}
      </p>
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
        <button className="auth-submit" type="submit" disabled={isSubmitting}>{isSubmitting ? "Please wait..." : isCreatingAccount ? "Create account" : "Sign in"}</button>
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
