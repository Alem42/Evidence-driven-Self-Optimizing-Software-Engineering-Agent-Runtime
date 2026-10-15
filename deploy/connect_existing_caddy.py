#!/usr/bin/env python3
"""Add Verdict alongside the existing policy app on this server; reload Caddy in place."""
import datetime
import json
from pathlib import Path
import shutil
import subprocess


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    site = Path('/opt/personal-website')
    caddy = site / 'Caddyfile'
    compose = site / 'compose.yaml'
    container = 'alem-site-web-1'
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    old_caddy, old_compose = caddy.read_text(), compose.read_text()
    for path in (caddy, compose):
        shutil.copy2(path, str(path) + '.before-masa-' + stamp)
    candidate = old_caddy
    if 'handle_path /apps/verdict/*' not in candidate:
        anchor = '    redir /apps/policy /apps/policy/ 308'
        if candidate.count(anchor) != 1:
            raise ValueError('existing Caddy routes changed; inspect before editing')
        routes = ('    redir /apps/verdict /apps/verdict/ 308\n'
                  '    handle_path /apps/verdict/* {\n'
                  '        reverse_proxy masa:8765\n'
                  '    }\n')
        candidate = candidate.replace(anchor, routes + anchor)
    candidate_compose = old_compose
    if 'name: masa-edge' not in candidate_compose:
        anchor = '  web:\n    image: caddy:2-alpine\n'
        if candidate_compose.count(anchor) != 1 or '\nnetworks:' in candidate_compose:
            raise ValueError('existing Compose networks changed; inspect before editing')
        candidate_compose = candidate_compose.replace(anchor, anchor + '    networks: [default, masa-edge]\n')
        candidate_compose += '\nnetworks:\n  masa-edge:\n    name: masa-edge\n    external: true\n'
    networks = json.loads(run(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', container],
                             capture_output=True).stdout)
    if 'masa-edge' not in networks:
        run(['docker', 'network', 'connect', 'masa-edge', container])
    try:
        # Write in place: Caddy's bind mount continues pointing at this inode.
        caddy.write_text(candidate)
        compose.write_text(candidate_compose)
        run(['docker', 'compose', '-f', str(compose), 'config', '--quiet'], cwd=site)
        run(['docker', 'exec', container, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
        run(['docker', 'exec', container, 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
        for url in ('http://43.134.116.107/', 'http://43.134.116.107/apps/policy/', 'http://43.134.116.107/apps/verdict/'):
            response = run(['curl', '--fail', '--silent', '--show-error', '--max-time', '15', '-o', '/dev/null',
                            '-w', '%{http_code}', url], capture_output=True)
            if response.stdout != '200':
                raise RuntimeError('unexpected response for ' + url + ': ' + response.stdout)
            print('PASS ' + url + ': ' + response.stdout)
    except Exception:
        caddy.write_text(old_caddy)
        compose.write_text(old_compose)
        run(['docker', 'exec', container, 'caddy', 'reload', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
        raise


if __name__ == '__main__':
    main()
