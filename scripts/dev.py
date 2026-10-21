"""Run the local shared Code Studio stack. Ctrl-C stops child processes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


async def ensure_namespace() -> None:
    from google.protobuf.duration_pb2 import Duration
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, RegisterNamespaceRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    client = await Client.connect("localhost:7233")
    try:
        await client.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace="code-studio")
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        await client.workflow_service.register_namespace(
            RegisterNamespaceRequest(
                namespace="code-studio",
                workflow_execution_retention_period=Duration(seconds=3 * 24 * 60 * 60),
            )
        )


def main() -> None:
    os.chdir(ROOT)
    for executable in ("temporal", "npm"):
        if shutil.which(executable) is None:
            raise SystemExit(f"Install {executable} before starting the studio.")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in .env.local, then run just dev.")
    if not (ROOT / "node_modules/.bin/vite").exists():
        raise SystemExit("Run npm ci before starting the studio.")
    for port in (8001, 8002, 5174):
        if listening(port):
            raise SystemExit(f"Port {port} is already in use. Stop that instance first.")

    env = dict(os.environ)
    # The all-in-one command is deliberately local. Individual recipes can use other profiles.
    env.update(
        TEMPORAL_CONFIG_FILE=str(ROOT / "temporal.local.toml"),
        TEMPORAL_PROFILE="default",
        TEMPORAL_ADDRESS="localhost:7233",
        TEMPORAL_NAMESPACE="code-studio",
        PYTHONUNBUFFERED="1",
    )
    children: list[tuple[str, subprocess.Popen]] = []
    log_files = []
    (ROOT / ".local/logs").mkdir(parents=True, exist_ok=True)

    def start(name: str, command: list[str]) -> None:
        log = (ROOT / ".local/logs" / f"{name}.log").open("w")
        log_files.append(log)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log, start_new_session=True)
        children.append((name, process))
        print(f"Starting {name} — .local/logs/{name}.log", flush=True)

    def check_children() -> None:
        for name, process in children:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited ({process.returncode}). See .local/logs/{name}.log.")

    def wait_for(port: int) -> None:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            check_children()
            if listening(port):
                return
            time.sleep(0.25)
        raise RuntimeError(f"Timed out waiting for port {port}. See .local/logs/.")

    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        if listening(7233):
            print("Using the existing local Temporal server on :7233.", flush=True)
        else:
            start("temporal", ["temporal", "server", "start-dev", "--namespace", "code-studio",
                               "--db-filename", str(ROOT / ".local/temporal.db")])
            wait_for(7233)
        asyncio.run(asyncio.wait_for(ensure_namespace(), timeout=30))

        harness = str(Path(sys.executable).parent / "temporal-agent-harness")
        start("session-manager", [harness, "session-manager"])
        start("worker", [sys.executable, "-m", "agent_dag.worker"])
        start("server", [sys.executable, "-m", "scripts.harness_server"])
        wait_for(8001)
        # `serve` includes the packaged debugger as well as the API.
        with urlopen('http://127.0.0.1:8001/', timeout=10) as response:
            if b'Temporal Agent UI' not in response.read():
                raise RuntimeError('Harness UI assets are unavailable. Reinstall the pinned harness UI dependency.')
        start("workspace", [sys.executable, "-m", "workspace.server"])
        wait_for(8002)
        start("studio", ["npm", "run", "dev"])
        wait_for(5174)
        print("\nCode Studio: http://127.0.0.1:5174\nHarness UI: http://127.0.0.1:8001/\nOpen in two browser sessions to collaborate.\nCtrl-C stops this stack.", flush=True)
        while True:
            check_children()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping studio processes…", flush=True)
    finally:
        for _name, process in reversed(children):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for _name, process in reversed(children):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        for log in log_files:
            log.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(str(exc)) from exc
