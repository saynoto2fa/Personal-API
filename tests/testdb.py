"""Throwaway local Postgres 16 + pgvector for the test suite, installed into .testdb/ (git-ignored).

    python -m tests.testdb setup    # one time: download micromamba, install postgres + pgvector, init
    python -m tests.testdb start    # start the server (pytest does this automatically)
    python -m tests.testdb stop
    python -m tests.testdb status
    python -m tests.testdb reset    # delete the test data folder and re-create it empty

No admin rights, no Windows service, no Docker. The server listens on localhost:54329 only, with
trust auth and fsync off: it is for disposable test data and nothing else.
"""

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / ".testdb"
ENV = ROOT / "env"
DATA = ROOT / "data"
LOG = ROOT / "postgres.log"
PORT = 54329
DB_NAME = "personal_api_test"
URL = f"postgresql://postgres@127.0.0.1:{PORT}/{DB_NAME}"
ADMIN_URL = f"postgresql://postgres@127.0.0.1:{PORT}/postgres"

IS_WINDOWS = os.name == "nt"
MICROMAMBA = ROOT / ("micromamba.exe" if IS_WINDOWS else "micromamba")
MICROMAMBA_URL = {
    ("Windows", "AMD64"): "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64",
    ("Linux", "x86_64"): "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64",
    ("Darwin", "arm64"): "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-osx-arm64",
    ("Darwin", "x86_64"): "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-osx-64",
}
# conda-forge only builds pgvector for Windows against Postgres 16. Supabase runs 17, but nothing in
# the migrations (gen_random_uuid, HNSW, pgvector 0.8 iterative scans) differs between the two.
PACKAGES = ["postgresql=16", "pgvector>=0.8"]


def _bin(name: str) -> Path:
    sub = ENV / "Library" / "bin" if IS_WINDOWS else ENV / "bin"
    return sub / (f"{name}.exe" if IS_WINDOWS else name)


def installed() -> bool:
    return _bin("pg_ctl").exists() and (DATA / "PG_VERSION").exists()


def _run(args: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], stdin=subprocess.DEVNULL, capture_output=True, text=True, **kw)


def running() -> bool:
    try:
        with psycopg.connect(ADMIN_URL, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


def setup() -> None:
    ROOT.mkdir(exist_ok=True)
    if not MICROMAMBA.exists():
        url = MICROMAMBA_URL.get((platform.system(), platform.machine()))
        if not url:
            raise SystemExit(f"No micromamba download known for {platform.system()} {platform.machine()}")
        print(f"downloading micromamba from {url}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(MICROMAMBA, "wb") as f:
                for block in r.iter_bytes():
                    f.write(block)
        MICROMAMBA.chmod(0o755)
    if not _bin("pg_ctl").exists():
        print(f"installing {' '.join(PACKAGES)} from conda-forge into {ENV} ...")
        env = {**os.environ, "MAMBA_ROOT_PREFIX": str(ROOT / "mamba")}
        r = _run([MICROMAMBA, "create", "-y", "-p", ENV, "-c", "conda-forge", "--override-channels", *PACKAGES], env=env)
        if r.returncode != 0:
            raise SystemExit(f"micromamba failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        _run([MICROMAMBA, "clean", "-a", "-y"], env=env)  # drop the ~350 MB download cache
    if not (DATA / "PG_VERSION").exists():
        print(f"initializing data folder {DATA}")
        r = _run([_bin("initdb"), "-D", DATA, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C"])
        if r.returncode != 0:
            raise SystemExit(f"initdb failed:\n{r.stdout}\n{r.stderr}")
    start()
    print(f"ready: {URL}")


def start() -> None:
    if running():
        _ensure_database()
        return
    if not installed():
        raise SystemExit("Test database is not installed. Run: python -m tests.testdb setup")
    opts = f"-p {PORT} -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
    # No pipes here: the postgres process pg_ctl leaves behind would inherit them and the call would
    # never return. The server logs through -l; pg_ctl's own chatter is dropped (it can't share the
    # log file on Windows either).
    r = subprocess.run(
        [str(a) for a in (_bin("pg_ctl"), "-D", DATA, "-l", LOG, "-o", opts, "-w", "-t", "60", "start")],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    if r.returncode != 0 and not running():
        raise SystemExit(f"pg_ctl start failed; see {LOG}")
    for _ in range(50):
        if running():
            break
        time.sleep(0.2)
    _ensure_database()


def _ensure_database() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone():
            conn.execute(f'CREATE DATABASE "{DB_NAME}"')


def stop() -> None:
    if installed() and running():
        _run([_bin("pg_ctl"), "-D", DATA, "-m", "fast", "-w", "stop"])


def reset() -> None:
    stop()
    shutil.rmtree(DATA, ignore_errors=True)
    setup()


def status() -> None:
    print(f"installed: {installed()}  running: {running()}  url: {URL}")
    if running():
        with psycopg.connect(URL) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            vector = conn.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'").fetchone()
        print(f"postgres {version}, pgvector available: {vector[0] if vector else 'NO'}")


if __name__ == "__main__":
    commands = {"setup": setup, "start": start, "stop": stop, "status": status, "reset": reset}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    commands[sys.argv[1]]()
