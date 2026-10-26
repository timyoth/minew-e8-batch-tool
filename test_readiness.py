import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import batch_configure as batch
import configure_tag
from profiles import validate_profile

class ReadinessTests(unittest.TestCase):
    def test_missing_or_invalid_key_fails_locally(self):
        with tempfile.TemporaryDirectory() as td, patch.object(configure_tag, 'ROOT', Path(td)):
            with self.assertRaisesRegex(ValueError, 'Missing sdk_bootstrap'):
                configure_tag.load_bootstrap_key()
            (Path(td)/'sdk_bootstrap_key.bin').write_bytes(b'not-a-key')
            with self.assertRaisesRegex(ValueError, 'exactly 16'):
                configure_tag.load_bootstrap_key()
            (Path(td)/'sdk_bootstrap_key.bin').write_bytes(bytes(16))
            self.assertEqual(len(configure_tag.load_bootstrap_key()), 16)

    def test_output_collisions_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            args=SimpleNamespace(command='apply', inventory=root/'tags.csv', profile=root/'profile.json', state=root/'tags.json')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                batch.validate_paths(args)
            args.state=root/'profile.json'
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                batch.validate_paths(args)
            args.state=root/'ledger.csv'
            with self.assertRaisesRegex(ValueError, 'end in .json'):
                batch.validate_paths(args)
            args.state=root/'ledger.json'
            batch.validate_paths(args)

    def test_plan_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            args=SimpleNamespace(command='plan', inventory=root/'tags.csv', profile=root/'profile.json', output=root/'tags.csv')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                batch.validate_paths(args)
            args.output=root/'draft'
            args.profile=root/'draft.tmp'
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                batch.validate_paths(args)
            args.profile=root/'profile.json'
            args.output=root/'plan.json'
            args.output.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                batch.validate_paths(args)
            args.output=root/'fresh.json'
            batch.validate_paths(args)

    def test_null_settings_rejected_in_validation(self):
        for field in ('interval_ms', 'tx_dbm'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'null'):
                validate_profile({'version':1,'slots':[{'slot':1,field:None}]})
