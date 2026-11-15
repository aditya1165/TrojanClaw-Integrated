import { useEffect, useRef, useState, type FormEvent } from "react";

interface AuthDialogProps {
  onClose: () => void;
  onSubmit: (profile: { name: string; username: string }) => void;
}

export default function AuthDialog({ onClose, onSubmit }: AuthDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [isCreatingAccount, setIsCreatingAccount] = useState(false);
  const [passwordError, setPasswordError] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (isCreatingAccount && data.get("password") !== data.get("confirmPassword")) {
      setPasswordError("Passwords do not match.");
      return;
    }
    onSubmit({
      name: String(data.get("name") || "").trim(),
      username: String(data.get("username") || "").trim(),
    });
  };

  return (
    <dialog ref={dialogRef} className="auth-dialog" aria-labelledby="auth-title" onCancel={onClose}>
      <button type="button" className="auth-close" aria-label="Close sign-in" onClick={onClose}>
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
            <input id="auth-name" name="name" autoComplete="name" placeholder="Your name" pattern=".*\S.*" required />
          </label>
        )}
        <label htmlFor="auth-username">Username
          <input id="auth-username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} placeholder="Your username" pattern="\S+" required />
        </label>
        <label htmlFor="auth-password">Password
          <input id="auth-password" name="password" type="password" autoComplete={isCreatingAccount ? "new-password" : "current-password"} placeholder="Your password" required onChange={() => setPasswordError("")} />
        </label>
        {isCreatingAccount && (
          <label htmlFor="auth-confirm-password">Confirm password
            <input id="auth-confirm-password" name="confirmPassword" type="password" autoComplete="new-password" placeholder="Re-enter your password" required aria-invalid={Boolean(passwordError)} aria-describedby={passwordError ? "password-error" : undefined} onChange={() => setPasswordError("")} />
          </label>
        )}
        {passwordError && <p id="password-error" className="auth-error" role="alert">{passwordError}</p>}
        <button className="auth-submit" type="submit">{isCreatingAccount ? "Create account" : "Sign in"}</button>
      </form>
      <p className="auth-switch">
        {isCreatingAccount ? "Already have an account?" : "New to TrojanClaw?"}{" "}
        <button type="button" onClick={() => {
          setIsCreatingAccount(!isCreatingAccount);
          setPasswordError("");
        }}>{isCreatingAccount ? "Sign in" : "Create account"}</button>
      </p>
    </dialog>
  );
}
