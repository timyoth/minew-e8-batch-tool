import unittest
from types import SimpleNamespace as S
from unittest.mock import patch
import configure_tag as c
A='C3:00:00:00:00:45'
class CurrentAdvertisementTests(unittest.IsolatedAsyncioTestCase):
 async def test_new_connectable_packet_overrides_stale_device_details(self):
  old=S(is_connectable=False,bluetooth_address_type=0)
  fresh=S(is_connectable=True,bluetooth_address_type=1)
  device=S(address=A,details=(old,None))
  async def scanner(predicate,timeout):
   self.assertFalse(predicate(device,S(platform_data=(None,(old,None)))))
   self.assertTrue(predicate(device,S(platform_data=(None,(fresh,None)))))
   return device
  with patch.object(c.BleakScanner,'find_device_by_filter',scanner):
   actual,event=await c.find_connectable(A,15)
  self.assertIs(actual,device);self.assertIs(event,fresh)
 async def test_stale_connectable_packet_does_not_override_current_packet(self):
  old=S(is_connectable=True);fresh=S(is_connectable=False)
  device=S(address=A,details=(old,None))
  async def scanner(predicate,timeout):
   self.assertFalse(predicate(device,S(platform_data=(None,(fresh,None)))))
   self.assertFalse(predicate(S(address='C3:00:00:00:00:40'),S(platform_data=(None,(old,None)))))
   return None
  with patch.object(c.BleakScanner,'find_device_by_filter',scanner):
   with self.assertRaisesRegex(RuntimeError,'No connectable'):await c.find_connectable(A,15)
 def test_packet_observation_changes_even_with_same_device(self):
  one=object();two=object()
  self.assertEqual(c.current_advertisement_events(S(platform_data=(None,(one,None)))),(one,))
  self.assertEqual(c.current_advertisement_events(S(platform_data=(None,(two,None)))),(two,))
if __name__=='__main__':unittest.main()
