"""Build today's page with the default search and publish it to GitHub Pages.

    python scripts/publish.py            # what the hourly Windows task runs (via pythonw)

halooglasi answers 403 to cloud runners, so the page is built here, on a home connection, and pushed
as a single-commit `site` branch (index.html + .nojekyll) that GitHub Pages serves as is. The branch
is replaced on every run, so its history stays one commit long. Nothing in the working tree changes.
If no source answered (offline), nothing is published and the last good page stays up.
Log: out/publish.log.
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'out'
LOG = OUT / 'publish.log'
BRANCH = 'site'
SEARCH = ['--today', '--limit', '0', '--sort', 'price', '--max-price', '250000', '--max-price-m2', '4000',
          '--district', 'stari grad', '--district', 'savski venac', '--district', 'banovo brdo']
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)     # pythonw: do not flash console windows


def log(msg):
    OUT.mkdir(exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(f'{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n')


def run(*cmd, data=None):
    r = subprocess.run(cmd, cwd=ROOT, input=data, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError(f'{cmd[:3]} -> {r.returncode}: {r.stderr.decode("utf-8", "replace").strip()[-500:]}')
    return r.stdout.decode('utf-8').strip()


def git_blob(data):
    return run('git', 'hash-object', '-w', '--stdin', data=data)


def main():
    # pythonw has no console, so run the builder with python.exe next to it
    python = Path(sys.executable).with_name('python.exe')
    build = subprocess.run([str(python if python.exists() else sys.executable),
                            str(ROOT / 'scripts' / 'belgrade_flats.py'), *SEARCH],
                           cwd=ROOT, capture_output=True, creationflags=NO_WINDOW,
                           env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    errors = build.stderr.decode('utf-8', 'replace').strip()
    if build.returncode:
        log(f'build failed ({build.returncode}), page not published: {errors[-500:]}')
        return 1
    summary = build.stdout.decode('utf-8', 'replace').splitlines()[0]

    page = (OUT / 'belgrade_flats.html').read_bytes()
    tree = run('git', 'mktree', data=(
        f'100644 blob {git_blob(b"")}\t.nojekyll\n'
        f'100644 blob {git_blob(page)}\tindex.html\n').encode())
    commit = run('git', 'commit-tree', tree, '-m', f'Listings {datetime.now():%Y-%m-%d %H:%M}')
    run('git', 'push', '--force', '--quiet', 'origin', f'{commit}:refs/heads/{BRANCH}')
    log(f'published: {summary.split(" -> ")[0]}' + (f' | {errors}' if errors else ''))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:                     # the task has no console: the log is the only trace
        log(f'error: {e}')
        sys.exit(1)
