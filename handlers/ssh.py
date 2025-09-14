"""Optional SSH command execution using Paramiko."""

import os
from typing import Optional

import paramiko

ALLOWED_HOSTS = os.environ.get("SSH_ALLOWED_HOSTS", "").split(",")


def execute_command(host: str, username: str, key_path: str, command: str) -> Optional[str]:
    """Execute a remote command with basic safety checks."""
    if host not in ALLOWED_HOSTS:
        raise RuntimeError("Host not allowed")
    if any(bad in command for bad in ["rm -rf", ":(){:|:&};:"]):
        raise RuntimeError("Dangerous command")
    try:
        key = paramiko.RSAKey.from_private_key_file(key_path)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, username=username, pkey=key)
        _, stdout, _ = client.exec_command(command)
        output = stdout.read().decode()
        client.close()
        return output
    except Exception as exc:  # pragma: no cover - SSH
        print(f"SSH failed: {exc}")
        return None
