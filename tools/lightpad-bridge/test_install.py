"""Transaction tests use private temporary folders and never launch programs."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('lightpad_install',Path(__file__).with_name('install.py'))
install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lightpad-install-test-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.app=self.root/'new.app';self.script=self.root/'new-script'
        (self.app/'Contents/Resources').mkdir(parents=True)
        (self.app/'Contents/MacOS').mkdir()
        (self.app/'Contents/MacOS/lightpad-bridge').write_bytes(b'offline-test-binary')
        (self.app/'Contents/Resources/status.littlefoot').write_text('offline-status')
        (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier=install.LABEL,CFBundleExecutable='lightpad-bridge')))
        self.script.mkdir();(self.script/'__init__.py').write_text('new script')
        (self.script/'configuration.py').write_text('LIVE_STATE_MODE = True\nAUDIO_CONTROLLER_MANIFEST = None\n')
        (self.script/'health.py').write_text('offline health contract')
        self.app_target=self.root/'Apps/Lightpad Bridge.app';self.script_target=self.root/'Scripts/LightpadController'
        self.support=self.root/'Support';self.login=self.root/'Login/net.xiphe.lightpad-bridge.plist';self.stage=self.root/'staged'
        self.app_target.mkdir(parents=True);(self.app_target/'old.txt').write_text('old app')
        self.script_target.mkdir(parents=True);(self.script_target/'configuration.py').write_text('old local configuration')
        self.login.parent.mkdir();self.login.write_bytes(b'old login')

    def prepare(self,login=True):
        return install.stage(self.app,self.script,self.stage,self.app_target,self.script_target,self.support,self.login,login)

    def test_staging_never_changes_installed_files_or_creates_login_agent(self):
        before=[install.hashes(p) for p in (self.app_target,self.script_target,self.login)]
        plan=self.prepare()
        self.assertEqual(before,[install.hashes(p) for p in (self.app_target,self.script_target,self.login)])
        self.assertFalse(self.support.exists())
        agent=plistlib.loads((self.stage/(install.LABEL+'.plist')).read_bytes())
        self.assertEqual(agent['ProgramArguments'],[str(self.app_target/'Contents/MacOS/lightpad-bridge')])
        self.assertTrue(agent['RunAtLoad']);self.assertEqual(agent['KeepAlive'],{'SuccessfulExit':False})
        self.assertFalse(plan['saved_default_writes']);self.assertFalse(plan['audio_saves'])

    def test_process_guard_matches_executables_and_never_shell_arguments(self):
        live = '/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live'
        bridge = '/Users/test/Applications/Lightpad Bridge.app/Contents/MacOS/lightpad-bridge'
        legacy = '/Applications/Lightpad Bridge.app/Contents/Resources/lightpad-bridge'
        cli = '/tmp/build/lightpad-bridge'
        output = '\n'.join(['/bin/zsh', '/usr/bin/python3', '/usr/bin/rg', live, bridge, legacy, cli])
        self.assertEqual(install.active_executables(output), [live, bridge, legacy, cli])
        # Only executable identities are requested, so argv mentioning either app is absent.
        with patch.object(install.subprocess, 'run', return_value=SimpleNamespace(
                stdout='/bin/zsh\n/usr/bin/python3\n/usr/bin/rg\n')) as run:
            install.ensure_stopped()
            run.assert_called_once_with(['ps', '-axo', 'comm='], check=True, capture_output=True, text=True)
        with patch.object(install.subprocess, 'run', return_value=SimpleNamespace(stdout=live+'\n')):
            with self.assertRaisesRegex(RuntimeError, 'Ableton Live 12'):
                install.ensure_stopped()

    def test_apply_and_rollback_restore_matched_app_script_and_configuration(self):
        before=[install.hashes(p) for p in (self.app_target,self.script_target,self.login)]
        self.prepare()
        with patch.object(install.subprocess,'run',side_effect=AssertionError('must not launch')):
            receipt=install.apply(self.stage/'install-plan.json',check_processes=lambda:None)
        self.assertFalse(receipt['activated_login'])
        self.assertEqual((self.script_target/'configuration.py').read_text(),'LIVE_STATE_MODE = True\nAUDIO_CONTROLLER_MANIFEST = None\n')
        result=install.rollback(Path(receipt['revision'])/'receipt.json',check_processes=lambda:None)
        self.assertTrue(result['restored'])
        self.assertEqual(before,[install.hashes(p) for p in (self.app_target,self.script_target,self.login)])
        self.assertTrue((Path(result['preserved_new_installation'])/'app/Contents/MacOS/lightpad-bridge').exists())

    def test_changed_installed_or_staged_files_refuse_apply(self):
        self.prepare();(self.script_target/'extra.txt').write_text('manual change')
        with self.assertRaisesRegex(ValueError,'Installed files changed'):
            install.apply(self.stage/'install-plan.json',check_processes=lambda:None)
        self.assertFalse(self.support.exists())
        (self.script_target/'extra.txt').unlink();(self.stage/'LightpadController/__init__.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'Staged files changed'):
            install.apply(self.stage/'install-plan.json',check_processes=lambda:None)

    def test_copy_failure_leaves_all_original_roots_in_place(self):
        before=[install.hashes(p) for p in (self.app_target,self.script_target,self.login)];self.prepare()
        original=install.copy
        def fail_second(source,target):
            if source.name=='LightpadController':
                target.mkdir();(target/'partial').write_text('partial copy')
                raise OSError('disk test failure')
            original(source,target)
        with patch.object(install,'copy',fail_second),self.assertRaises(OSError):
            install.apply(self.stage/'install-plan.json',check_processes=lambda:None)
        self.assertEqual(before,[install.hashes(p) for p in (self.app_target,self.script_target,self.login)])
        self.assertFalse(list(self.app_target.parent.glob('.*install-*')))

    def test_live_or_bridge_running_blocks_before_mutation(self):
        self.prepare()
        def running():raise RuntimeError('Close Ableton Live and Lightpad Bridge')
        with self.assertRaises(RuntimeError):install.apply(self.stage/'install-plan.json',check_processes=running)
        self.assertFalse(self.support.exists())

    def test_private_experiment_dependency_is_rejected(self):
        (self.script/'configuration.py').write_text("AUDIO_CONTROLLER_MANIFEST = '/some/verification/experiments/fixture.json'\n")
        with self.assertRaisesRegex(ValueError,'private experiment'):self.prepare()
        self.assertFalse(self.stage.exists())

    def test_legacy_host_cannot_be_staged_as_a_production_installation(self):
        (self.script/'configuration.py').write_text('LIVE_STATE_MODE = False\n')
        with self.assertRaisesRegex(ValueError,'Live-state mode'):self.prepare()
        self.assertFalse(self.stage.exists())

    def test_no_login_plan_has_no_login_entry(self):
        plan=self.prepare(login=False)
        self.assertEqual([e['name'] for e in plan['entries']],['app','live_script'])

    def test_rollback_copy_failure_leaves_complete_current_generation(self):
        self.prepare();receipt=install.apply(self.stage/'install-plan.json',check_processes=lambda:None)
        after=[install.hashes(p) for p in (self.app_target,self.script_target,self.login)]
        original=install.copy
        def fail_second(source,target):
            if source.name=='live_script':
                target.mkdir();(target/'partial').write_text('partial old script')
                raise OSError('rollback disk failure')
            original(source,target)
        with patch.object(install,'copy',fail_second),self.assertRaisesRegex(OSError,'rollback disk'):
            install.rollback(Path(receipt['revision'])/'receipt.json',check_processes=lambda:None)
        self.assertEqual(after,[install.hashes(p) for p in (self.app_target,self.script_target,self.login)])
        self.assertFalse(list(self.app_target.parent.glob('.*rollback-*')))
        self.assertFalse(list(self.script_target.parent.glob('.*rollback-*')))

    def test_rollback_rename_failure_restores_complete_current_generation(self):
        self.prepare();receipt=install.apply(self.stage/'install-plan.json',check_processes=lambda:None)
        after=[install.hashes(p) for p in (self.app_target,self.script_target,self.login)]
        original=Path.rename
        def fail_second(source,target):
            if '.rollback-' in source.name and Path(target)==self.script_target:
                raise OSError('rollback rename failure')
            return original(source,target)
        with patch.object(Path,'rename',fail_second),self.assertRaisesRegex(OSError,'rollback rename'):
            install.rollback(Path(receipt['revision'])/'receipt.json',check_processes=lambda:None)
        self.assertEqual(after,[install.hashes(p) for p in (self.app_target,self.script_target,self.login)])
        # The old snapshots were copied, never consumed during rollback.
        revision=Path(receipt['revision'])
        for entry in receipt['entries']:
            self.assertEqual(install.hashes(revision/entry['name']),entry['before'])

    def test_rollback_refuses_overwriting_later_user_changes(self):
        self.prepare();receipt=install.apply(self.stage/'install-plan.json',check_processes=lambda:None)
        (self.script_target/'user-edited.py').write_text('keep me')
        with self.assertRaisesRegex(ValueError,'Installed files changed'):
            install.rollback(Path(receipt['revision'])/'receipt.json',check_processes=lambda:None)
        self.assertTrue((self.script_target/'user-edited.py').exists())


if __name__=='__main__':unittest.main()
