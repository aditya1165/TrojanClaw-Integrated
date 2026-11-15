import { useCallback, useEffect, useRef, useState } from "react";

// The browser's built-in speech recognition (Chrome, Edge, Safari; not Firefox).
// Chrome sends the audio to Google to recognise it.
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}
type RecognitionClass = new () => Recognition;

const RecognitionImpl: RecognitionClass | undefined =
  (window as unknown as { SpeechRecognition?: RecognitionClass; webkitSpeechRecognition?: RecognitionClass }).SpeechRecognition
  ?? (window as unknown as { webkitSpeechRecognition?: RecognitionClass }).webkitSpeechRecognition;

export const speechSupported = Boolean(RecognitionImpl);

/**
 * Dictation into the message box. `onText` gets the full text so far (what was
 * already typed plus what has been said); `onError` gets a readable message.
 */
export function useSpeech(onText: (text: string) => void, onError: (message: string) => void) {
  const [listening, setListening] = useState(false);
  const recognition = useRef<Recognition | null>(null);
  const callbacks = useRef({ onText, onError });
  useEffect(() => { callbacks.current = { onText, onError }; });
  useEffect(() => () => recognition.current?.stop(), []);

  const stop = useCallback(() => recognition.current?.stop(), []);

  const start = useCallback((typed: string) => {
    if (!RecognitionImpl || recognition.current) return;
    const instance = new RecognitionImpl();
    instance.lang = navigator.language || "en-US";
    instance.continuous = true;
    instance.interimResults = true;
    const before = typed.trim() ? typed.trimEnd() + " " : "";
    let spoken = "";
    instance.onresult = (event) => {
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index++) {
        const result = event.results[index];
        if (result.isFinal) spoken += result[0].transcript;
        else interim += result[0].transcript;
      }
      callbacks.current.onText(before + (spoken + interim).trimStart());
    };
    instance.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        callbacks.current.onError("Allow microphone access for this site to use voice input.");
      } else if (event.error === "no-speech") {
        callbacks.current.onError("Didn't hear anything. Try again.");
      } else if (event.error !== "aborted") {
        callbacks.current.onError("Voice input stopped. Try again.");
      }
    };
    instance.onend = () => {
      recognition.current = null;
      setListening(false);
    };
    recognition.current = instance;
    instance.start();
    setListening(true);
  }, []);

  return { listening, start, stop };
}
