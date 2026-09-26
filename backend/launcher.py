import logging
import multiprocessing
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
PORT = int(os.environ.get("BINGDU_PORT", "5173"))
URL = f"http://{HOST}:{PORT}/"
OPEN_BROWSER = os.environ.get("BINGDU_OPEN_BROWSER", "1") != "0"


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.4)
        return client.connect_ex((HOST, PORT)) == 0


def _open_when_ready() -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"{URL}api/health", timeout=0.5) as response:
                if response.status == 200:
                    if OPEN_BROWSER:
                        webbrowser.open(URL)
                    return
        except OSError:
            time.sleep(0.2)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=DATA_DIR / "bingdu.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )
    if _port_is_open():
        if OPEN_BROWSER:
            webbrowser.open(URL)
        return
    threading.Thread(target=_open_when_ready, daemon=True).start()
    try:
        # PyInstaller 的 --windowed 模式没有 stdout/stderr。关闭 Uvicorn 的
        # 控制台日志配置，避免它访问不存在的终端后直接退出。
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
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
