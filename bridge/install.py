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


def controller_mode(app, info):
    resources = app / 'Contents/Resources'
    manifest_path = resources / 'build-info.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    mode = manifest.get('controller_mode', 'legacy')
    if mode not in ('legacy', 'audio-performance'):
        raise ValueError('Unsupported controller mode in app metadata')
    if info.get('LightpadBridgeControllerMode', 'legacy') != mode:
        raise ValueError('App controller mode metadata does not match its sealed launch defaults')
    if mode == 'audio-performance':
        entries = dict(main='controller.littlefoot', metronome='metronome.littlefoot',
                       patch='patch.littlefoot', status='status.littlefoot')
        if set(manifest.get('programs', {})) != set(entries):
            raise ValueError('Audio app requires its matched main, metronome, patch and status programs')
        for name, filename in entries.items():
            program = resources / filename
            metadata = manifest['programs'][name]
            if not program.is_file() or digest(program) != metadata.get('source_sha256'):
                raise ValueError('Audio program does not match bundle metadata: ' + name)
            for relative, expected in metadata.get('includes_sha256', {}).items():
                include = Path(relative)
                if include.is_absolute() or '..' in include.parts:
                    raise ValueError('Audio program include escapes bundle resources')
                source = resources / include
                if not source.is_file() or digest(source) != expected:
                    raise ValueError('Audio include does not match bundle metadata: ' + relative)
    return mode


def configuration_values(configuration):
    result = {}
    for node in ast.parse(configuration, filename='configuration.py').body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = value
    return result


def validate_host(script, configuration, mode):
    if 'verification/experiments/' in configuration:
        raise ValueError('Production configuration must not depend on private experiment paths')
    settings = configuration_values(configuration)
    if mode == 'legacy':
        if settings.get('LIVE_STATE_MODE') is not True or not (script / 'health.py').is_file():
            raise ValueError('Production installation requires Live-state mode and matching host health support')
        return
    modules = ('surface', 'backend', 'health', 'protocol', 'model', 'audio_inputs',
               'bundle_recording', 'recording_core', 'recording_coverage', 'launch_admission',
               'selection_restore', 'timing')
    if any(not (script / (name + '.py')).is_file() for name in modules):
        raise ValueError('Audio app requires the complete matched PerformanceController script')
    surface = ast.parse((script / 'surface.py').read_text())
    if not any(isinstance(node, ast.ClassDef) and node.name == 'PerformanceController' for node in surface.body):
        raise ValueError('Audio app requires the matched PerformanceController script')
    paths = settings.get('TARGET_SETS') or (settings.get('TARGET_SET'),)
    if (not isinstance(paths, tuple) or not paths or any(
            not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.lower() != '.als'
            for path in paths)):
        raise ValueError('Audio configuration requires explicit absolute Live Set paths')
    sources = settings.get('AUDIO_SOURCES')
    if (not isinstance(sources, tuple) or len(sources) != 2 or any(
            not isinstance(source, dict) or any(not isinstance(source.get(key), str) or not source[key]
                for key in ('name', 'helper_name')) for source in sources)):
        raise ValueError('Audio configuration requires two named source/helper definitions')
    if any(len({source[key] for source in sources}) != 2 for key in ('name', 'helper_name')):
        raise ValueError('Audio source and helper names must be unique')
    for key in ('TRACE_PATH', 'STATUS_PATH'):
        value = settings.get(key)
        if value is not None and (not isinstance(value, str) or not Path(value).is_absolute()):
            raise ValueError('Audio diagnostic paths must be absolute or None: ' + key)


def stage(app, script, output, app_target, script_target, support, login_target, login=True,
          configuration=None):
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
    mode = controller_mode(app, info)
    expected_targets = ('LightpadAudioInputs', 'PerformanceController') if mode == 'audio-performance' else ('LightpadController',)
    if app_target.suffix != '.app' or script_target.name not in expected_targets:
        raise ValueError('Installation targets must name the app and matching Live script folder: ' + ', '.join(expected_targets))
    configuration_path = Path(configuration).expanduser().absolute() if configuration is not None else script / 'configuration.py'
    if configuration_path.is_symlink():
        raise ValueError('Symbolic-link configuration files are not supported')
    configuration_text = configuration_path.read_text()
    validate_host(script, configuration_text, mode)
    hashes(app); hashes(script)  # reject symlinks before creating output
    output.mkdir(parents=True)
    copy(app, output / 'Lightpad Bridge.app')
    staged_script = output / script_target.name
    copy(script, staged_script)
    (staged_script / 'configuration.py').write_text(configuration_text)
    entries = []
    for name, source, target in (('app', output / 'Lightpad Bridge.app', app_target),
                                 ('live_script', staged_script, script_target)):
        entries.append(dict(name=name, source=str(source), target=str(target),
                            before=hashes(target), after=hashes(source)))
    if login:
        agent = output / (LABEL + '.plist')
        agent.write_bytes(plistlib.dumps(agent_plist(app_target, support)))
        entries.append(dict(name='login_agent', source=str(agent), target=str(login_target),
                            before=hashes(login_target), after=hashes(agent)))
    plan = dict(schema=1, controller_mode=mode, entries=entries, support=str(support), login_enabled=login,
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
    staging.add_argument('--configuration', type=Path, help='Private configuration copied into the staged Live script')
    staging.add_argument('--output', required=True, type=Path)
    home = Path.home(); support = home / 'Library/Application Support/Lightpad Bridge'
    staging.add_argument('--app-target', type=Path, default=home / 'Applications/Lightpad Bridge.app')
    staging.add_argument('--script-target', type=Path, help='Defaults to LightpadAudioInputs for Audio mode, otherwise LightpadController')
    staging.add_argument('--support', type=Path, default=support)
    staging.add_argument('--login-target', type=Path, default=home / 'Library/LaunchAgents' / (LABEL + '.plist'))
    staging.add_argument('--no-login', action='store_true')
    applying = sub.add_parser('apply'); applying.add_argument('plan', type=Path)
    applying.add_argument('--activate-login', action='store_true', help='Also load the login agent; starts the helper immediately')
    reverting = sub.add_parser('rollback'); reverting.add_argument('receipt', type=Path)
    args = parser.parse_args()
    if args.command == 'stage':
        if args.script_target is None:
            info = plistlib.loads((args.app / 'Contents/Info.plist').read_bytes())
            folder = 'LightpadAudioInputs' if controller_mode(args.app, info) == 'audio-performance' else 'LightpadController'
            args.script_target = home / 'Music/Ableton/User Library/Remote Scripts' / folder
        result = stage(args.app, args.script, args.output, args.app_target, args.script_target,
                       args.support, args.login_target, login=not args.no_login, configuration=args.configuration)
    elif args.command == 'apply': result = apply(args.plan, args.activate_login)
    else: result = rollback(args.receipt)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
