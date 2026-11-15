import {useEffect, useState} from 'react';
import {apiRequest} from '../lib/api';
import {Button} from './ui';

export function MobilePairing() {
  const [code, setCode] = useState('');
  const [expires, setExpires] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!expires) return;
    const timer = window.setTimeout(() => { setCode(''); setExpires(0); }, Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timer);
  }, [expires]);
  const pair = async () => {
    setBusy(true); setError('');
    try {
      const result = await apiRequest<{code:string; expires_in:number}>('/mobile/pairing', {});
      setCode(result.code); setExpires(Date.now() + result.expires_in * 1000);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not create a pairing code.'); }
    finally { setBusy(false); }
  };
  return <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 text-sm" aria-label="Android pairing">
    <h2 className="font-semibold">Connect the Android app</h2>
    <p className="my-2 text-sm text-muted">Pair your phone with this account. On the phone, you can choose whether Gemini may read your classes.</p>
    <Button variant="secondary" disabled={busy || !!code} onClick={() => void pair()}>{busy ? 'Creating code…' : 'Create phone pairing code'}</Button>
    {code && <div className="mt-3">
      <p className="text-sm">Enter this once in the Android app. It expires in two minutes. Keep it private.</p>
      <input aria-label="Phone pairing code" readOnly value={code} className="mt-2 w-full rounded border border-line bg-bg p-2 font-mono text-sm" />
      <Button variant="ghost" onClick={() => { setCode(''); setExpires(0); }}>Hide code</Button>
    </div>}
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
  </section>;
}
