"""桌面版 A 股量化控制台。

用 pywebview 打开原生窗口，内嵌 FastAPI 后端 + 已构建的 Vue 前端（web/dist）。
窗口关闭后自动停止本次启动的后端；若 8000 端口已有兼容后端则直接复用。

用法：
    python desktop_app.py                 # 打开桌面窗口
    python desktop_app.py --no-gui        # 只启动后端并自检后退出
    python desktop_app.py --rebuild-frontend
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
WEB_DIST = WEB_DIR / "dist"
LOG_DIR = ROOT / "logs"
HEALTH_PATH = "/api/health"
EXPECTED_VERSION = "0.2.0"


def find_free_port(preferred: int | None = None) -> int:
    """返回可绑定的端口；preferred 空闲时优先使用。"""
    if preferred is not None:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", int(preferred)))
                return int(preferred)
            except OSError:
                pass
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def wait_for_http(url: str, timeout: float = 30.0, interval: float = 0.3) -> bool:
    """轮询等待 URL 可访问。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 400:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def is_compatible_backend(url: str, timeout: float = 1.0) -> bool:
    """检查 URL 是否为兼容的 oversell 后端。

    返回 True 表示可直接复用；返回 False 表示该地址没有服务；
    若地址有服务但不是本项目后端，则抛出 RuntimeError。
    """
    payload = _http_json(f"{url}{HEALTH_PATH}", timeout)
    if payload is None:
        return False
    if payload.get("app") != "oversell":
        raise RuntimeError(f"{url} 已被其他服务占用，桌面版将改用空闲端口。")
    if payload.get("version") != EXPECTED_VERSION:
        raise RuntimeError(f"{url} 上是旧版后端，请先关闭旧控制台。")
    return True


def build_backend_command(port: int, python: str | None = None) -> list[str]:
    return [
        python or sys.executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def ensure_frontend_built(rebuild: bool = False) -> None:
    """确保 web/dist 存在；缺失时自动 npm install + build。"""
    if WEB_DIST.exists() and not rebuild:
        return
    npm = shutil.which("npm")
    if not npm:
        system_npm = Path(r"C:\Program Files\nodejs\npm.cmd")
        npm = str(system_npm) if system_npm.exists() else ""
    if not npm:
        raise RuntimeError("找不到 npm，请先安装 Node.js")
    env = os.environ.copy()
    if not (WEB_DIR / "node_modules").exists():
        print("[INFO] web/node_modules 不存在，开始 npm install ...")
        subprocess.run([npm, "install"], cwd=str(WEB_DIR), check=True, env=env)
    print("[INFO] 开始构建前端 web/dist ...")
    subprocess.run([npm, "run", "build"], cwd=str(WEB_DIR), check=True, env=env)


def _start_backend(port: int) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_DIR / "desktop_backend.log", "a", encoding="utf-8")
    log_file.write(f"\n===== desktop start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    log_file.flush()
    return subprocess.Popen(
        build_backend_command(port),
        cwd=str(ROOT),
        stdout=log_file,
        stderr=log_file,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="桌面版 A 股量化控制台")
    parser.add_argument("--port", type=int, default=8000, help="后端端口（默认 8000）")
    parser.add_argument("--no-gui", action="store_true", help="只启动后端并自检后退出")
    parser.add_argument("--rebuild-frontend", action="store_true", help="强制重新构建前端")
    args = parser.parse_args()

    try:
        ensure_frontend_built(rebuild=args.rebuild_frontend)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 前端准备失败：{exc}")
        return 1

    url = f"http://127.0.0.1:{args.port}"
    try:
        reuse = is_compatible_backend(url)
    except RuntimeError as exc:
        print(f"[WARN] {exc}")
        reuse = False
    port = args.port if reuse else find_free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    backend_proc: subprocess.Popen | None = None
    if reuse:
        print(f"[INFO] 检测到已有后端服务，直接复用：{url}")
    else:
        print(f"[INFO] 启动后端：{url}")
        backend_proc = _start_backend(port)
        if not wait_for_http(f"{url}{HEALTH_PATH}", timeout=30):
            print("[ERROR] 后端启动超时，详见 logs/desktop_backend.log")
            if backend_proc and backend_proc.poll() is None:
                backend_proc.terminate()
            return 1
        print("[INFO] 后端就绪")

    if args.no_gui:
        print("[INFO] --no-gui 自检完成")
        if backend_proc and backend_proc.poll() is None:
            backend_proc.terminate()
        return 0

    try:
        import webview
    except ImportError:
        print("[ERROR] 未安装 pywebview，请先执行 pip install pywebview")
        if backend_proc and backend_proc.poll() is None:
            backend_proc.terminate()
        return 1

    webview.create_window("A 股量化控制台", url, width=1280, height=860)
    webview.start()
    print("[INFO] 桌面窗口已关闭，正在停止后端...")
    if backend_proc and backend_proc.poll() is None:
        backend_proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
