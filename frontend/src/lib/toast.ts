/** Short confirmations shown at the bottom of the screen ("Chat renamed"). */
export type ToastTone = "info" | "error";
export interface ToastDetail { message: string; tone: ToastTone }

export function toast(message: string, tone: ToastTone = "info") {
  window.dispatchEvent(new CustomEvent<ToastDetail>("tc-toast", { detail: { message, tone } }));
}
