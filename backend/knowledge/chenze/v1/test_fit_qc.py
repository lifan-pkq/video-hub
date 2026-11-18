import unittest
from fit_qc import evaluate
class FitQC(unittest.TestCase):
 def test_70_is_not_fit60(self):
  q=evaluate(1155,1225,60);self.assertEqual(q['duration_seconds'],'70');self.assertEqual(q['status'],'REJECT_OVER_LIMIT');self.assertFalse(q['automatic_trim'])
 def test_41_is_not_fit30(self):
  q=evaluate(1237,1278,30);self.assertEqual(q['duration_seconds'],'41');self.assertEqual(q['status'],'REJECT_OVER_LIMIT')
 def test_exact_cap_still_requires_human_context(self):self.assertEqual(evaluate(0,30,30)['status'],'NEEDS_HUMAN_REVIEW')
 def test_context_without_exact_boundary_not_fit(self):self.assertEqual(evaluate(0,30,30,context_verified=True,sentence_tail_verified=True,asr_human_verified=True)['status'],'NEEDS_HUMAN_REVIEW')
 def test_fit_scope_is_not_publication(self):
  q=evaluate(0,30,30,precision='human_verified_original_timeline',context_verified=True,sentence_tail_verified=True,asr_human_verified=True);self.assertEqual(q['status'],'FIT');self.assertFalse(q['publication_approved'])
 def test_missing_tail_blocks_fit(self):self.assertEqual(evaluate(0,29,30,precision='human_verified_original_timeline',context_verified=True,asr_human_verified=True)['status'],'NEEDS_HUMAN_REVIEW')
 def test_decimal_no_float_rounding_lenience(self):self.assertEqual(evaluate('0','30.000001','30')['status'],'REJECT_OVER_LIMIT')
 def test_invalid(self):
  for a,b,c in [(-1,30,30),(30,0,30),(0,0,30),(0,30,0),('NaN',30,30),(0,'Infinity',30),('bad',30,30)]:
   with self.subTest(a=a,b=b,c=c):self.assertEqual(evaluate(a,b,c)['status'],'INVALID_INTERVAL')
if __name__=='__main__':unittest.main()
