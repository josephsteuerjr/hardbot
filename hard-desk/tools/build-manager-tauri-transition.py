"""Create the offline release assets for the Manager 3.5.0 transition."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


DESK = Path(__file__).resolve().parents[1]
CHANNEL = "cis-collection-tauri-stable"
ORIGIN = "https://hard.rimborso.ru"
RELEASE = "https://github.com/josephsteuerjr/hard-desktop-releases/releases/download"


def decode(data):
    return base64.b64decode(data, validate=True)


def signed_installer(release_dir):
    envelope = json.loads((release_dir / "manager-signed.json").read_text(encoding="utf-8"))
    signed = decode(envelope["payload"])
    public = json.loads((DESK / "src/main/release-public-key.json").read_text(encoding="utf-8"))
    key = serialization.load_pem_public_key(public["pem"].encode())
    key.verify(decode(envelope["signature"]), signed)
    meta = json.loads(signed)
    version = meta["version"]
    if (not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            or meta["product"] != "manager" or meta["file"] != f"manager-{version}-setup.exe"):
        raise ValueError("Expected an exact signed stable Manager installer")
    path = release_dir / meta["file"]
    sha256, sha512 = hashlib.sha256(), hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
            sha512.update(chunk)
    if path.stat().st_size != meta["size"] or sha512.digest() != decode(meta["sha512"]):
        raise ValueError("Installer and signed metadata differ")
    return dict(version=version, name=path.name, bytes=meta["size"], sha256=sha256.hexdigest(),
                url=f"{RELEASE}/v{version}/{path.name}")


def verify_tauri(public, signature, payload, version):
    key = decode(decode(public.read_bytes().strip()).splitlines()[1])
    lines = decode(signature.encode()).splitlines()
    record = decode(lines[1])
    prefix = b"trusted comment: "
    if (len(key) != 42 or len(record) != 74 or record[:2] != b"ED" or key[2:10] != record[2:10]
            or not lines[2].startswith(prefix)):
        raise ValueError("Tauri signature has an unexpected format or key")
    trusted = lines[2][len(prefix):]
    verifier = Ed25519PublicKey.from_public_bytes(key[10:])
    verifier.verify(record[10:], hashlib.blake2b(payload).digest())
    verifier.verify(decode(lines[3]), record[10:] + trusted)
    if f"version:{version}".encode() not in trusted.split(b"\t"):
        raise ValueError("No matching signed Tauri version")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tauri-private-key", type=Path, required=True)
    parser.add_argument("--tauri-public-key", type=Path, required=True)
    parser.add_argument("--version", default="3.5.1")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version) or tuple(map(int, args.version.split("."))) <= (3, 5, 0):
        parser.error("The signed transition version must be greater than 3.5.0")
    payload = signed_installer(args.release_dir.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = output / "manager-transition-payload.g.cs"
    settings.write_text(
        "internal static class ManagerTransitionPayload {\n"
        f'    internal const string Url = "{payload["url"]}";\n'
        f'    internal const string Sha256 = "{payload["sha256"]}";\n'
        f'    internal const long Length = {payload["bytes"]}L;\n'
        "}\n", encoding="utf-8")
    installer = output / f"manager-{args.version}-transition.exe"
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/codepage:65001",
                    "/reference:System.Drawing.dll", "/reference:System.Security.dll",
                    "/reference:System.Web.Extensions.dll", "/reference:System.Windows.Forms.dll",
                    f"/out:{installer}", str(DESK / "installer/manager-tauri-transition.cs"), str(settings)], check=True)
    if installer.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Transition exceeds the 3.5.0 download limit")
    command = ["npm.cmd", "exec", "--yes", "--package", "@tauri-apps/cli@2.12.6", "--", "tauri",
               "signer", "sign", "--private-key-path", str(args.tauri_private_key.resolve()),
               "--password", "", "--app-version", args.version, str(installer)]
    subprocess.run(command, check=True)
    signature = installer.with_suffix(".exe.sig").read_text(encoding="utf-8").strip()
    data = installer.read_bytes()
    verify_tauri(args.tauri_public_key, signature, data, args.version)
    url = f"{ORIGIN}/updates/{CHANNEL}/manager/{args.version}/{installer.name}"
    write_json(output / "manager.json", dict(schema=1, product="manager", channel=CHANNEL, version=args.version,
               notes=f"Переход на Руководитель CRM {payload['version']}",
               platforms={"windows-x86_64": dict(url=url, signature=signature)}))
    write_json(output / "BUILD.private.json", dict(version=args.version, bytes=len(data),
               sha256=hashlib.sha256(data).hexdigest(), manager=payload, tauri_signature_verified=True,
               checked_at=int(time.time()), published=False))


if __name__ == "__main__":
    main()
