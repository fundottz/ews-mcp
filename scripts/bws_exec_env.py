#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOKEN_FILE = Path.home() / '.bws_token'
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def resolve_bws_bin() -> str:
    configured = os.environ.get('BWS_BIN', '').strip()
    if configured:
        return configured
    resolved = shutil.which('bws')
    if resolved:
        return resolved
    raise RuntimeError('bws binary is not available on PATH; install it and ensure non-interactive PATH includes ~/.local/bin')


def load_access_token() -> str:
    token = os.environ.get('BWS_ACCESS_TOKEN', '').strip()
    if token:
        return token
    if TOKEN_FILE.exists():
        for line in TOKEN_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(r"(?:export\s+)?BWS_ACCESS_TOKEN=(.*)$", line)
            if not m:
                continue
            value = m.group(1).strip()
            if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
                value = value[1:-1]
            value = value.strip()
            if value:
                return value
    raise RuntimeError('BWS_ACCESS_TOKEN not found in environment or ~/.bws_token')


def run_bws_json(args: list[str], token: str):
    env = os.environ.copy()
    env['BWS_ACCESS_TOKEN'] = token
    proc = subprocess.run(
        [resolve_bws_bin()] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or '').strip()
        raise RuntimeError(err or f'bws exited with code {proc.returncode}')
    return json.loads(proc.stdout)


def build_secret_index(token: str) -> dict[str, list[str]]:
    secrets = run_bws_json(['secret', 'list', '-o', 'json'], token) or []
    by_key: dict[str, list[str]] = {}
    for item in secrets:
        if not isinstance(item, dict):
            continue
        key = item.get('key')
        secret_id = item.get('id')
        if isinstance(key, str) and key and isinstance(secret_id, str) and secret_id:
            by_key.setdefault(key, []).append(secret_id)
    return by_key


def resolve_secret_id(secret_ref: str, by_key: dict[str, list[str]]) -> str:
    if UUID_RE.fullmatch(secret_ref):
        return secret_ref
    matches = by_key.get(secret_ref, [])
    if not matches:
        raise RuntimeError(f'Secret with key {secret_ref!r} not found')
    if len(matches) > 1:
        raise RuntimeError(f'Secret key {secret_ref!r} is ambiguous ({len(matches)} matches)')
    return matches[0]


def get_secret(secret_ref: str, token: str, by_key: dict[str, list[str]]) -> str:
    secret_id = resolve_secret_id(secret_ref, by_key)
    data = run_bws_json(['secret', 'get', secret_id, '-o', 'json'], token)
    value = data.get('value')
    if not isinstance(value, str) or value == '':
        raise RuntimeError('Secret resolved to empty or non-string value')
    return value


def main() -> int:
    if len(sys.argv) < 5 or '--' not in sys.argv[3:]:
        print('Usage: bws_exec_env.py ENV_NAME SECRET_REF -- command [args...]', file=sys.stderr)
        return 2

    env_name = sys.argv[1]
    secret_ref = sys.argv[2]
    sep = sys.argv.index('--', 3)
    command = sys.argv[sep + 1:]
    if not command:
        print('Command is required after --', file=sys.stderr)
        return 2

    token = load_access_token()
    by_key = build_secret_index(token)
    value = get_secret(secret_ref, token, by_key)
    os.environ[env_name] = value
    os.execvpe(command[0], command, os.environ)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
