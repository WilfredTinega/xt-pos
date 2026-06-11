"""XT POS — update wizard.

Compiled to Update.exe and installed next to POS.exe (a Start-Menu "Check for
Updates" shortcut points at it). It checks an online manifest for a newer
version and, if there is one, downloads the new app files and applies them in
place — so the POS updates like any normal desktop app.

How updates are published
-------------------------
Host a small JSON manifest at a URL, and point the app at it via the
`UPDATE_URL` line in the install folder's .env (the installer writes a default;
edit it to your own URL). The manifest looks like:

    {
      "version": "1.1.0",
      "url": "https://example.com/downloads/XTPOS-1.1.0.zip",
      "notes": "What changed in this release.",
      "sha256": "<optional hex digest of the zip>"
    }

`url` must point to a .zip of the app's single-file executables (POS.exe,
Update.exe, Uninstall.exe) — produced by `make_update.py`. The wizard downloads
it, closes the running POS, copies the files over the install folder, and
updates the stored version. The installed version is read from version.txt
(written at install time); if it's missing, DEFAULT_VERSION is assumed.

Modes:
  Update.exe              Show the GUI wizard.
  Update.exe /silent      Check and, if an update exists, apply it with no GUI.
  Update.exe /check       Print the current + latest version as JSON and exit.
"""
import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

APP_NAME = "XT POS"
APP_EXE = "POS.exe"
UPDATE_EXE = "Update.exe"
DEFAULT_VERSION = "1.0.0"

# Where updates live. Releases are published on GitHub: each release's tag is
# the version (e.g. v1.2.0) and a XTPOS-<version>.zip is attached as an asset.
# The GitHub "latest release" API always points at the newest one, so there is
# nothing to hand-edit per release.
GITHUB_REPO = "WilfredTinega/xt-pos"
# Fallback used when the install folder's .env has no UPDATE_URL. The .env value
# (written by the installer / editable on each machine) always wins if present.
DEFAULT_UPDATE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def install_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _hidden_startupinfo():
    """A STARTUPINFO that force-hides any window a child process would create.

    CREATE_NO_WINDOW alone can still briefly flash a console on some Windows
    builds — and combining it with DETACHED_PROCESS (as the file-swap does) is
    technically an invalid flag pairing. STARTF_USESHOWWINDOW + SW_HIDE makes
    the "no window" explicit and reliable. Harmless on non-Windows.
    """
    si = None
    if hasattr(subprocess, "STARTUPINFO"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return si


def read_env(directory):
    env = {}
    try:
        with open(os.path.join(directory, ".env"), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip()
                if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                    val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                env[key.strip()] = val
    except OSError:
        pass
    return env


def update_url(directory):
    return read_env(directory).get("UPDATE_URL", "").strip() or DEFAULT_UPDATE_URL


def current_version(directory):
    try:
        with open(os.path.join(directory, "version.txt"), "r",
                  encoding="utf-8") as fh:
            return fh.read().strip() or DEFAULT_VERSION
    except OSError:
        return DEFAULT_VERSION


def parse_version(s):
    """Turn '1.10.2' into (1, 10, 2) for ordered comparison; tolerant of junk."""
    out = []
    for part in str(s).strip().split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


# --------------------------------------------------------------------------
# Update steps
# --------------------------------------------------------------------------
class UpdateError(Exception):
    pass


def _normalize_manifest(data):
    """Accept either a plain manifest or a GitHub 'latest release' API object,
    returning the internal {version, url, notes, sha256} shape.

    GitHub release JSON has `tag_name` (e.g. 'v1.2.0'), `body` (release notes)
    and `assets[]` (each with `name` + `browser_download_url`, and a `digest`
    like 'sha256:...'). We pick the XTPOS .zip asset to download.
    """
    if isinstance(data, dict) and "tag_name" in data and "assets" in data:
        version = str(data.get("tag_name", "")).strip().lstrip("vV")
        zip_url, digest = None, ""
        for asset in data.get("assets") or []:
            name = (asset.get("name") or "").lower()
            if name.endswith(".zip"):
                zip_url = asset.get("browser_download_url")
                raw = (asset.get("digest") or "")
                digest = raw.split(":", 1)[1] if raw.startswith("sha256:") else ""
                break
        if not zip_url:
            raise UpdateError(
                "The latest GitHub release has no .zip asset to download.")
        return {"version": version, "url": zip_url,
                "notes": data.get("body") or "", "sha256": digest}
    # Plain hosted manifest (the make_update.py / manifest.json format).
    return data


def fetch_manifest(url):
    if not url or "OWNER/REPO" in url:
        raise UpdateError(
            "Updates are not configured. Set UPDATE_URL in the install "
            "folder's .env (or GITHUB_REPO in the build) to your GitHub "
            "releases URL.")
    try:
        # GitHub's API requires a User-Agent; the Accept header asks for the
        # stable v3 release JSON. Both are harmless for a plain manifest host.
        req = urllib.request.Request(url, headers={
            "User-Agent": f"{APP_NAME}-Updater",
            "Accept": "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        manifest = _normalize_manifest(json.loads(data.decode("utf-8")))
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001
        raise UpdateError(f"Could not check for updates:\n{e}")
    if not manifest.get("version") or not manifest.get("url"):
        raise UpdateError("The update manifest is missing 'version' or 'url'.")
    return manifest


def download(url, dest, progress=None):
    def hook(block_num, block_size, total_size):
        if progress and total_size > 0:
            progress(min(100, int(block_num * block_size * 100 / total_size)))
    urllib.request.urlretrieve(url, dest, hook)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def close_running_app(log):
    """Close POS.exe so its files can be replaced."""
    log("Closing the running POS (if open)…")
    subprocess.run(["taskkill", "/f", "/im", APP_EXE],
                   creationflags=_no_window(), startupinfo=_hidden_startupinfo())


def _schedule_swaps(pairs, log):
    """After we exit, replace locked files (e.g. a new Update.exe) with the
    staged .new copies that couldn't be written while in use.

    Rather than wait on our PID (unreliable — PIDs get reused and `find` can
    match other columns in tasklist output), just retry each move until it
    succeeds: it fails while the target is still our running exe, and goes
    through the moment we've exited. The loop ends once no .new file remains."""
    if not pairs:
        return
    bat = os.path.join(tempfile.gettempdir(), "xtpos_swap.bat")
    moves = "\r\n".join(f'move /y "{new}" "{dst}" >nul 2>&1' for new, dst in pairs)
    checks = "\r\n".join(f'if exist "{new}" goto retry' for new, dst in pairs)
    script = (
        "@echo off\r\n"
        ":retry\r\n"
        "ping 127.0.0.1 -n 2 >nul\r\n"
        f"{moves}\r\n"
        f"{checks}\r\n"
        'del "%~f0"\r\n'
    )
    try:
        with open(bat, "w", encoding="ascii", errors="replace") as fh:
            fh.write(script)
        subprocess.Popen(
            ["cmd.exe", "/c", bat],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | _no_window(),
            startupinfo=_hidden_startupinfo(),
            close_fds=True,
        )
        log("Scheduled the final file swap (applied when this window closes).")
    except Exception as e:  # noqa: BLE001
        log(f"  (could not schedule the final swap: {e})")


def apply_files(extracted, directory, log):
    """Copy the extracted new app files over the install folder.

    The currently-running Update.exe (and any other locked file) is staged as
    <name>.new and swapped in after this process exits.
    """
    running = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None
    pending = []
    # If the zip wraps everything in a single top folder, descend into it.
    entries = [e for e in os.listdir(extracted)
               if not e.startswith("__MACOSX")]
    root_src = extracted
    if len(entries) == 1:
        only = os.path.join(extracted, entries[0])
        if os.path.isdir(only) and not os.path.isfile(
                os.path.join(extracted, APP_EXE)):
            root_src = only

    log("Installing the new files…")
    for root, _dirs, files in os.walk(root_src):
        rel = os.path.relpath(root, root_src)
        dest_dir = directory if rel == "." else os.path.join(directory, rel)
        os.makedirs(dest_dir, exist_ok=True)
        for f in files:
            src = os.path.join(root, f)
            dst = os.path.join(dest_dir, f)
            locked = running and os.path.abspath(dst) == running
            try:
                if locked:
                    raise PermissionError("running executable")
                shutil.copy2(src, dst)
            except (PermissionError, OSError):
                staged = dst + ".new"
                try:
                    shutil.copy2(src, staged)
                    pending.append((staged, dst))
                except OSError as e:
                    log(f"  (could not update {f}: {e})")
    _schedule_swaps(pending, log)


def write_version(directory, version, log):
    try:
        with open(os.path.join(directory, "version.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(str(version).strip() + "\n")
        log(f"Updated to version {version}.")
    except OSError as e:
        log(f"  (could not write version.txt: {e})")


def run_update(manifest, log, progress=None):
    """Download + apply the update described by `manifest`."""
    directory = install_dir()
    url = manifest["url"]
    version = manifest["version"]
    tmp_zip = os.path.join(tempfile.gettempdir(), "localpos-update.zip")

    log(f"Downloading version {version}…")
    try:
        download(url, tmp_zip, progress)
    except Exception as e:  # noqa: BLE001
        raise UpdateError(f"Download failed:\n{e}")

    expected = (manifest.get("sha256") or "").strip().lower()
    if expected:
        log("Verifying download…")
        if _sha256(tmp_zip).lower() != expected:
            raise UpdateError("The downloaded file failed its integrity check.")

    extract_dir = os.path.join(tempfile.gettempdir(), "localpos-update")
    shutil.rmtree(extract_dir, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise UpdateError("The downloaded update is not a valid .zip package.")

    close_running_app(log)
    apply_files(extract_dir, directory, log)
    write_version(directory, version, log)

    try:
        os.remove(tmp_zip)
    except OSError:
        pass
    log("")
    log("✓ Update complete.")


def relaunch_app():
    exe = os.path.join(install_dir(), APP_EXE)
    if os.path.isfile(exe):
        os.startfile(exe)  # noqa: S606 — launching our own app


# --------------------------------------------------------------------------
# GUI — a multi-step wizard (Check → Update → Finish)
# --------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk

    from wizard_ui import Wizard, Page, WHITE, MUTED, make_log_box, append_log

    class CheckPage(Page):
        title = "Check for updates"
        subtitle = "Contacting the update server…"

        def build(self):
            self.checked = False
            # Animated loader shown while we contact GitHub. The bar runs in
            # indeterminate mode (a bouncing sweep) until the check finishes.
            self.loader = tk.Frame(self.frame, bg=WHITE)
            self.loader.pack(anchor="w", fill="x", pady=(4, 8))
            self.loader_label = tk.Label(
                self.loader, bg=WHITE, fg=MUTED, justify="left", anchor="nw",
                wraplength=540,
                text=f"Getting the latest updates from GitHub…\n"
                     f"    {GITHUB_REPO} · default branch")
            self.loader_label.pack(anchor="w", pady=(0, 6))
            self.spinner = ttk.Progressbar(self.loader, mode="indeterminate")
            self.spinner.pack(fill="x")
            self.status = tk.Label(self.frame, bg=WHITE, justify="left",
                                   anchor="nw", wraplength=540, text="")
            self.notes = tk.Label(self.frame, bg=WHITE, fg=MUTED, justify="left",
                                  anchor="nw", wraplength=540)
            self.notes.pack(anchor="w", fill="both", expand=True)

        def _stop_loader(self):
            """Stop the animation and reveal the result text in its place."""
            self.spinner.stop()
            self.loader.pack_forget()
            self.status.pack(anchor="w", pady=(4, 8), before=self.notes)

        def on_enter(self):
            if self.checked:
                return
            self.wizard.set_next_enabled(False)
            self.spinner.start(12)
            import threading
            threading.Thread(target=self._check, daemon=True).start()

        def _check(self):
            directory = install_dir()
            cur = current_version(directory)
            self.wizard.shared["current"] = cur
            try:
                manifest = fetch_manifest(update_url(directory))
            except UpdateError as e:
                def show_err():
                    self.checked = True
                    self._stop_loader()
                    self.status.config(text=str(e))
                    self.wizard.shared["update_available"] = False
                    self.wizard.set_next_enabled(True)
                    self.wizard.refresh_buttons()
                self.wizard.after(show_err)
                return

            latest = manifest["version"]
            available = parse_version(latest) > parse_version(cur)
            self.wizard.shared["manifest"] = manifest
            self.wizard.shared["update_available"] = available

            def show():
                self.checked = True
                self._stop_loader()
                if available:
                    self.status.config(
                        text=f"A new version is available.\n\n"
                             f"    Installed:  {cur}\n    Latest:     {latest}")
                    notes = manifest.get("notes", "")
                    self.notes.config(
                        text=("What's new:\n" + notes) if notes else "")
                else:
                    self.status.config(
                        text=f"You're up to date.\n\n    Installed:  {cur}\n"
                             f"    Latest:     {latest}")
                    self.notes.config(text="")
                self.wizard.set_next_enabled(True)
                self.wizard.refresh_buttons()
            self.wizard.after(show)

        def next_text(self):
            return "Update now" if self.wizard.shared.get("update_available") \
                else "Close"

        def handle_next(self):
            # Nothing to do unless an update is available → finish here.
            if not self.wizard.shared.get("update_available"):
                self.wizard.finish()
                return True
            if not is_admin():
                from tkinter import messagebox
                messagebox.showwarning(
                    "Administrator required",
                    "Please run the updater as Administrator so it can replace "
                    "the app files in Program Files.")
                return True
            return False  # advance to the update page

        def show_back(self):
            return False

    class UpdatePage(Page):
        title = "Updating"
        subtitle = "Downloading and installing the latest version…"

        def build(self):
            self.running = False
            self.done = False
            self.progress = ttk.Progressbar(self.frame, mode="determinate")
            self.progress.pack(fill="x", pady=(2, 8))
            self.log_box = make_log_box(self.frame)
            self.log_box.pack(fill="both", expand=True)

        def _log(self, msg):
            self.wizard.after(lambda: append_log(self.log_box, msg))

        def _progress(self, pct):
            self.wizard.after(lambda: self.progress.configure(value=pct))

        def on_enter(self):
            if self.done:
                self.wizard.set_next_enabled(True)
                return
            if self.running:
                return
            self.running = True
            self.wizard.set_next_enabled(False)
            self.wizard.set_back_enabled(False)
            self.wizard.set_cancel_enabled(False)
            import threading
            threading.Thread(target=self._worker, daemon=True).start()

        def _worker(self):
            try:
                run_update(self.wizard.shared["manifest"], self._log,
                           self._progress)
                self.wizard.shared["applied"] = True
            except Exception as e:  # noqa: BLE001
                self._log(f"\nERROR: {e}")
                from tkinter import messagebox
                self.wizard.after(
                    lambda: messagebox.showerror("Update failed", str(e)))
            self.done = True
            self.running = False

            def ok():
                self.progress.configure(value=100)
                self.wizard.set_next_enabled(True)
            self.wizard.after(ok)

        def show_back(self):
            return False

        def show_cancel(self):
            return not self.done

    class FinishPage(Page):
        title = "Done"
        subtitle = ""

        def build(self):
            self.msg = tk.Label(self.frame, bg=WHITE, justify="left",
                                anchor="nw", wraplength=540, text="")
            self.msg.pack(anchor="w", pady=(0, 14))
            self.relaunch = tk.BooleanVar(value=True)
            self.relaunch_chk = tk.Checkbutton(
                self.frame, bg=WHITE, variable=self.relaunch,
                text=f"Open {APP_NAME} now")

        def on_enter(self):
            if self.wizard.shared.get("applied"):
                self.msg.config(text="The update was installed successfully.")
                self.relaunch_chk.pack(anchor="w")
            else:
                self.msg.config(text="No changes were made.")

        def next_text(self):
            return "Finish"

        def show_back(self):
            return False

        def show_cancel(self):
            return False

    icon = resource_path(os.path.join("assets", "icon.ico"))
    wiz = Wizard(f"{APP_NAME} — Update", icon_path=icon, width=620, height=500)
    wiz.add_page(CheckPage)
    wiz.add_page(UpdatePage)
    finish = wiz.add_page(FinishPage)

    def on_finish(shared):
        if shared.get("applied") and finish.relaunch.get():
            relaunch_app()

    wiz.on_finish = on_finish
    wiz.start()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="XT POS updater")
    parser.add_argument("--silent", dest="silent", action="store_true",
                        help="Check and apply any update with no GUI.")
    parser.add_argument("--check", dest="check", action="store_true",
                        help="Print current + latest version as JSON and exit.")
    norm = [("--" + a[1:]) if a.startswith("/") else a for a in sys.argv[1:]]
    args, _ = parser.parse_known_args(norm)

    directory = install_dir()
    cur = current_version(directory)

    if args.check:
        info = {"current": cur, "configured": bool(update_url(directory))}
        try:
            m = fetch_manifest(update_url(directory))
            info["latest"] = m.get("version")
            info["update_available"] = parse_version(m.get("version")) > parse_version(cur)
        except UpdateError as e:
            info["error"] = str(e)
        print(json.dumps(info, indent=2))
        return

    if args.silent:
        try:
            m = fetch_manifest(update_url(directory))
        except UpdateError as e:
            print(e)
            sys.exit(1)
        if parse_version(m["version"]) > parse_version(cur):
            run_update(m, print)
        else:
            print(f"Already up to date ({cur}).")
        return

    run_gui()


if __name__ == "__main__":
    main()
