import unittest
from ui_model import countdown,display_state

class DisplayTests(unittest.TestCase):
    def test_countdown_never_claims_reset_from_clock(self):
        self.assertEqual(countdown(100,101),'更新中')
        self.assertEqual(countdown(3700,100),'1h 00m')
    def test_stale_data_changes_status_without_faking_balance(self):
        data={'connected':True,'enabled':True,'usage':{'observed_at':100,'windows':[{'minutes':300,'used':100,'reset':120}]}}
        view=display_state(data,180)
        self.assertTrue(view['stale'])
        self.assertEqual(view['groups'][0]['balance'],'0%')
        self.assertEqual(view['groups'][0]['countdown'],'更新中')
    def test_missing_window_is_not_zero(self):
        self.assertEqual(display_state({},0)['groups'][0]['balance'],'—')

if __name__=='__main__':unittest.main()
