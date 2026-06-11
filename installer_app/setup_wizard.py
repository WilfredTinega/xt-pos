"""XT POS — self-contained Windows setup wizard.

Compiled (with the POS app bundled inside) into a single XTPOS-Setup.exe.
When the user runs it, it:

  1. Asks for the database admin (root) username + password, port, shop name.
  2. Downloads and silently installs MariaDB as a Windows service (skipped if
     MariaDB is already present), using the password the user chose.
  3. Installs the WebView2 runtime if it's missing (needed for the app window).
  4. Copies the POS app into Program Files and writes its config.
  5. Creates the pos_db database, the app user, and all tables.
  6. Creates Start-Menu + Desktop shortcuts and launches the app.

No Python, no MariaDB, and no other tools need to be pre-installed on the
target PC. Internet is required during installation to download MariaDB.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

APP_NAME = "XT POS"
APP_PUBLISHER = "Xonal Tech"
INSTALL_DIRNAME = "XTPOS"
APP_EXE = "POS.exe"
UNINSTALL_EXE = "Uninstall.exe"
UPDATE_EXE = "Update.exe"
# Default update manifest URL written into .env. Leave blank to configure later
# (edit UPDATE_URL in the install folder's .env).
DEFAULT_UPDATE_URL = ""
# Add/Remove Programs registry key (so the POS appears in "Apps & features").
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\XTPOS"

# MariaDB to download (LTS). Bump both together to update.
MARIADB_VERSION = "11.4.4"
MARIADB_URL = (
    "https://archive.mariadb.org/mariadb-11.4.4/winx64-packages/"
    "mariadb-11.4.4-winx64.msi"
)
# Microsoft Edge WebView2 Evergreen bootstrapper (tiny, pulls the runtime).
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _detect_version():
    """Read the bundled VERSION file (single source of truth). The build
    bundles it into the installer; falls back if it's somehow missing."""
    try:
        with open(resource_path("VERSION"), "r", encoding="utf-8") as fh:
            return fh.read().strip() or "1.0.0"
    except OSError:
        return "1.0.0"


APP_VERSION = _detect_version()


def payload_dir():
    """Folder (bundled inside this exe) holding the compiled POS app."""
    return resource_path("app_payload")


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _service_exists(name):
    try:
        out = subprocess.run(
            ["sc.exe", "query", name],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.returncode == 0
    except Exception:
        return False


def is_mariadb_installed():
    return _service_exists("MariaDB") or _service_exists("MySQL")


def test_db_connection(password, port, host="127.0.0.1", timeout=5):
    """Try to reach MariaDB as root with the given password/port.

    Returns (status, message):
      True  — connected (the password matches the existing MariaDB root),
      False — reachable but the connection/login failed,
      None  — nothing to test yet (MariaDB will be installed by setup).
    """
    if not is_mariadb_installed():
        return (None, "MariaDB isn't installed yet — setup will download and "
                      "install it. Nothing to test on this PC.")
    try:
        import pymysql
    except Exception as e:  # noqa: BLE001
        return (None, f"Database driver unavailable ({e}).")
    try:
        conn = pymysql.connect(host=host, port=int(port or 3306), user="root",
                               password=password, connect_timeout=timeout)
        conn.close()
        return (True, f"Connected to MariaDB on port {port}. The password matches.")
    except Exception as e:  # noqa: BLE001
        return (False, f"Could not connect: {e}")


def is_webview2_installed():
    """WebView2 Evergreen runtime registers a client GUID in the registry."""
    import winreg
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    paths = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\" + guid),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + guid),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients\\" + guid),
    ]
    for root, sub in paths:
        try:
            with winreg.OpenKey(root, sub):
                return True
        except OSError:
            continue
    return False


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _hidden_startupinfo():
    """A STARTUPINFO that force-hides any window a child process would create.

    CREATE_NO_WINDOW alone can still briefly flash a console window on some
    Windows builds; STARTF_USESHOWWINDOW + SW_HIDE makes the "no window"
    explicit and reliable (e.g. the powershell shortcut helper). Harmless on
    non-Windows.
    """
    si = None
    if hasattr(subprocess, "STARTUPINFO"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return si


def download(url, dest, progress=None):
    def hook(block_num, block_size, total_size):
        if progress and total_size > 0:
            pct = min(100, int(block_num * block_size * 100 / total_size))
            progress(pct)
    urllib.request.urlretrieve(url, dest, hook)


# --------------------------------------------------------------------------
# Install steps
# --------------------------------------------------------------------------
class InstallError(Exception):
    pass


def install_webview2(log):
    if is_webview2_installed():
        log("WebView2 runtime already present — skipping.")
        return
    log("Downloading WebView2 runtime…")
    boot = os.path.join(tempfile.gettempdir(), "MicrosoftEdgeWebview2Setup.exe")
    try:
        download(WEBVIEW2_URL, boot)
    except Exception as e:
        log(f"WARNING: could not download WebView2 ({e}). The app may need it.")
        return
    log("Installing WebView2 runtime…")
    subprocess.run([boot, "/silent", "/install"],
                   creationflags=_no_window(), startupinfo=_hidden_startupinfo())


def install_mariadb(password, port, log, progress=None):
    """Returns True if it actually installed MariaDB, False if it was already
    present (in which case the entered password must match the existing root)."""
    if is_mariadb_installed():
        log("MariaDB is already installed — skipping download.")
        return False
    msi = os.path.join(tempfile.gettempdir(), "mariadb.msi")
    log(f"Downloading MariaDB {MARIADB_VERSION} (this can take a few minutes)…")
    try:
        download(MARIADB_URL, msi, progress)
    except Exception as e:
        raise InstallError(
            f"Could not download MariaDB.\n{e}\n\n"
            "Check your internet connection and run setup again."
        )
    log("Installing MariaDB service…")
    params = [
        "msiexec.exe", "/i", msi, "/qn", "/norestart",
        "SERVICENAME=MariaDB", f"PORT={port}",
        f"PASSWORD={password}", "UTF8=1",
    ]
    res = subprocess.run(params, creationflags=_no_window(),
                         startupinfo=_hidden_startupinfo())
    if res.returncode != 0:
        raise InstallError(
            f"MariaDB installation failed (code {res.returncode})."
        )
    # Make sure the service is running.
    subprocess.run(["net", "start", "MariaDB"],
                   creationflags=_no_window(), startupinfo=_hidden_startupinfo())
    return True


def app_already_working(install_dir, log):
    """True if an existing install can already reach its database (so a re-run
    needn't touch MariaDB, the database, or the saved credentials)."""
    exe = os.path.join(install_dir, APP_EXE)
    env = os.path.join(install_dir, ".env")
    if not (os.path.isfile(exe) and os.path.isfile(env)):
        return False
    try:
        r = subprocess.run([exe, "--check-db"], creationflags=_no_window(),
                           startupinfo=_hidden_startupinfo(), timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def copy_app(install_dir, log):
    log(f"Installing the app to {install_dir}…")
    os.makedirs(install_dir, exist_ok=True)
    src = payload_dir()
    if not os.path.isdir(src):
        raise InstallError("Bundled app payload is missing from the installer.")
    shutil.copytree(src, install_dir, dirs_exist_ok=True)


def _drop_self_copy(install_dir, log):
    """Copy this installer next to the app so the app can re-run it later to
    auto-install MariaDB on first launch / repair a broken setup."""
    try:
        if getattr(sys, "frozen", False):
            dest = os.path.join(install_dir, "XTPOS-Setup.exe")
            if os.path.abspath(sys.executable) != os.path.abspath(dest):
                shutil.copy2(sys.executable, dest)
    except Exception as e:
        log(f"  (could not stage the repair installer: {e})")


def _env_quote(value):
    """Double-quote a .env value and escape it so special characters (#, =,
    spaces, backslashes, quotes) survive round-tripping through python-dotenv."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
    return '"' + s + '"'


def write_env(install_dir, user, password, port, shop):
    lines = [
        "DB_HOST=127.0.0.1",
        f"DB_PORT={port}",
        "DB_NAME=pos_db",
        f"DB_USER={_env_quote(user)}",
        f"DB_PASSWORD={_env_quote(password)}",
        f"STORE_NAME={_env_quote(shop)}",
        "CURRENCY=KES",
        "TAX_RATE=0",
        f"UPDATE_URL={_env_quote(DEFAULT_UPDATE_URL)}",
    ]
    with open(os.path.join(install_dir, ".env"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_version(install_dir):
    """Record the installed version so the updater can compare against the
    online manifest."""
    try:
        with open(os.path.join(install_dir, "version.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write(APP_VERSION + "\n")
    except OSError:
        pass


def init_database(install_dir, root_password, user, port, log, mariadb_was_present):
    """Create the database/user/tables. Raises InstallError on failure.

    Must run BEFORE write_env so a wrong password never clobbers a working
    config on a re-run.
    """
    log("Creating the POS database and tables…")
    cfg = {
        "root_password": root_password,
        "db_host": "127.0.0.1",
        "db_port": str(port),
        "db_name": "pos_db",
        "app_user": user,
        "app_password": root_password,
    }
    cfg_path = os.path.join(tempfile.gettempdir(), "pos-init.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    try:
        exe = os.path.join(install_dir, APP_EXE)
        res = subprocess.run(
            [exe, "--init-db", "--config", cfg_path],
            creationflags=_no_window(),
            startupinfo=_hidden_startupinfo(),
        )
        if res.returncode != 0:
            if mariadb_was_present:
                raise InstallError(
                    "MariaDB is already installed on this PC, and the password "
                    "you entered does not match its existing root password.\n\n"
                    "Enter the ORIGINAL root password you set the first time, "
                    "or uninstall MariaDB (Add/Remove Programs) to start fresh."
                )
            raise InstallError(
                "The database could not be initialized. Make sure the MariaDB "
                "service started, then run setup again."
            )
    finally:
        try:
            os.remove(cfg_path)  # contains the root password
        except OSError:
            pass


def _make_shortcut(lnk, target, workdir, log):
    try:
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            "$s.TargetPath='{exe}';$s.WorkingDirectory='{wd}';$s.Save()"
        ).format(lnk=lnk.replace("'", "''"),
                 exe=target.replace("'", "''"),
                 wd=workdir.replace("'", "''"))
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       creationflags=_no_window(), startupinfo=_hidden_startupinfo())
    except Exception as e:
        log(f"  (could not create {os.path.basename(lnk)}: {e})")


def create_shortcuts(install_dir, log):
    log("Creating shortcuts…")
    exe = os.path.join(install_dir, APP_EXE)
    start_menu = os.path.join(
        os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
        "Microsoft", "Windows", "Start Menu", "Programs")
    desktop = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")

    _make_shortcut(os.path.join(desktop, f"{APP_NAME}.lnk"), exe, install_dir, log)
    _make_shortcut(os.path.join(start_menu, f"{APP_NAME}.lnk"), exe, install_dir, log)

    # A Start-Menu "Check for Updates" shortcut, if the updater shipped.
    updater = os.path.join(install_dir, UPDATE_EXE)
    if os.path.isfile(updater):
        _make_shortcut(os.path.join(start_menu, f"{APP_NAME} — Check for Updates.lnk"),
                       updater, install_dir, log)


def _dir_size_kb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total // 1024


def register_uninstall(install_dir, log):
    """Register the app in Add/Remove Programs so it uninstalls like any
    normal Windows application (Settings → Apps, or Control Panel)."""
    import winreg
    uninstaller = os.path.join(install_dir, UNINSTALL_EXE)
    if not os.path.isfile(uninstaller):
        log("  (uninstaller not bundled; skipping Add/Remove Programs entry)")
        return
    exe = os.path.join(install_dir, APP_EXE)
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": APP_VERSION,
        "Publisher": APP_PUBLISHER,
        "InstallLocation": install_dir,
        "DisplayIcon": exe,
        "UninstallString": f'"{uninstaller}"',
        "QuietUninstallString": f'"{uninstaller}" /silent /purge-data',
    }
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                                winreg.KEY_WRITE) as key:
            for name, val in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD,
                              _dir_size_kb(install_dir))
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        log("Registered in Add/Remove Programs.")
    except OSError as e:
        log(f"  (could not register the uninstaller: {e})")


def launch_app(install_dir):
    exe = os.path.join(install_dir, APP_EXE)
    os.startfile(exe)  # noqa: S606 — launching our own installed app


def run_install(user, password, port, shop, log, progress=None):
    """Full installation pipeline. Raises InstallError on failure."""
    install_dir = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"), INSTALL_DIRNAME)

    mariadb_was_present = not install_mariadb(password, port, log, progress)
    install_webview2(log)
    copy_app(install_dir, log)
    _drop_self_copy(install_dir, log)

    # If this PC is already set up and the database is reachable with the
    # existing config, a re-run should just refresh the app files and leave the
    # database + credentials untouched (no password needed).
    if app_already_working(install_dir, log):
        log("Already configured — refreshed the app; kept existing database settings.")
    else:
        # Validate the password / create the DB BEFORE writing .env, so a failed
        # re-run never overwrites a previously-working configuration.
        init_database(install_dir, password, user, port, log, mariadb_was_present)
        write_env(install_dir, user, password, port, shop)

    write_version(install_dir)
    create_shortcuts(install_dir, log)
    register_uninstall(install_dir, log)
    log("")
    log("✓ Installation complete.")
    return install_dir


# --------------------------------------------------------------------------
# GUI — a multi-step wizard (Welcome → Settings → Install → Finish)
# --------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    from wizard_ui import Wizard, Page, WHITE, MUTED, make_log_box, append_log

    class WelcomePage(Page):
        title = f"Welcome to the {APP_NAME} setup"
        subtitle = ("This will install everything needed to run the POS on "
                    "this computer.")

        def build(self):
            steps = (
                "The wizard will:\n\n"
                "    •  Install the MariaDB database engine (downloaded "
                "automatically if it isn't already present).\n\n"
                "    •  Install the WebView2 runtime needed for the app window.\n\n"
                "    •  Copy the POS application into Program Files and create "
                "the database.\n\n"
                "    •  Add Start-Menu and Desktop shortcuts.\n\n"
                "Administrator rights are required. Click Next to continue."
            )
            tk.Label(self.frame, text=steps, bg=WHITE, justify="left",
                     anchor="nw", wraplength=540).pack(fill="both", expand=True)

    class SettingsPage(Page):
        title = "Database settings"
        subtitle = ("Choose the database administrator account the POS will "
                    "use. Keep these safe — you'll need them for maintenance.")

        def build(self):
            self.vars = {}

            def field(label, default="", show=None, readonly=False):
                row = tk.Frame(self.frame, bg=WHITE)
                row.pack(fill="x", pady=5)
                tk.Label(row, text=label, bg=WHITE, width=18, anchor="w").pack(
                    side="left")
                var = tk.StringVar(value=default)
                ttk.Entry(row, textvariable=var, show=show, width=34,
                          state="readonly" if readonly else "normal").pack(
                    side="left", fill="x", expand=True)
                return var

            self.vars["user"] = field("Admin username", "root")
            self.vars["pass"] = field("Admin password", "", show="•")
            self.vars["port"] = field("Database port", "3306", readonly=True)
            self.vars["shop"] = field("Company / business name", "My Company")

            # Test-connection row: verify MariaDB is reachable before installing.
            test_row = tk.Frame(self.frame, bg=WHITE)
            test_row.pack(fill="x", pady=(12, 2))
            self.test_btn = ttk.Button(test_row, text="Test connection",
                                       command=self._test)
            self.test_btn.pack(side="left")
            self.test_status = tk.Label(test_row, bg=WHITE, anchor="w",
                                        justify="left", wraplength=360, text="")
            self.test_status.pack(side="left", padx=10, fill="x", expand=True)

            tk.Label(self.frame, bg=WHITE, fg=MUTED, justify="left",
                     wraplength=540,
                     text='Enter your company/business name — "-POS" is added '
                          'automatically (e.g. Acme → Acme-POS).').pack(
                anchor="w", pady=(8, 0))

        def _test(self):
            import threading
            self.test_status.config(text="Testing…", fg=MUTED)
            self.test_btn.config(state="disabled")
            pwd = self.vars["pass"].get()
            port = self.vars["port"].get().strip() or "3306"

            def work():
                status, msg = test_db_connection(pwd, port)

                def show():
                    color = {True: "#15803d", False: "#b91c1c"}.get(status, MUTED)
                    self.test_status.config(text=msg, fg=color)
                    self.test_btn.config(state="normal")
                self.wizard.after(show)
            threading.Thread(target=work, daemon=True).start()

        def validate(self):
            v = {k: var.get() for k, var in self.vars.items()}
            if not v["pass"]:
                messagebox.showerror("Setup", "Please enter an admin password.")
                return False
            if not is_admin():
                messagebox.showwarning(
                    "Administrator required",
                    "Please run this installer as Administrator so it can "
                    "install MariaDB and the app.")
                return False
            # The stored/displayed name is the business name with "-POS" appended.
            biz = v["shop"].strip() or "My Company"
            if not biz.upper().endswith("-POS"):
                biz = f"{biz}-POS"
            self.wizard.shared.update(
                user=v["user"].strip() or "root",
                password=v["pass"],
                port=v["port"].strip() or "3306",
                shop=biz,
            )
            return True

    class InstallPage(Page):
        title = "Installing"
        subtitle = "Please wait while the POS is installed…"

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
            threading.Thread(target=self._worker, daemon=True).start()

        def _worker(self):
            s = self.wizard.shared
            try:
                d = run_install(s["user"], s["password"], s["port"],
                                s["shop"], self._log, self._progress)
                s["install_dir"] = d
                self.done = True
                self.running = False

                def ok():
                    self.progress.configure(value=100)
                    self.wizard.set_next_enabled(True)
                self.wizard.after(ok)
            except Exception as e:  # noqa: BLE001
                self.running = False
                msg = str(e)
                self._log(f"\nERROR: {msg}")

                def fail():
                    messagebox.showerror("Setup failed", msg)
                    self.wizard.set_back_enabled(True)
                    self.wizard.set_cancel_enabled(True)
                self.wizard.after(fail)

        def show_back(self):
            return not self.done

        def show_cancel(self):
            return not self.done

    class FinishPage(Page):
        title = f"{APP_NAME} is installed"
        subtitle = "Setup finished successfully."

        def build(self):
            tk.Label(self.frame, bg=WHITE, justify="left", anchor="nw",
                     wraplength=540,
                     text="The POS is ready to use. You can open it any time "
                          "from the Start Menu or the desktop shortcut.").pack(
                anchor="w", pady=(0, 16))
            self.launch = tk.BooleanVar(value=True)
            tk.Checkbutton(self.frame, bg=WHITE, variable=self.launch,
                           text=f"Launch {APP_NAME} now").pack(anchor="w")

        def next_text(self):
            return "Finish"

        def show_back(self):
            return False

        def show_cancel(self):
            return False

    icon = resource_path(os.path.join("assets", "icon.ico"))
    wiz = Wizard(f"{APP_NAME} — Setup", icon_path=icon, width=620, height=580)
    wiz.add_page(WelcomePage)
    wiz.add_page(SettingsPage)
    wiz.add_page(InstallPage)
    finish = wiz.add_page(FinishPage)

    def on_finish(shared):
        if finish.launch.get() and shared.get("install_dir"):
            launch_app(shared["install_dir"])

    wiz.on_finish = on_finish
    wiz.start()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="XT POS setup")
    parser.add_argument("--selftest", action="store_true",
                        help="Print environment detection + payload status and exit.")
    parser.add_argument("--out", help="Write selftest output to this file.")
    args = parser.parse_args()

    if args.selftest:
        report = {
            "is_admin": is_admin(),
            "mariadb_installed": is_mariadb_installed(),
            "webview2_installed": is_webview2_installed(),
            "payload_dir": payload_dir(),
            "payload_has_exe": os.path.isfile(os.path.join(payload_dir(), APP_EXE)),
        }
        text = json.dumps(report, indent=2)
        print(text)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        return

    run_gui()


if __name__ == "__main__":
    main()
