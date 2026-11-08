"""Committed configuration cannot enable a local scratch/proof installation."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from LightpadController import configuration as config

class PortableConfigurationTests(unittest.TestCase):

    def test_repository_defaults_require_explicit_local_recording_and_proof_opt_in(self):
        for name in ('AUDIO_CONTROLLER_MANIFEST', 'SYNC_UI_EVIDENCE', 'PROOF_DIRECTORY', 'CONTROLLER_PROBE_DIRECTORY'):
            self.assertIsNone(getattr(config, name), name)
        for name in ('NATIVE_BAR_CLOCK', 'AUDIO_PROOF_MODE', 'AUDIO_CONTROLLER_PROBE_MODE'):
            self.assertIs(getattr(config, name), False, name)
        self.assertEqual(config.VALIDATED_AUDIO_FEATURES, ())
        self.assertEqual(config.PARAMETER_TARGETS, {})
        self.assertEqual(config.GROUP_NAMES, tuple(('Lightpad %d' % (i + 1) for i in range(8))))
if __name__ == '__main__':
    unittest.main()
