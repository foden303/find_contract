"""Windows and macOS desktop lifecycle; smoke mode also runs on development hosts."""

import argparse
import csv
import ctypes
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import subprocess
import webbrowser

APP_ID = "b2b-contact-finder"
MUTEX_NAME = r"Local\B2BContactFinder"
STARTUP_TIMEOUT = 40.0
LOG = logging.getLogger("finder.desktop")


class WindowsMutex:
    """Keep the kernel handle and ownership until the server has fully stopped."""

    def __init__(self):
        from ctypes import wintypes

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateMutexW(None, True, MUTEX_NAME)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        self.owned = error != 183  # ERROR_ALREADY_EXISTS
        if not self.owned:
            result = self.api.WaitForSingleObject(self.handle, 0)
            if result == 0xFFFFFFFF:
                error = ctypes.get_last_error()
                self.api.CloseHandle(self.handle)
                raise ctypes.WinError(error)
            self.owned = result in (0, 0x80)  # acquired or abandoned

    def close(self):
        if self.owned:
            self.api.ReleaseMutex(self.handle)
        self.api.CloseHandle(self.handle)

class PosixMutex:
    """Advisory per-user lock; keep the file descriptor open until shutdown."""

    def __init__(self, path):
        import fcntl

        self.stream = open(path, "a+b")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owned = True
        except BlockingIOError:
            self.owned = False

    def close(self):
        if self.stream is None:
            return
        if self.owned:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.stream = None


def message(text, *, warning=False):
    if os.name == "nt":
        from ctypes import wintypes

        api = ctypes.WinDLL("user32", use_last_error=True)
        api.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        api.MessageBoxW.restype = ctypes.c_int
        return api.MessageBoxW(None, text, "B2B Contact Finder", 0x134 if warning else 0x10) == 6
    if sys.platform == "darwin":
        environment = os.environ.copy()
        environment["FINDER_MESSAGE"] = text
        if warning:
            script = ('display alert \"B2B Contact Finder\" message (system attribute \"FINDER_MESSAGE\") '
                      'as warning buttons {\"Cancel\", \"Exit\"} default button \"Cancel\" cancel button \"Cancel\"')
            result = subprocess.run(["/usr/bin/osascript", "-e", script], env=environment,
                                    capture_output=True, text=True)
            return result.returncode == 0 and "Exit" in result.stdout
        script = ('display alert \"B2B Contact Finder\" message (system attribute \"FINDER_MESSAGE\") '
                  'as critical buttons {\"OK\"} default button \"OK\"')
        subprocess.run(["/usr/bin/osascript", "-e", script], env=environment,
                       capture_output=True, text=True)
        return False
    if sys.stderr is not None:
        print(text, file=sys.stderr)
    return False


def configure_logs(directory):
    path = Path(directory) / "desktop.log"
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Do not log browser query strings: the initial URL contains the session secret.
    logging.getLogger("uvicorn.access").disabled = True
    return path


def request(port, token, path, *, data=None, content_type=None):
    headers = {"X-Finder-Token": token}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
    # Ignore system proxies for the private loopback service.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=2) as response:
        return response.read(), response.headers


def request_json(port, token, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    body, _ = request(port, token, path, data=data, content_type="application/json" if data else None)
    return json.loads(body)


def health(port, token, version):
    result = request_json(port, token, "/api/health")
    if result.get("app") != APP_ID or result.get("version") != version:
        raise RuntimeError("The local server did not identify as this version of B2B Contact Finder")
    if not isinstance(result.get("active_runs"), int):
        raise RuntimeError("The local server returned an invalid health response")
    return result


def browser_url(port, token):
    return f"http://127.0.0.1:{port}/?token={token}"


def open_browser(port, token):
    if not webbrowser.open(browser_url(port, token)):
        raise RuntimeError("Could not open your default browser. Set a default browser and try again.")


def process_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except ProcessLookupError:
            return False
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 258
    finally:
        api.CloseHandle(handle)


def open_existing(state_path, version):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            port, token, pid = state["port"], state["token"], state["pid"]
            if (state.get("app") != APP_ID or state.get("version") != version
                    or not isinstance(port, int) or not 1 <= port <= 65535
                    or not isinstance(token, str) or len(token) != 64
                    or not isinstance(pid, int) or pid <= 0 or not process_alive(pid)):
                raise ValueError("Stale or invalid instance state")
            health(port, token, version)
            # A listener that ignores authentication must never be accepted.
            try:
                request(port, "invalid-instance-token", "/api/health")
            except urllib.error.HTTPError as exc:
                if exc.code not in (401, 403):
                    raise
            else:
                raise ValueError("Existing listener does not enforce authentication")
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            time.sleep(0.2)
            continue
        open_browser(port, token)
        return
    raise RuntimeError("Another instance is starting or stopping, but could not be reached safely. Wait and try again.")

def stop_existing(state_path, version):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    port, token, pid = state["port"], state["token"], state["pid"]
    if (state.get("app") != APP_ID or state.get("version") != version
            or not isinstance(port, int) or not 1 <= port <= 65535
            or not isinstance(token, str) or len(token) != 64
            or not isinstance(pid, int) or pid <= 0 or not process_alive(pid)):
        raise RuntimeError("No valid running B2B Contact Finder instance was found.")
    health(port, token, version)
    request(port, token, "/api/desktop/exit", data=b"")


def bind_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", 8765))
        except OSError as exc:
            import errno

            if exc.errno not in (errno.EADDRINUSE, errno.EACCES, 10048, 10013):
                raise
            listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)
        return listener
    except BaseException:
        listener.close()
        raise


def write_state(path, port, token, version):
    state = {"app": APP_ID, "version": version, "pid": os.getpid(), "port": port, "token": token}
    fd, temporary = tempfile.mkstemp(prefix="instance-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def remove_owned_state(path, token):
    if path is None:
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("pid") == os.getpid() and state.get("token") == token:
            path.unlink()
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        LOG.exception("Could not remove instance state")


class LocalServer:
    def __init__(self, app, listener):
        import uvicorn

        self.listener = listener
        self.error = None
        self.stopped = threading.Event()
        config = uvicorn.Config(app, host="127.0.0.1", log_config=None, access_log=False,
                                lifespan="on", timeout_graceful_shutdown=15)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.run, name="finder-server", daemon=False)

    def run(self):
        try:
            self.server.run(sockets=[self.listener])
        except BaseException as exc:
            self.error = exc
            LOG.exception("Local server failed")
        finally:
            self.stopped.set()

    def start(self, port, token, version):
        self.thread.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if not self.thread.is_alive():
                raise RuntimeError("Local server exited before startup completed") from self.error
            try:
                health(port, token, version)
                return
            except (OSError, ValueError, RuntimeError):
                time.sleep(0.1)
        raise RuntimeError("Local server did not become ready within 40 seconds")

    def stop(self):
        self.server.should_exit = True
        # Do not release the mutex or close SQLite from another thread. Uvicorn's
        # lifespan owns cancellation and database closure, including during startup.
        if self.thread.ident is not None:
            while not self.stopped.is_set():
                try:
                    self.stopped.wait(timeout=0.2)
                except KeyboardInterrupt:
                    # Keep ownership until lifespan closes SQLite, even on repeated Ctrl-C.
                    continue
            self.thread.join()
        self.listener.close()


def run_tray(server, port, token, version, log_path, exit_event):
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (64, 64), "#16324f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((12, 10, 40, 38), outline="white", width=5)
    draw.line((35, 34, 52, 51), fill="white", width=6)
    exiting = threading.Lock()

    def report_failure(action):
        try:
            action()
        except Exception as exc:
            LOG.exception("Desktop action failed")
            message(f"{exc}\n\nDiagnostic log: {log_path}")

    def open_action(icon=None, item=None):
        report_failure(lambda: open_browser(port, token))

    def logs_action(icon, item):
        if os.name == "nt":
            report_failure(lambda: os.startfile(str(log_path.parent)))
        else:
            report_failure(lambda: subprocess.Popen(
                ["/usr/bin/open", str(log_path.parent)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))

    def exit_action(icon, item):
        if not exiting.acquire(blocking=False):
            return
        try:
            try:
                active = health(port, token, version)["active_runs"]
            except Exception:
                LOG.exception("Could not check active scans before exit")
                active = None
            if active is None or active > 0:
                warning = ("The scan status could not be checked." if active is None
                           else f"{active} scan(s) are still running.")
                if not message(warning + "\nExit and stop any active scans?", warning=True):
                    return
            server.stop()
            icon.stop()
        finally:
            exiting.release()

    icon = pystray.Icon("B2BContactFinder", image, "B2B Contact Finder",
                        pystray.Menu(pystray.MenuItem("Open", open_action, default=True),
                                     pystray.MenuItem("Open logs", logs_action),
                                     pystray.MenuItem("Exit", exit_action)))
    # Unexpected server death must not leave a misleading, inert tray icon behind.
    monitor_done = threading.Event()

    def monitor():
        while not monitor_done.wait(0.5):
            if exit_event.is_set():
                server.stop()
                icon.stop()
                return
            if not server.thread.is_alive():
                icon.stop()
                return

    monitor_thread = threading.Thread(target=monitor, name="finder-monitor", daemon=True)
    monitor_thread.start()
    try:
        open_action()
        icon.run()
        if not server.server.should_exit:
            raise RuntimeError("Local server or desktop tray exited unexpectedly") from server.error
    finally:
        monitor_done.set()
        monitor_thread.join()


def smoke(port, token, version, app_module):
    """Exercise packaged resources and real HTTP handlers, without Internet searches."""
    from core.models import Result

    evidence = {"health": health(port, token, version)}
    try:
        request(port, "incorrect-token", "/api/health")
    except urllib.error.HTTPError as exc:
        if exc.code not in (401, 403):
            raise
        evidence["unauthorized_status"] = exc.code
    else:
        raise RuntimeError("Session authentication did not reject an invalid token")
    import http.cookiejar

    cookies = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                          urllib.request.HTTPCookieProcessor(cookies))
    try:
        with browser.open(browser_url(port, token), timeout=2) as response:
            root, headers = response.read(), response.headers
            if response.geturl() != f"http://127.0.0.1:{port}/":
                raise RuntimeError("Browser entry did not remove the token from its URL")
        with browser.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            if json.load(response).get("app") != APP_ID:
                raise RuntimeError("Browser session could not access the API")
    except Exception:
        # Never propagate a URL-bearing exception containing the launch secret.
        raise RuntimeError("Browser session-cookie handshake failed") from None
    if not any(cookie.has_nonstandard_attr("HttpOnly")
               and cookie.get_nonstandard_attr("SameSite", "").lower() == "strict" for cookie in cookies):
        raise RuntimeError("Browser session cookie is missing its security attributes")
    evidence["browser_session"] = True
    if "text/html" not in headers.get("Content-Type", "") or b"<html" not in root.lower():
        raise RuntimeError("Packaged browser UI was not served")
    evidence["ui_bytes"] = len(root)
    boundary = "finder-" + secrets.token_hex(12)
    upload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"smoke.csv\"\r\n"
              "Content-Type: text/csv\r\n\r\nCompany,Country\r\nFinder Smoke Company,US\r\n"
              f"\r\n--{boundary}--\r\n").encode()
    body, _ = request(port, token, "/api/upload", data=upload,
                      content_type=f"multipart/form-data; boundary={boundary}")
    uploaded = json.loads(body)
    plan = request_json(port, token, "/api/plan", {"file_id": uploaded["file_id"],
                        "company_col": "Company", "country_col": "Country"})
    if plan["job_count"] != 1 or plan["jobs"][0]["company"] != "Finder Smoke Company":
        raise RuntimeError(f"Unexpected CSV plan: {plan}")
    evidence["imported_jobs"] = plan["job_count"]
    # Synthetic local data is explicitly labelled; this is not a successful scan.
    run_id = app_module.store.create_run("Synthetic launcher smoke", "local fixture", 1, {})
    app_module.store.add_result(run_id, 0, Result(query="Finder Smoke Company", company="Finder Smoke Company",
                                                emails=["smoke@example.invalid"]))
    app_module.store.finish_run(run_id, "done", 0.0)
    history = request_json(port, token, "/api/runs")
    if not any(row["id"] == run_id and row["status"] == "done" for row in history["runs"]):
        raise RuntimeError("Completed synthetic history was not returned")
    detail = request_json(port, token, f"/api/runs/{run_id}")
    if detail["results"][0]["emails"] != ["smoke@example.invalid"]:
        raise RuntimeError("Synthetic contact did not survive persistence")
    exported, _ = request(port, token, f"/api/runs/{run_id}/export.csv")
    rows = list(csv.reader(io.StringIO(exported.decode("utf-8-sig"))))
    if len(rows) != 2 or not any("smoke@example.invalid" in field for field in rows[1]):
        raise RuntimeError("CSV export did not contain the synthetic contact")
    workbook, _ = request(port, token, f"/api/runs/{run_id}/export.xlsx")
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(workbook), read_only=True)
    try:
        values = list(book.active.values)
        if len(values) != 2 or not any("smoke@example.invalid" in str(value) for value in values[1]):
            raise RuntimeError("XLSX export did not contain the synthetic contact")
    finally:
        book.close()
    evidence.update(history_run=run_id, csv_rows=len(rows), xlsx_rows=len(values), synthetic=True,
                    external_network_used=False)
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description="B2B Contact Finder desktop launcher")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke-test", action="store_true", help="Run isolated offline HTTP smoke and exit")
    mode.add_argument("--stop", action="store_true", help="Ask a running desktop instance to exit")
    parser.add_argument("--smoke-output", type=Path, help="Write smoke JSON to this file (for windowed executables)")
    args = parser.parse_args(argv)
    if args.smoke_test and args.smoke_output is None and sys.stdout is None:
        args.smoke_output = Path(tempfile.gettempdir()) / "B2BContactFinder-smoke-result.json"
    mutex = server = listener = state_path = temporary_home = None
    token = None
    log_path = "the application log directory"
    result = {"ok": False}
    status = 1
    try:
        if os.name == "nt":
            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.GetNativeSystemInfo.argtypes = [ctypes.c_void_p]
            api.GetNativeSystemInfo.restype = None
            system_info = ctypes.create_string_buffer(64)
            api.GetNativeSystemInfo(system_info)
            native_arch = ctypes.c_ushort.from_buffer(system_info).value
            if native_arch != 9 or ctypes.sizeof(ctypes.c_void_p) != 8:
                raise RuntimeError("B2B Contact Finder requires Windows x64. ARM and 32-bit Windows are not supported.")
            mutex = WindowsMutex()
        elif sys.platform == "darwin":
            import platform
            if platform.machine().lower() not in ("arm64", "x86_64") or ctypes.sizeof(ctypes.c_void_p) != 8:
                raise RuntimeError("B2B Contact Finder requires a 64-bit Apple Silicon or Intel Mac.")
        elif not args.smoke_test:
            raise RuntimeError("The desktop launcher supports Windows and macOS only. Use main.py for development or --smoke-test.")
        if args.smoke_test:
            if mutex is not None and not mutex.owned:
                raise RuntimeError("Close B2B Contact Finder before running the installation smoke test.")
            temporary_home = tempfile.TemporaryDirectory(prefix="finder-smoke-")
            os.environ["FINDER_HOME"] = temporary_home.name
            os.environ.pop("FINDER_CACHE_DIR", None)
            os.environ.pop("FINDER_UPLOAD_DIR", None)
        from core.paths import log_dir, state_dir
        from core.version import VERSION

        state_path = Path(state_dir()) / "instance.json"
        if sys.platform == "darwin" and not args.smoke_test:
            mutex = PosixMutex(Path(state_dir()) / "instance.lock")
        if args.stop and mutex is not None and mutex.owned:
            raise RuntimeError("B2B Contact Finder is not running.")
        if mutex is not None and not mutex.owned:
            if args.stop:
                stop_existing(state_path, VERSION)
            else:
                open_existing(state_path, VERSION)
            return 0
        # Exclusive owner can discard crashed-instance state, never another live owner's.
        state_path.unlink(missing_ok=True)
        log_path = configure_logs(log_dir())
        LOG.info("Starting desktop version %s", VERSION)
        token = secrets.token_hex(32)
        os.environ["FINDER_SESSION_TOKEN"] = token
        import web.app as app_module
        app_module.app.state.desktop_exit = threading.Event()

        listener = bind_listener()
        port = listener.getsockname()[1]
        server = LocalServer(app_module.app, listener)
        server.start(port, token, VERSION)
        write_state(state_path, port, token, VERSION)
        if args.smoke_test:
            result = {"ok": True, "version": VERSION, "checks": smoke(port, token, VERSION, app_module)}
        else:
            run_tray(server, port, token, VERSION, log_path, app_module.app.state.desktop_exit)
        status = 0
    except KeyboardInterrupt:
        result = {"ok": False, "error": "Interrupted"}
        status = 130
    except BaseException as exc:
        if not isinstance(log_path, Path):
            try:
                fallback = Path(tempfile.gettempdir()) / "B2BContactFinder" / "logs"
                fallback.mkdir(parents=True, exist_ok=True)
                log_path = configure_logs(fallback)
            except OSError:
                log_path = "unavailable (the application and temporary log directories are not writable)"
        LOG.exception("Desktop startup or runtime failed")
        result = {"ok": False, "error": str(exc), "log": str(log_path)}
        if not args.smoke_test:
            message(f"B2B Contact Finder could not continue.\n\n{exc}\n\nDiagnostic log: {log_path}")
    finally:
        try:
            if server is not None:
                server.stop()
            elif listener is not None:
                listener.close()
        finally:
            remove_owned_state(state_path, token)
            if mutex is not None:
                mutex.close()
        if args.smoke_test:
            result["shutdown_complete"] = server is not None and not server.thread.is_alive()
            if args.smoke_output:
                args.smoke_output.parent.mkdir(parents=True, exist_ok=True)
                args.smoke_output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            if sys.stdout is not None:
                print(json.dumps(result), flush=True)
            logging.shutdown()
            if temporary_home is not None:
                temporary_home.cleanup()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
