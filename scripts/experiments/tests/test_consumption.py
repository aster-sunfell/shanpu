import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from runner import require_idle


class ConsumptionEvidenceTest(unittest.TestCase):
    def check(self, policy):
        metrics={'enabled':True,'settings':{'sutId':'sut','database':'db','orderConsumption':policy}}
        with patch.dict(os.environ,{'SUT_INSTANCE_ID':'sut','MYSQL_DATABASE':'db'}), \
                patch('runner.observation.fresh_metrics',return_value=metrics), \
                patch('runner.observation.clock_evidence',return_value={}), \
                patch('runner.lag',return_value={}),patch('runner.sql',return_value='0'):
            return require_idle(None)

    def test_requires_real_supported_business_policy_before_data_mutation(self):
        policy={'maxTransactionsPerSecond':400,'mode':'smooth-no-catch-up','scope':'backend-instance'}
        self.assertEqual(policy,self.check(policy)['settings']['orderConsumption'])
        for invalid in ({},{**policy,'maxTransactionsPerSecond':0},
                        {**policy,'maxTransactionsPerSecond':True},
                        {**policy,'mode':'token-bucket'},{**policy,'scope':'per-thread'}):
            with self.subTest(policy=invalid), self.assertRaisesRegex(RuntimeError,'pacing evidence'):
                self.check(invalid)
