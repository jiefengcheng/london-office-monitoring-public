"""Portable install boundaries; no model or registry calls in these tests."""
from contextlib import redirect_stdout, redirect_stderr
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import agent
from agent_protocol import ProviderError
from scripts import install

ROOT = Path(__file__).resolve().parents[1]


class PythonCP6Tests(unittest.TestCase):
    def test_core_on_mac_and_linux_never_uses_xcode_or_package_download(self):
        for system, arch in [('Darwin', 'arm64'), ('Linux', 'x86_64'), ('Windows', 'AMD64')]:
            with self.subTest(system=system), patch.object(install.platform, 'system', return_value=system), \
                 patch.object(install.platform, 'machine', return_value=arch), \
                 patch.object(install.platform, 'mac_ver', side_effect=AssertionError('core read macOS')), \
                 patch.object(install, 'ensure_environment', return_value=Path('/repo/.venv/bin/python')), \
                 patch.object(install.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
                install.install()
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ['/repo/.venv/bin/python', '-m', 'pip', 'check'])

    def test_apple_on_unsupported_platform_holds_before_environment_changes(self):
        with patch.object(install.platform, 'system', return_value='Linux'), \
             patch.object(install, 'ensure_environment') as prepare, patch.object(install.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'requires native Apple silicon'):
                install.install('apple')
            prepare.assert_not_called(); run.assert_not_called()

    def test_apple_old_os_holds_before_environment_changes(self):
        with patch.object(install.platform, 'system', return_value='Darwin'), \
             patch.object(install.platform, 'machine', return_value='arm64'), \
             patch.object(install.platform, 'mac_ver', return_value=('26.3', (), '')), \
             patch.object(install, 'ensure_environment') as prepare:
            with self.assertRaisesRegex(RuntimeError, '26.4'):
                install.install('apple')
            prepare.assert_not_called()

    def test_apple_missing_tools_holds_before_environment_changes(self):
        with patch.object(install.platform, 'system', return_value='Darwin'), \
             patch.object(install.platform, 'machine', return_value='arm64'), \
             patch.object(install.platform, 'mac_ver', return_value=('27.2', (), '')), \
             patch.object(install, 'ensure_environment') as prepare, \
             patch.object(install.subprocess, 'run', side_effect=FileNotFoundError('xcrun')):
            with self.assertRaises(FileNotFoundError): install.install('apple')
            prepare.assert_not_called()

    def test_apple_profile_uses_separate_hash_locked_build_and_sdk_inputs(self):
        with patch.object(install.platform, 'system', return_value='Darwin'), \
             patch.object(install.platform, 'machine', return_value='arm64'), \
             patch.object(install.platform, 'mac_ver', return_value=('27.2', (), '')), \
             patch.object(install, 'ensure_environment', return_value=Path('/repo/.venv/bin/python')), \
             patch.object(install.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
            install.install('apple')
            calls = [x.args[0] for x in run.call_args_list]
            self.assertEqual(calls[0], ['xcrun', '--show-sdk-version'])
            self.assertIn('--require-hashes', calls[1]); self.assertTrue(calls[1][-1].endswith('requirements-apple-build.lock'))
            self.assertIn('--no-build-isolation', calls[2]); self.assertIn('--no-deps', calls[2])
            self.assertIn('--require-hashes', calls[2]); self.assertTrue(calls[2][-1].endswith('requirements-apple.lock'))
            self.assertEqual(calls[3][-3:], ['-m', 'pip', 'check'])

    def test_dependency_check_failure_is_not_installation_success(self):
        with patch.object(install, 'ensure_environment', return_value=Path('/repo/.venv/bin/python')), \
             patch.object(install.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['pip', 'check'])), \
             redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(subprocess.CalledProcessError): install.install('core')
            self.assertNotIn('PASS | installation', output.getvalue())

    def test_unknown_profile_is_rejected_before_environment_changes(self):
        with patch.object(install, 'ensure_environment') as prepare:
            with self.assertRaisesRegex(RuntimeError, 'Unknown installation profile'): install.install('typo')
            prepare.assert_not_called()

    def test_api_default_configuration_failure_cannot_fall_back_to_apple(self):
        with patch.object(agent, 'load_environment', return_value={}), \
             patch.object(agent, 'AppleFoundationModelsProvider') as apple, \
             patch.object(agent, 'OpenAIResponsesProvider', side_effect=ProviderError('MODEL_NAME_REQUIRED')) as api, \
             redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(agent.main(['--message', 'Show F01']), 2)
            api.assert_called_once(); apple.assert_not_called()
            self.assertIn('MODEL_NAME_REQUIRED', errors.getvalue())

    def test_api_runtime_does_not_import_optional_sdk(self):
        code = '''import importlib.abc, sys
class NoAppleSDK(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, *args):
  if fullname.startswith('apple_fm_sdk'): raise AssertionError('Optional SDK imported by API route')
sys.meta_path.insert(0, NoAppleSDK())
import agent
agent.load_environment=lambda: {}
assert agent.main(['--provider','openai','--message','Show F01'])==2
assert 'apple_fm_sdk' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MODEL_NAME_REQUIRED', result.stderr)

    def test_core_lock_declares_no_runtime_packages(self):
        requirements = [line.strip() for line in (ROOT/'requirements.lock').read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(requirements, [])


if __name__ == '__main__': unittest.main()
