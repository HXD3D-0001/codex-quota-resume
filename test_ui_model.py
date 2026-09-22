import unittest
from ui_model import countdown,display_state,lifecycle_problem

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

    def test_green_describes_detection_not_all_task_history(self):
        report={'connected':True,'enabled':True,'usage':{'observed_at':100}}
        self.assertEqual(display_state(report,100)['status'],'监测正常 · 未发现待恢复任务')

    def test_incomplete_scan_is_not_green(self):
        report={'connected':True,'enabled':True,'usage':{'observed_at':100},
                'inspections':[{'reason':'read_failed'}]}
        self.assertEqual(display_state(report,100)['color'],'#fbbf24')


class StartupVisibilityTests(unittest.TestCase):
    """A dead listener means the next Codex launch shows nothing at all."""

    def healthy(self):
        return {'connected':True,'enabled':True,'usage':{'observed_at':100}}

    def test_healthy_listener_is_not_reported(self):
        lifecycle={'heartbeat_age':3,'task_registered':True}
        self.assertIsNone(lifecycle_problem(lifecycle))
        self.assertEqual(display_state(self.healthy(),100,lifecycle)['status'],'监测正常 · 未发现待恢复任务')

    def test_stale_heartbeat_turns_the_bar_yellow(self):
        view=display_state(self.healthy(),100,{'heartbeat_age':90,'task_registered':True})
        self.assertEqual(view['color'],'#fbbf24')
        self.assertIn('心跳中断',view['status'])
        self.assertEqual(view['startup_problem'],view['status'])

    def test_missing_heartbeat_is_reported_rather_than_ignored(self):
        view=display_state(self.healthy(),100,{})
        self.assertEqual(view['color'],'#fbbf24')
        self.assertIn('无心跳',view['status'])

    def test_unregistered_startup_is_reported_only_when_known(self):
        unknown=display_state(self.healthy(),100,{'heartbeat_age':1})
        self.assertIsNone(unknown['startup_problem'])
        known=display_state(self.healthy(),100,{'heartbeat_age':1,'task_registered':False})
        self.assertIn('自动启动未注册',known['status'])

    def test_no_lifecycle_information_stays_green(self):
        # Nothing is known without a heartbeat file, so nothing is claimed.
        self.assertEqual(display_state(self.healthy(),100)['color'],'#6ee7b7')

    def test_balance_and_countdown_are_unaffected_by_the_warning(self):
        report={'connected':True,'enabled':True,
                'usage':{'observed_at':100,'windows':[{'minutes':300,'used':40,'reset':1000}]}}
        view=display_state(report,100,{'heartbeat_age':900})
        self.assertEqual(view['groups'][0]['balance'],'60%')
        self.assertEqual(view['groups'][0]['countdown'],'15m')


if __name__=='__main__':unittest.main()
