"""Exact public source allowlist shared by the runtime builder and EXE auditor."""
import hashlib
import json
import subprocess
from pathlib import Path

SCHEMAS = {
    "server/data_exchange_contract.json",
    *{f"registry_cycle/config/{name}.json" for name in
      ("app", "fields", "theme", "distribution", "recovery")},
}


def allowed(path):
    return (path.endswith(".py") and path.startswith(("pult/", "registry_cycle/", "server/"))
            or path in SCHEMAS)


def source_manifest(repo):
    repo = Path(repo).resolve()
    def files(*options):
        result = subprocess.check_output(
            ["git", "-c", "core.fsmonitor=false", "ls-files", "-z", *options], cwd=repo)
        return sorted({p for p in result.decode("utf-8").split("\0") if p and allowed(p)})
    missing = files("--others", "--exclude-standard")
    if missing:
        raise RuntimeError("Backend sources are absent from Git's source list: " + ", ".join(missing))
    sources = []
    for rel in files("--cached"):
        path = repo / rel
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(repo):
            raise RuntimeError("Missing or unsafe backend source: " + rel)
        sources.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if not SCHEMAS.issubset({s["path"] for s in sources}):
        raise RuntimeError("Missing public backend schemas")
    return {"schema": 1, "files": sources}


if __name__ == "__main__":
    print(json.dumps(source_manifest(Path(__file__).resolve().parents[2]), ensure_ascii=True))
