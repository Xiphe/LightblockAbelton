#!/usr/bin/env python3
"""Stage a matched app/Live script and optional login agent. Staging is read-only
outside OUTPUT. Apply/rollback are explicit, require Live and the helper closed,
and retain every replaced file. They never open MIDI, flash a startup program,
change Live preferences or save recordings.
"""
import argparse
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import uuid

LABEL = 'net.xiphe.lightpad-bridge'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError('Symbolic-link installation roots are not supported: ' + str(path))
    if path.is_file():
        return {'': digest(path)}
    result = {}
    for file in sorted(path.rglob('*')):
        if file.is_symlink():
            raise ValueError('Symbolic links are not supported in a staged installation')
        if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
            result[str(file.relative_to(path))] = digest(file)
    return result


def copy(source, target):
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    else:
        shutil.copy2(source, target)


def agent_plist(app_target, support):
    return dict(Label=LABEL, ProgramArguments=[str(app_target / 'Contents/MacOS/lightpad-bridge')],
                RunAtLoad=True, KeepAlive={'SuccessfulExit': False}, ThrottleInterval=5,
                LimitLoadToSessionType='Aqua', ProcessType='Interactive',
                StandardOutPath=str(support / 'bridge.stdout.log'),
                StandardErrorPath=str(support / 'bridge.stderr.log'))


def stage(app, script, output, app_target, script_target, support, login_target, login=True):
    app, script, output, app_target, script_target, support, login_target = (
        Path(p).expanduser().absolute() for p in (app, script, output, app_target, script_target, support, login_target))
    if output.exists():
        raise ValueError('Staging output must be a new directory')
    if not app.is_dir() or not script.is_dir() or not (script / '__init__.py').is_file():
        raise ValueError('A packaged app and complete Live script are required')
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != LABEL or info.get('CFBundleExecutable') != 'lightpad-bridge':
        raise ValueError('App must have the native Lightpad Bridge bundle identity')
    if not (app / 'Contents/Resources/status.littlefoot').is_file():
        raise ValueError('Production app requires its separate status program')
    if app_target.suffix != '.app' or script_target.name != 'LightpadController':
        raise ValueError('Installation targets must name the app and LightpadController folder')
    # Refuse the current prototype's private runtime paths in a production stage.
    configuration = (script / 'configuration.py').read_text()
    if 'verification/experiments/' in configuration:
        raise ValueError('Production configuration must not depend on private experiment paths')
    settings = ast.parse(configuration, filename='configuration.py')
    live_mode = None
    for node in settings.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'LIVE_STATE_MODE' for t in node.targets):
            live_mode = node.value.value if isinstance(node.value, ast.Constant) else None
    if live_mode is not True or not (script / 'health.py').is_file():
        raise ValueError('Production installation requires Live-state mode and matching host health support')
    hashes(app); hashes(script)  # reject symlinks before creating output
    output.mkdir(parents=True)
    copy(app, output / 'Lightpad Bridge.app')
    copy(script, output / 'LightpadController')
    entries = []
    for name, source, target in (('app', output / 'Lightpad Bridge.app', app_target),
                                 ('live_script', output / 'LightpadController', script_target)):
        entries.append(dict(name=name, source=str(source), target=str(target),
                            before=hashes(target), after=hashes(source)))
    if login:
        agent = output / (LABEL + '.plist')
        agent.write_bytes(plistlib.dumps(agent_plist(app_target, support)))
        entries.append(dict(name='login_agent', source=str(agent), target=str(login_target),
                            before=hashes(login_target), after=hashes(agent)))
    plan = dict(schema=1, entries=entries, support=str(support), login_enabled=login,
                launch_label=LABEL, source_stage=str(output), saved_default_writes=False,
                firmware_writes=False, live_preference_writes=False, audio_saves=False)
    (output / 'install-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    return plan


def active_executables(output):
    """Classify macOS ps comm output, which contains executable paths, not argv."""
    return [path for line in output.splitlines() if (path := line.strip()) and
            (path.endswith('/Contents/MacOS/Live') or Path(path).name == 'lightpad-bridge')]


def ensure_stopped():
    # command= includes shell/Python arguments and can match this installer's caller.
    result = subprocess.run(['ps', '-axo', 'comm='], check=True, capture_output=True, text=True)
    active = active_executables(result.stdout)
    if active:
        raise RuntimeError('Close Ableton Live and Lightpad Bridge before applying or rolling back this installation: '
                           + ', '.join(active))


def apply(plan_path, activate_login=False, check_processes=ensure_stopped):
    plan_path = Path(plan_path).absolute()
    plan = json.loads(plan_path.read_text())
    if plan.get('schema') != 1:
        raise ValueError('Unsupported installation plan')
    check_processes()
    for entry in plan['entries']:
        if hashes(Path(entry['source'])) != entry['after']:
            raise ValueError('Staged files changed: ' + entry['name'])
        if hashes(Path(entry['target'])) != entry['before']:
            raise ValueError('Installed files changed since staging: ' + entry['name'])
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    revision = Path(plan['support']) / 'revisions' / stamp
    revision.mkdir(parents=True)
    prepared = []
    committed = []
    try:
        # Prepare and hash-check every copy before moving any installed root.
        for entry in plan['entries']:
            target = Path(entry['target']); target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('.' + target.name + '.install-' + stamp)
            prepared.append((entry, temporary))
            copy(Path(entry['source']), temporary)
            if hashes(temporary) != entry['after']:
                raise ValueError('Copied files failed verification: ' + entry['name'])
        for entry, temporary in prepared:
            target = Path(entry['target']); previous = revision / entry['name']
            if target.exists():
                target.rename(previous)
            try:
                temporary.rename(target)
            except BaseException:
                if previous.exists(): previous.rename(target)
                raise
            committed.append(entry)
        receipt = dict(plan, revision=str(revision), installed_at=stamp, activated_login=False)
        (revision / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    except BaseException:
        for entry in reversed(committed):
            target = Path(entry['target']); previous = revision / entry['name']
            if target.exists(): target.rename(revision / ('failed-new-' + entry['name']))
            if previous.exists(): previous.rename(target)
        raise
    finally:
        for entry, temporary in prepared:
            if temporary.exists():
                if temporary.is_dir(): shutil.rmtree(temporary)
                else: temporary.unlink()

    if activate_login and plan['login_enabled']:
        # Activation is separate from file commit. A launch failure never rolls
        # installed files backward underneath a helper that may have started.
        domain = 'gui/' + str(os.getuid())
        subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
        login = next(e for e in plan['entries'] if e['name'] == 'login_agent')
        result = subprocess.run(['launchctl', 'bootstrap', domain, login['target']], capture_output=True, text=True)
        receipt['activated_login'] = result.returncode == 0
        if result.returncode:
            receipt['activation_error'] = result.stderr.strip() or 'Login agent could not be loaded'
        (revision / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def rollback(receipt_path, check_processes=ensure_stopped):
    receipt_path = Path(receipt_path).absolute()
    receipt = json.loads(receipt_path.read_text()); check_processes()
    revision = receipt_path.parent
    for entry in receipt['entries']:
        if hashes(Path(entry['target'])) != entry['after']:
            raise ValueError('Installed files changed; refusing automatic rollback: ' + entry['name'])
        if hashes(revision / entry['name']) != entry['before']:
            raise ValueError('Rollback snapshot changed: ' + entry['name'])
    stamp = uuid.uuid4().hex[:8]
    replacement = revision / ('rolled-back-' + stamp); replacement.mkdir()
    prepared, committed = [], []
    try:
        # Copy/verify all old roots before moving any currently installed root.
        for entry in receipt['entries']:
            target = Path(entry['target']); previous = revision / entry['name']
            temporary = target.with_name('.' + target.name + '.rollback-' + stamp)
            prepared.append((entry, temporary))
            if previous.exists():
                copy(previous, temporary)
                if hashes(temporary) != entry['before']:
                    raise ValueError('Prepared rollback failed verification: ' + entry['name'])
        if receipt.get('activated_login'):
            subprocess.run(['launchctl', 'bootout', 'gui/' + str(os.getuid()) + '/' + LABEL], capture_output=True)
        for entry, temporary in prepared:
            target = Path(entry['target']); current = replacement / entry['name']
            target.rename(current)
            try:
                if temporary.exists(): temporary.rename(target)
            except BaseException:
                current.rename(target)
                raise
            committed.append(entry)
    except BaseException:
        # On a commit failure restore the complete newer generation. Both old
        # snapshots and any prepared/restored old roots remain recoverable.
        for entry in reversed(committed):
            target = Path(entry['target']); current = replacement / entry['name']
            if target.exists(): target.rename(replacement / ('failed-restore-' + entry['name']))
            current.rename(target)
        raise
    finally:
        for entry, temporary in prepared:
            if temporary.exists():
                if temporary.is_dir(): shutil.rmtree(temporary)
                else: temporary.unlink()
    return dict(restored=True, preserved_new_installation=str(replacement),
                login_agent_requires_reload=receipt.get('activated_login', False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    staging = sub.add_parser('stage')
    staging.add_argument('--app', required=True, type=Path)
    staging.add_argument('--script', required=True, type=Path)
    staging.add_argument('--output', required=True, type=Path)
    home = Path.home(); support = home / 'Library/Application Support/Lightpad Bridge'
    staging.add_argument('--app-target', type=Path, default=home / 'Applications/Lightpad Bridge.app')
    staging.add_argument('--script-target', type=Path, default=home / 'Music/Ableton/User Library/Remote Scripts/LightpadController')
    staging.add_argument('--support', type=Path, default=support)
    staging.add_argument('--login-target', type=Path, default=home / 'Library/LaunchAgents' / (LABEL + '.plist'))
    staging.add_argument('--no-login', action='store_true')
    applying = sub.add_parser('apply'); applying.add_argument('plan', type=Path)
    applying.add_argument('--activate-login', action='store_true', help='Also load the login agent; starts the helper immediately')
    reverting = sub.add_parser('rollback'); reverting.add_argument('receipt', type=Path)
    args = parser.parse_args()
    if args.command == 'stage':
        result = stage(args.app, args.script, args.output, args.app_target, args.script_target,
                       args.support, args.login_target, login=not args.no_login)
    elif args.command == 'apply': result = apply(args.plan, args.activate_login)
    else: result = rollback(args.receipt)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
