#!/usr/bin/env python3
"""Pull verified public Release assets; deploy only this Compose project, with rollback.

Run as root via masa-update.timer. --local accepts an already verified local bundle.
No GitHub token or SSH key is needed for a public repository.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path('/opt/masa')
REPO = 'Alem42/Evidence-driven-Self-Optimizing-Software-Engineering-Agent-Runtime'
FILES = ('masa-image.tar.gz', 'compose.cloud.yml', 'check_cloud.py')


def command(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def download(url, path, limit):
    # URLs come only from the public GitHub Release API.
    if not url.startswith(('https://api.github.com/', 'https://github.com/')):
        raise ValueError('unexpected Release URL')
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'masa-release-sync'})
    with urllib.request.urlopen(request, timeout=60) as response, path.open('wb') as handle:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError('Release asset exceeds size limit')
            handle.write(chunk)


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def compose(record, args, **kwargs):
    env = {**os.environ, 'MASA_IMAGE': record['image_id']}
    return command(['docker', 'compose', '--env-file', str(ROOT / '.env'), '-p', 'masa-cloud',
                    '-f', str(Path(record['directory']) / 'compose.cloud.yml'), *args], env=env, **kwargs)


def health(record):
    for _ in range(45):
        result = compose(record, ['ps', '-q', 'masa'], capture_output=True)
        container = result.stdout.strip()
        if container:
            status = command(['docker', 'inspect', '--format', '{{.State.Status}}|{{.State.Health.Status}}', container],
                             capture_output=True).stdout.strip()
            if status == 'running|healthy':
                return
            if status.startswith(('exited|', 'dead|')):
                break
        time.sleep(2)
    raise RuntimeError('MASA health check failed')


def probe(record, idle=False):
    script = (Path(record['directory']) / 'check_cloud.py').read_text(encoding='utf-8')
    args = ['exec', '-T']
    if idle:
        args += ['-e', 'MASA_REQUIRE_IDLE=1']
    compose(record, [*args, 'masa', 'python', '-'], input=script)


def install(bundle, manifest):
    if manifest.get('schema') != 1 or not re.fullmatch(r'[0-9a-f]{40}', manifest.get('revision', '')):
        raise ValueError('invalid Release manifest revision')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', manifest.get('image_id', '')):
        raise ValueError('invalid image identity')
    if not re.fullmatch(r'masa-release:[A-Za-z0-9_.-]+', manifest.get('image_tag', '')):
        raise ValueError('invalid image tag')
    if not manifest.get('image_revision'):
        raise ValueError('missing image revision label')
    for name in FILES:
        expected = manifest.get('sha256', {}).get(name, '')
        if not re.fullmatch(r'[0-9a-f]{64}', expected) or digest(bundle / name) != expected:
            raise ValueError('asset checksum mismatch: ' + name)
    directory = ROOT / 'releases' / manifest['revision']
    directory.mkdir(parents=True, exist_ok=True)
    # A commit's first verified deployment bundle is retained unchanged for rollback.
    for name in ('compose.cloud.yml', 'check_cloud.py'):
        target = directory / name
        if target.exists() and digest(target) != manifest['sha256'][name]:
            raise ValueError('revision already exists with different configuration')
        if not target.exists():
            shutil.copy2(bundle / name, target)
    record = {k: manifest[k] for k in ('revision', 'image_id', 'tag')}
    record['source_image_id'] = manifest['image_id']
    record['directory'] = str(directory)
    record['asset_sha256'] = manifest['sha256']
    previous = read_json(ROOT / 'current.json')
    if previous:
        probe(previous, idle=True)  # Fail before stopping any active task.
    compose(record, ['config', '--quiet'])
    command(['docker', 'image', 'load', '--input', str(bundle / 'masa-image.tar.gz')])
    # Classic and containerd stores can identify the same archive differently.
    # Verify the archive hash and revision label, then pin the server's own ID.
    imported = json.loads(command(['docker', 'image', 'inspect', manifest['image_tag']], capture_output=True).stdout)[0]
    if imported.get('Config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != manifest['image_revision']:
        raise ValueError('imported image revision label mismatch')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', imported.get('Id', '')):
        raise ValueError('invalid imported image identity')
    record['image_id'] = imported['Id']
    try:
        compose(record, ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120'])
        health(record)
        probe(record)
    except Exception:
        if previous:
            print('Deployment failed; restoring ' + previous['tag'], flush=True)
            compose(previous, ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120'])
            health(previous)
            probe(previous)
        raise
    if previous:
        atomic_json(ROOT / 'previous.json', previous)
    atomic_json(ROOT / 'current.json', record)
    # Keep only this application's current/previous image; never run a global prune.
    if previous:
        keep = {record['image_id'], previous['image_id']}
        result = command(['docker', 'image', 'ls', '--filter', 'reference=masa-release:*',
                          '--format', '{{.Repository}}:{{.Tag}} {{.ID}}', '--no-trunc'], capture_output=True)
        for line in result.stdout.splitlines():
            tag, image_id = line.split()
            if image_id not in keep:
                subprocess.run(['docker', 'image', 'rm', tag], check=False)
    print('Deployed ' + record['tag'] + ' (' + record['image_id'] + ')', flush=True)


def sync(force=False):
    with tempfile.TemporaryDirectory(prefix='release-', dir=ROOT / 'downloads') as temp:
        bundle = Path(temp)
        try:
            download('https://api.github.com/repos/' + REPO + '/releases/latest', bundle / 'release.json', 2 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                print('No published stable Release yet.')
                return
            raise
        release = read_json(bundle / 'release.json')
        if release['draft'] or release['prerelease']:
            return
        assets = {asset['name']: asset for asset in release['assets'] if asset['state'] == 'uploaded'}
        if not all(name in assets for name in (*FILES, 'masa-release.json')):
            print('Release build has not uploaded all verified assets yet.')
            return
        download(assets['masa-release.json']['browser_download_url'], bundle / 'masa-release.json', 65536)
        manifest = read_json(bundle / 'masa-release.json')
        if manifest['tag'] != release['tag_name']:
            raise ValueError('manifest/Release tag mismatch')
        current = read_json(ROOT / 'current.json')
        if current and current.get('source_image_id', current['image_id']) == manifest['image_id']:
            return
        failed = read_json(ROOT / 'failed.json')
        if not force and failed and failed.get('image_id') == manifest['image_id']:
            print('Previously failed image skipped; use --force after resolving the failure.')
            return
        for name in FILES:
            download(assets[name]['browser_download_url'], bundle / name,
                     1024 * 1024 * 1024 if name.endswith('.gz') else 1024 * 1024)
        try:
            install(bundle, manifest)
        except Exception as error:
            # An active task is transient: retry at the next timer tick.
            if isinstance(error, subprocess.CalledProcessError) and 'exec' in error.cmd:
                raise
            atomic_json(ROOT / 'failed.json', {'tag': manifest['tag'], 'image_id': manifest['image_id'], 'error': str(error)})
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', type=Path, help='install a local bundle instead of fetching GitHub')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / 'downloads').mkdir(exist_ok=True)
    with (ROOT / 'update.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another deployment is running.')
            return
        if args.rollback:
            previous = read_json(ROOT / 'previous.json')
            if not previous:
                raise ValueError('No previous version')
            current = read_json(ROOT / 'current.json')
            probe(current, idle=True)
            compose(previous, ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120'])
            health(previous)
            probe(previous)
            atomic_json(ROOT / 'current.json', previous)
            atomic_json(ROOT / 'previous.json', current)
            if current:
                atomic_json(ROOT / 'failed.json', {'image_id': current.get('source_image_id', current['image_id']), 'tag': current['tag'], 'error': 'manual rollback'})
        elif args.local:
            install(args.local.resolve(), read_json(args.local / 'masa-release.json'))
        else:
            sync(args.force)


if __name__ == '__main__':
    main()
