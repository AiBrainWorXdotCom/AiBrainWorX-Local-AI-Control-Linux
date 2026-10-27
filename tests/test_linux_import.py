import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('runtime_installer', Path(__file__).resolve().parents[1]/'install-linux-runtimes.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ConfigurationImport(unittest.TestCase):
    def test_keeps_upstream_low_ram_flag_and_translates_only_paths(self):
        original = ['--pack', 'D:\\Models\\pack', '--resident-experts', '--max-context', '16384']
        result = installer.map_engine_args(original)
        self.assertEqual(result, ['--pack', '/mnt/d/Models/pack', '--resident-experts', '--max-context', '16384'])
        self.assertEqual(original[1], 'D:\\Models\\pack')

    def test_preserves_explicit_mmap_and_other_memory_choices(self):
        original = ['--mmap-experts', '--ple-io', 'mmap', '--kv', 'int8']
        self.assertEqual(installer.map_engine_args(original), original)
        self.assertIsNot(installer.map_engine_args(original), original)

    def test_rejects_non_string_arguments(self):
        with self.assertRaises(ValueError):
            installer.map_engine_args(['--max-context', 16384])
