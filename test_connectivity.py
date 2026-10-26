import unittest
from connectivity import decode_modes,encode_modes,preserve_access
class ConnectivityTests(unittest.TestCase):
 def test_observed_acc_only_connectability(self):
  self.assertEqual(decode_modes(bytes.fromhex('010d16000301000203030304030503')),[3,0,3,3,3,3])
 def test_encoding(self):
  self.assertEqual(encode_modes([0,0,3,3,3,3]).hex(),'000001000203030304030503')
 def test_rejects_unknown_modes(self):
  with self.assertRaises(ValueError):encode_modes([0,1,3,3,3,3])
 def test_assigns_surviving_slot(self):
  slots=[{'slot':0,'frame_hex':'a0ff','interval_ms':900},{'slot':1,'frame_hex':'ffff','interval_ms':0}]
  self.assertEqual(preserve_access(slots,[3,0,3,3,3,3]),[0,0,3,3,3,3])
 def test_refuses_no_active_slots(self):
  with self.assertRaises(ValueError):preserve_access([], [3]*6)
if __name__=='__main__':unittest.main()
