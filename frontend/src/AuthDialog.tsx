import { useEffect, useRef, useState } from "react";
import { authRequest, type AccountProfile, type UscLoginStatus } from "./lib/api";
import { CloseIcon } from "./components/ui";

interface AuthDialogProps {
  onClose: () => void;
  onSubmit: (profile: AccountProfile, notice?: string) => void;
  /** Back from Google: finish with the USC login, which connects Brightspace, Handshake and WebReg. */
  resumeGoogle?: boolean;
}

/**
 * Sign in: Continue with Google (in this browser), then one USC login in a small window that
 * the backend opens (NetID + Duo). That window connects Brightspace, Handshake and WebReg
 * and closes by itself; this dialog waits for the backend to say it is done.
 */
export default function AuthDialog({ onClose, onSubmit, resumeGoogle = false }: AuthDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [waiting, setWaiting] = useState(resumeGoogle); // the USC step after Google
  const [error, setError] = useState("");
  const attemptRef = useRef<string | null>(null);
  const mounted = useRef(false);
  const resumed = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => {
      mounted.current = false;
      attemptRef.current = null; // stop polling if the dialog closes
      dialog?.close();
    };
  }, []);

  useEffect(() => {
    let active = true;
    authRequest<{ configured: boolean }>("google/config")
      .then((result) => { if (active) setConfigured(result.configured); })
      .catch(() => { if (active) setConfigured(false); });
    return () => { active = false; };
  }, []);

  // Ready for Enter as soon as the button can be used.
  useEffect(() => {
    if (configured) buttonRef.current?.focus();
  }, [configured]);

  const continueWithGoogle = async () => {
    setStarting(true);
    setError("");
    try {
      const { url } = await authRequest<{ url: string }>("google/start", {});
      window.location.assign(url); // Google signs in here, in the student's own browser
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Couldn't open Google sign-in.");
      setStarting(false);
    }
  };

  // Back from Google: the backend opens the USC login window; poll until it finishes.
  const finishWithUsc = async () => {
    setError("");
    setWaiting(true);
    try {
      const position = {  // centre the 520×720 window over this one
        left: Math.round(window.screenX + (window.outerWidth - 520) / 2),
        top: Math.round(window.screenY + (window.outerHeight - 720) / 2),
      };
      const { attempt_id } = await authRequest<{ attempt_id: string }>("google/finish", position);
      if (!mounted.current) return;
      attemptRef.current = attempt_id;
      while (attemptRef.current === attempt_id) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (attemptRef.current !== attempt_id) return;
        const result = await authRequest<UscLoginStatus>("google/status", { attempt_id });
        if (!mounted.current || attemptRef.current !== attempt_id) return;
        if (result.status === "done" && result.account) {
          onSubmit(result.account, result.detail || undefined);
          return;
        }
        if (result.status === "failed") throw new Error(result.detail ?? "The USC login did not finish. Please try again.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Couldn't finish signing in. Please try again.");
    } finally {
      attemptRef.current = null;
      if (mounted.current) setWaiting(false);
    }
  };

  useEffect(() => {
    if (resumeGoogle && !resumed.current) {
      resumed.current = true;
      void finishWithUsc();
    }
  }, [resumeGoogle]); // eslint-disable-line react-hooks/exhaustive-deps

  const cancel = () => {
    attemptRef.current = null;
    setWaiting(false);
    void authRequest<void>("google/cancel", {}).catch(() => {});
  };

  return (
    <dialog ref={dialogRef} className="auth-dialog" aria-labelledby="auth-title"
      onCancel={(event) => { if (waiting) event.preventDefault(); else onClose(); }}>
      <button type="button" className="auth-close" aria-label="Close" onClick={onClose} disabled={waiting}>
        <CloseIcon />
      </button>

      <div className="flex flex-col items-center gap-6 text-center">
        <img src="/logo.png" alt="" className="h-14 w-14 object-contain" />
        <div className="flex flex-col gap-2">
          <h2 id="auth-title" className="text-2xl font-semibold tracking-[-0.03em]">Welcome to TrojanClaw</h2>
          <p className="text-sm text-muted">Sign in with your USC Google account.</p>
        </div>

        {waiting ? (
          <div role="status" className="flex w-full flex-col items-center gap-3 rounded-2xl bg-subtle px-5 py-5">
            <span className="h-5 w-5 animate-spin rounded-full border-2 border-line border-t-accent-fg" aria-hidden="true" />
            <p className="text-sm font-medium">One more step: your USC login</p>
            <p className="text-xs leading-relaxed text-muted">
              A small USC window opened (check behind this one). Log in with your NetID and approve Duo; it connects Brightspace, Handshake and WebReg, then closes by itself.
            </p>
            <button type="button" className="text-xs font-medium text-muted underline hover:text-fg" onClick={cancel}>Cancel</button>
          </div>
        ) : (
          <button ref={buttonRef} type="button" onClick={() => void continueWithGoogle()} disabled={!configured || starting}
            className="flex min-h-12 w-full items-center justify-center gap-3 rounded-full border border-line bg-surface px-5 text-[15px] font-medium text-fg shadow-sm transition hover:bg-subtle hover:shadow disabled:cursor-not-allowed disabled:opacity-60">
            {starting
              ? <span className="h-[18px] w-[18px] animate-spin rounded-full border-2 border-line border-t-fg" aria-hidden="true" />
              : <GoogleLogo />}
            {starting ? "Opening Google…" : "Continue with Google"}
          </button>
        )}

        {error && <p role="alert" className="text-sm text-error">{error}</p>}
        {configured === false && !error && (
          <p className="text-xs text-muted">Google sign-in isn't set up on this server yet.</p>
        )}
        {!waiting && configured !== false && (
          <p className="text-xs leading-relaxed text-muted">Use your @usc.edu account. Next, USC asks you to log in once.</p>
        )}
      </div>
    </dialog>
  );
}

function GoogleLogo() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.6-.4-3.5z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.5z" />
    </svg>
  );
}
