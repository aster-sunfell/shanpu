"""Current-machine evidence is required on every run; no reusable readiness marker."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import actions


class StartupTest(unittest.TestCase):
    def test_residual_business_data_blocks_start_without_destroying_environment(self):
        for table in actions.BUSINESS_TABLES:
            counts='\n'.join(f'{name}\t{int(name==table)}' for name in actions.BUSINESS_TABLES)
            with patch.object(actions,'verify',return_value={'instanceId':'current'}), \
                 patch.object(actions,'initialize',return_value={'SUT_INSTANCE_ID':'current'}), \
                 patch.object(actions,'compose',side_effect=lambda *a:list(a)), \
                 patch.object(actions,'command',return_value=counts),patch.object(actions,'cleanup') as cleanup:
                with self.assertRaisesRegex(RuntimeError,'business rows'):actions.verify_clean_start()
                cleanup.assert_not_called()

    def test_clean_start_checks_redis_journal_and_instance_without_restart(self):
        counts='\n'.join(f'{name}\t0' for name in actions.BUSINESS_TABLES)
        with patch.object(actions,'verify',return_value={'instanceId':'current'}), \
             patch.object(actions,'initialize',return_value={'SUT_INSTANCE_ID':'current'}), \
             patch.object(actions,'compose',side_effect=lambda *a:list(a)), \
             patch.object(actions,'cleanup') as cleanup,patch.object(actions,'start_services') as start:
            with patch.object(actions,'command',side_effect=[counts,'','0']):
                self.assertEqual('current',actions.verify_clean_start()['instanceId'])
            with patch.object(actions,'command',side_effect=[counts,'','1']):
                with self.assertRaisesRegex(RuntimeError,'Redis'):actions.verify_clean_start()
            with patch.object(actions,'command',side_effect=[counts,RuntimeError('journal not empty')]):
                with self.assertRaisesRegex(RuntimeError,'journal'):actions.verify_clean_start()
            with patch.object(actions,'verify',return_value={'instanceId':'other'}):
                with self.assertRaisesRegex(RuntimeError,'identity'):actions.verify_clean_start()
            cleanup.assert_not_called();start.assert_not_called()

    def test_previous_machine_or_source_check_never_authorizes_current_run(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(actions,'RUNTIME',Path(temp)), \
             patch.object(actions,'REPORTS',Path(temp)),patch.object(actions,'source_metadata',return_value={'sourceSha256':'source'}), \
             patch.object(actions,'machine_id',return_value='machine'),patch.object(actions,'actions_context',return_value={'runId':'123'}):
            root=Path(temp);(root/'generator-check').mkdir()
            (root/'cpu-affinity.json').write_text(json.dumps({'runner':'2'}))
            good={'status':'passed','mode':'machine-check','experiment':'redis-filtering','sourceSha256':'source',
                  'machineId':'machine','actions':{'runId':'123'},'cpuset':'2'}
            with self.assertRaisesRegex(RuntimeError,'verification'):actions.require_generator_check('redis-filtering')
            for change in ({'machineId':'old'},{'sourceSha256':'old'},{'actions':{'runId':'old'}},{'cpuset':'3'},
                           {'experiment':'kafka-burst'},{'status':'failed'},{'mode':'regression'}):
                (root/'generator-check/check.json').write_text(json.dumps({**good,**change}))
                with self.subTest(change=change),self.assertRaises(RuntimeError):actions.require_generator_check('redis-filtering')
            (root/'generator-check/check.json').write_text(json.dumps(good))
            actions.require_generator_check('redis-filtering')

    def test_cache_exports_per_service_and_reuses_previous_index(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(actions,'RUNTIME',Path(temp)), \
             patch.dict(os.environ,{'BENCH_BUILD_CACHE':temp}),patch.object(actions,'compose',side_effect=lambda *a:list(a)):
            root=Path(temp);(root/'backend').mkdir();(root/'backend/index.json').write_text('{}')
            def command(args,**kwargs):
                config=json.loads(Path(args[args.index('--file')+1]).read_text())['services']
                self.assertIn('cache_from',config['backend']['build'])
                self.assertNotIn('cache_from',config['frontend']['build'])
                for name in config:
                    fresh=root/(name+'-new');fresh.mkdir();(fresh/'index.json').write_text('{"new":true}')
            with patch.object(actions,'command',side_effect=command):actions.build_images('sut',('backend','frontend'))
            self.assertEqual({'new':True},json.loads((root/'backend/index.json').read_text()))
            self.assertTrue((root/'frontend/index.json').exists())

    def test_failed_cache_import_falls_back_to_real_build(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(actions,'RUNTIME',Path(temp)), \
             patch.dict(os.environ,{'BENCH_BUILD_CACHE':temp}),patch.object(actions,'compose',side_effect=lambda *a:list(a)), \
             patch.object(actions,'command',side_effect=[subprocess.CalledProcessError(1,['build']),None]) as command,patch('builtins.print'):
            actions.build_images('sut',('backend',))
            self.assertEqual(['sut','build','backend'],command.call_args.args[0])
            self.assertEqual(2,command.call_count)

    def test_formal_run_stops_before_container_stage_if_startup_gate_fails(self):
        for failing in ('generator','initial-data'):
            with tempfile.TemporaryDirectory() as temp,patch.object(actions,'RUNTIME',Path(temp)), \
                 patch.object(actions,'REPORTS',Path(temp)),patch.object(actions,'run_stage') as stage:
                root=Path(temp)
                (root/'suite.json').write_text('{"status":"running"}')
                (root/'deadline.json').write_text(json.dumps({'monotonic':time.monotonic()+1000}))
                with patch.object(actions,'require_generator_check',side_effect=RuntimeError('generator') if failing=='generator' else None), \
                     patch.object(actions,'verify_clean_start',side_effect=RuntimeError('initial-data') if failing=='initial-data' else None):
                    with self.assertRaisesRegex(RuntimeError,failing):actions.run_experiment('redis-filtering',10)
                stage.assert_not_called()
                self.assertEqual('failed',json.loads((root/'suite.json').read_text())['status'])

if __name__=='__main__':unittest.main()
