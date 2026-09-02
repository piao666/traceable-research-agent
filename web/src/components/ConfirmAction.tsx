import { useRef, useState } from "react";
import { Button } from "./primitives";
import { errorMessage } from "../api/client";
import { Modal } from "./Modal";

export function ConfirmAction({ title, description, target, phrase, perform, close, destructive = true }: {
  title: string; description: string; target?: string; phrase?: string; perform: () => Promise<void>; close: () => void; destructive?: boolean;
}) {
  const lock = useRef(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    if (lock.current || (phrase && input !== phrase)) return;
    lock.current = true;
    setBusy(true); setError("");
    try { await perform(); close(); }
    catch (reason) { setError(errorMessage(reason)); setBusy(false); lock.current = false; }
  }
  return <Modal title={title} description={description} busy={busy} close={close}>
    {target && <p className="r5-prewrap">{target}</p>}
    {phrase && <label className="field">输入“{phrase}”确认<input className="input" value={input} onChange={(event) => setInput(event.target.value)} disabled={busy} /></label>}
    {error && <p role="alert" className="error-banner">{error}</p>}
    <div className="r5-actions"><Button variant="secondary" disabled={busy} onClick={close}>取消</Button><Button variant={destructive ? "danger" : "primary"} loading={busy} disabled={!!phrase && input !== phrase} onClick={submit}>确认操作</Button></div>
  </Modal>;
}
