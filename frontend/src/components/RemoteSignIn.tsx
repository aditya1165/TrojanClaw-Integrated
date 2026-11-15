import { useEffect, useRef, useState } from "react";
import type { ConnectorProvider, RemoteLogin } from "../lib/api";

export function RemoteSignIn({ provider, login, busy, error, onFinish, onCancel, onSkip, heading, description, progress, automatic = false }: {
  provider: ConnectorProvider;
  login: RemoteLogin;
  busy: boolean;
  error: string | null;
  onFinish: () => void;
  onCancel: () => void;
  onSkip?: () => void;
  heading?: string;
  description?: string;
  progress?: string | null;
  automatic?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [fullScreen, setFullScreen] = useState(false);
  const [displayError, setDisplayError] = useState<string | null>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  useEffect(() => {
    const sync = () => setFullScreen(document.fullscreenElement === dialog.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  const toggleFullScreen = async () => {
    try {
      setDisplayError(null);
      if (document.fullscreenElement) await document.exitFullscreen();
      else await dialog.current?.requestFullscreen();
    } catch {
      setDisplayError("Full screen isn't available in this browser. The expanded sign-in window is still available.");
    }
  };

  return <dialog className="remote-signin" ref={dialog} aria-labelledby="remote-signin-title"
    onCancel={(event) => { event.preventDefault(); if (!busy) onCancel(); }}>
    <header><div>
      <h2 id="remote-signin-title">{heading ?? `Connect ${{ brightspace: "Brightspace", handshake: "Handshake", piazza: "Piazza" }[provider]}`}</h2>
      <p>{description ?? (automatic ? "Complete school sign-in and verification. Your connections will be saved automatically." : "Sign in below and complete any school verification. Then select Finish sign-in.")}</p>
    </div>
      {document.fullscreenEnabled && <button type="button" className="remote-fullscreen" onClick={() => void toggleFullScreen()}>
        {fullScreen ? "Exit full screen" : "Full screen"}
      </button>}
    </header>
    {progress && <div role="status" className="remote-signin-progress"><span className="h-6 w-6 animate-spin rounded-full border-2 border-line border-t-accent-fg" aria-hidden="true" /><p>{progress}</p></div>}
    <iframe hidden={!!progress} title={`${provider} secure remote sign-in`} src={login.live_url}
      referrerPolicy="no-referrer" sandbox="allow-same-origin allow-scripts allow-forms"
      allow="clipboard-read; clipboard-write; fullscreen" />
    {displayError && <p role="status">{displayError}</p>}
    {error && <p className="auth-error" role="alert">{error}</p>}
    <footer><span>This private remote browser expires after ten minutes. Your connection is saved only to your account.</span>
      <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      {onSkip && <button type="button" disabled={busy} onClick={onSkip}>Skip Handshake</button>}
      {(!automatic || error) && <button type="button" disabled={busy} onClick={onFinish}>{busy ? "Checking…" : automatic ? "Retry" : "Finish sign-in"}</button>}
    </footer>
  </dialog>;
}
