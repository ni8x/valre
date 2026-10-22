import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type Mode = "upload" | "link";

type Pending = { file: File; name: string };

const stem = (name: string) => name.replace(/\.[^.]+$/, "");

export default function UploadDialog({
  open,
  initialMode = "upload",
  onClose,
  onAdded,
}: {
  open: boolean;
  initialMode?: Mode;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [pending, setPending] = useState<Pending[]>([]);
  const [path, setPath] = useState("");
  const [linkName, setLinkName] = useState("");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setPending([]);
    setPath("");
    setLinkName("");
    setError(null);
    setBusy(false);
    setDrag(false);
    const focusTimer = window.setTimeout(() => {
      boxRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    }, 30);
    return () => window.clearTimeout(focusTimer);
  }, [open, initialMode]);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        onClose();
        return;
      }
      if (e.key !== "Tab" || !boxRef.current) return;
      const focusable = Array.from(
        boxRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select, textarea, a[href]',
        ),
      ).filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [open, busy, onClose]);

  if (!open) return null;

  const addFiles = (files: FileList | File[] | null) => {
    if (!files) return;
    const list = Array.from(files);
    const vrf = list.filter((f) => f.name.toLowerCase().endsWith(".vrf"));
    if (!vrf.length) {
      setError("Those files are not .vrf replays.");
      return;
    }
    setError(vrf.length < list.length ? "Only the .vrf files were added." : null);
    setPending((current) => [
      ...current,
      ...vrf.map((file) => ({ file, name: stem(file.name) })),
    ]);
  };

  const renamePending = (index: number, name: string) => {
    setPending((current) => current.map((p, i) => (i === index ? { ...p, name } : p)));
  };

  const removePending = (index: number) => {
    setPending((current) => current.filter((_, i) => i !== index));
  };

  const canSubmit = mode === "upload" ? pending.length > 0 : path.trim().length > 0;

  const submit = async () => {
    if (busy || !canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "upload") {
        for (const item of pending) {
          await api.upload(item.file, item.name.trim() || undefined);
        }
      } else {
        await api.link(path.trim(), linkName.trim() || undefined);
      }
      onAdded();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="modal-box upload-box"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
        ref={boxRef}
      >
        <header className="modal-head">
          <div>
            <h2 id="upload-title">Add a replay</h2>
            <p className="small muted">Give it a name you will recognise in history.</p>
          </div>
          <button className="close-btn" onClick={onClose} disabled={busy} aria-label="Close">
            ×
          </button>
        </header>

        <div className="mode-switch" role="tablist" aria-label="How to add the replay">
          <button
            role="tab"
            aria-selected={mode === "upload"}
            data-autofocus
            className={mode === "upload" ? "on" : ""}
            onClick={() => setMode("upload")}
          >
            Upload a file
          </button>
          <button
            role="tab"
            aria-selected={mode === "link"}
            className={mode === "link" ? "on" : ""}
            onClick={() => setMode("link")}
          >
            Use a file already on this PC
          </button>
        </div>

        {mode === "upload" && (
          <>
            {pending.length === 0 ? (
              <div
                className={`dropzone ${drag ? "drag" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => fileRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    fileRef.current?.click();
                  }
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDrag(true);
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDrag(false);
                  addFiles(e.dataTransfer.files);
                }}
              >
                <span className="drop-mark" aria-hidden="true">
                  .vrf
                </span>
                <b>Drop a replay here</b>
                <span className="small muted">or click to choose a .vrf file</span>
              </div>
            ) : (
              <div className="pending">
                {pending.map((item, i) => (
                  <div className="pending-row" key={`${item.file.name}-${i}`}>
                    <span className="file-tag" aria-hidden="true">
                      VRF
                    </span>
                    <div className="pending-file">
                      <b>{item.file.name}</b>
                      <span className="small muted">{(item.file.size / 1048576).toFixed(1)} MB</span>
                    </div>
                    <label className="name-field">
                      <span className="small muted">Name</span>
                      <input
                        value={item.name}
                        maxLength={120}
                        autoFocus={i === 0}
                        placeholder="Shown in history"
                        onChange={(e) => renamePending(i, e.target.value)}
                      />
                    </label>
                    <button
                      className="icon-btn"
                      onClick={() => removePending(i)}
                      aria-label={`Remove ${item.file.name}`}
                      title="Remove"
                    >
                      ×
                    </button>
                  </div>
                ))}
                <button className="linkish" onClick={() => fileRef.current?.click()}>
                  Choose another file
                </button>
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".vrf"
              multiple
              className="sr-only"
              onChange={(e) => {
                addFiles(e.target.files);
                e.currentTarget.value = "";
              }}
            />
          </>
        )}

        {mode === "link" && (
          <div className="link-form">
            <label>
              <span>Full path to the replay</span>
              <input
                value={path}
                data-autofocus
                spellCheck={false}
                placeholder="C:\Users\you\Documents\match.vrf"
                onChange={(e) => setPath(e.target.value)}
              />
            </label>
            <label>
              <span>
                Name <span className="muted">(optional)</span>
              </span>
              <input
                value={linkName}
                maxLength={120}
                placeholder="Defaults to the file name"
                onChange={(e) => setLinkName(e.target.value)}
              />
            </label>
            <p className="small muted">
              The file is copied into VALRE's data folder, so moving the original later will not break
              the replay.
            </p>
          </div>
        )}

        {error && <div className="err small">{error}</div>}

        <footer className="modal-foot">
          <span className="small muted">Nothing leaves this machine.</span>
          <div className="row">
            <button className="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button className="primary" onClick={submit} disabled={busy || !canSubmit}>
              {busy ? "Adding…" : "Add replay"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
