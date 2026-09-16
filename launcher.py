import subprocess
import time
import sys
import os
import urllib.request
import webview

PORT = 8501
URL = f"http://localhost:{PORT}"

# Locate virtual environment python
VENV_PYTHON = os.path.join(os.path.dirname(__file__), "venv", "Scripts", "python.exe")
PYTHON_EXE = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable

def wait_for_server(url, timeout=30):
    """Poll Streamlit HTTP endpoint until it returns a 200 OK status."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def launch_gui():
    cmd = [
        PYTHON_EXE, "-m", "streamlit", "run", "app.py",
        "--server.headless=true",
        f"--server.port={PORT}",
        "--server.address=localhost",
        "--browser.serverAddress=localhost"
    ]
    
    print(f"Using Python: {PYTHON_EXE}")
    print(f"Starting Streamlit server on {URL}...")
    proc = subprocess.Popen(cmd)

    ready = wait_for_server(URL, timeout=30)
    if not ready:
        print("\n[ERROR] Streamlit failed to respond within 30 seconds.")
        proc.terminate()
        return

    print("✓ Server live! Opening desktop terminal window...")
    try:
        webview.create_window(
            title="NSE Institutional Quant Terminal",
            url=URL,
            width=1320,
            height=880,
            resizable=True
        )
        webview.start()
    finally:
        proc.terminate()

if __name__ == "__main__":
    # Check if user wants background daemon mode
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        from core.scheduler import start_daemon_clock
        start_daemon_clock()
    else:
        launch_gui()