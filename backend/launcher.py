import logging
import multiprocessing
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser

import uvicorn

if __package__ in {None, ""}:
    sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import app  # noqa: E402
from backend.paths import DATA_DIR  # noqa: E402


HOST = "127.0.0.1"
PREFERRED_PORT = int(os.environ.get("BINGDU_PORT", "5173"))
OPEN_BROWSER = os.environ.get("BINGDU_OPEN_BROWSER", "1") != "0"


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.4)
        return client.connect_ex((HOST, port)) == 0


def _is_bingdu(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=0.6) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and payload.get("app") == "bingdu"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _select_port() -> tuple[int, bool]:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 30):
        if not _port_is_open(port):
            return port, False
        if _is_bingdu(port):
            return port, True
    raise RuntimeError("5173-5202 端口均被占用，冰读无法启动")


def _open_when_ready(port: int) -> None:
    url = f"http://{HOST}:{port}/"
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{url}api/health", timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("app") == "bingdu":
                    if OPEN_BROWSER:
                        webbrowser.open(url)
                    return
        except (OSError, ValueError, json.JSONDecodeError):
            time.sleep(0.2)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "bingdu.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )
    port, already_running = _select_port()
    url = f"http://{HOST}:{port}/"
    if already_running:
        if OPEN_BROWSER:
            webbrowser.open(url)
        return
    logging.info("Selected local port %s", port)
    threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
    try:
        # PyInstaller 的 --windowed 模式没有 stdout/stderr。关闭 Uvicorn 的
        # 控制台日志配置，避免它访问不存在的终端后直接退出。
        uvicorn.run(
            app,
            host=HOST,
            port=port,
            log_level="info",
            access_log=False,
            log_config=None,
        )
    except Exception:
        (DATA_DIR / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
