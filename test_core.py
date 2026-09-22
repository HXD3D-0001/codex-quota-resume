import unittest
from core import normalize_usage, quota_ready, next_poll_delay, candidate_key


def usage(short=100, weekly=20, reset=200):
    return {'ordinaryUsageAllowed': short < 100 and weekly < 100,
            'rateLimitsByLimitId': {'codex': {
                'primary': {'usedPercent': short, 'windowDurationMins': 300, 'resetsAt': reset},
                'secondary': {'usedPercent': weekly, 'windowDurationMins': 10080, 'resetsAt': 900}}}}


def task(status='failed', error="You've hit your usage limit.", turn='turn-a', ended=110):
    return {'thread': {'id': 'thread-a', 'status': {'type': 'idle'}},
            'turns': [{'id': turn, 'status': status, 'error': {'message': error}, 'completedAt': ended}]}


class QuotaTests(unittest.TestCase):
    def test_clock_passing_reset_is_not_recovery(self):
        self.assertFalse(quota_ready(normalize_usage(usage(), 100), 201))

    def test_live_recovery_allows_resume(self):
        self.assertTrue(quota_ready(normalize_usage(usage(0, 20, 500), 201), 201))

    def test_weekly_block_prevents_resume(self):
        self.assertFalse(quota_ready(normalize_usage(usage(0, 100), 201), 201))

    def test_missing_windows_and_nan_are_unknown(self):
        for raw in [{}, {'rateLimits': {'primary': {'usedPercent': float('nan')}}}]:
            self.assertFalse(quota_ready(normalize_usage(raw, 100), 100))

    def test_authoritative_multi_bucket_wins(self):
        raw=usage(); raw['rateLimits'] = usage(0)['rateLimitsByLimitId']['codex']
        self.assertFalse(quota_ready(normalize_usage(raw, 100), 100))

    def test_secondary_missing_is_unknown(self):
        raw=usage(0); raw['rateLimitsByLimitId']['codex']['secondary']=None
        self.assertFalse(quota_ready(normalize_usage(raw, 100), 100))

    def test_server_denial_wins_over_percent(self):
        raw=usage(0); raw['ordinaryUsageAllowed']=False
        self.assertFalse(quota_ready(normalize_usage(raw, 100), 100))

    def test_stale_or_future_snapshot_blocks(self):
        data=normalize_usage(usage(0),100)
        self.assertFalse(quota_ready(data,170))
        self.assertFalse(quota_ready(data,90))

    def test_reset_scheduling_and_overdue_retry(self):
        data=normalize_usage(usage(),100)
        self.assertEqual(next_poll_delay(data,100),30)
        self.assertEqual(next_poll_delay(data,197),3)
        self.assertEqual(next_poll_delay(data,201),5)


class TaskTests(unittest.TestCase):
    def test_latest_failed_quota_turn_is_candidate(self):
        self.assertEqual(candidate_key(task(),None,100),'thread-a:turn-a')

    def test_completed_cancelled_approval_and_unrelated_errors_are_not_candidates(self):
        for status in ['completed','interrupted','inProgress']:
            self.assertIsNone(candidate_key(task(status),None,100))
        for error in ['HTTP 429 too many requests', 'Network failure', 'Please approve this action']:
            self.assertIsNone(candidate_key(task(error=error),None,100))
        detail=task();detail['thread']['status']={'type':'active','activeFlags':['waitingOnApproval']}
        self.assertIsNone(candidate_key(detail,None,100))

    def test_old_failure_is_not_restarted_on_new_install(self):
        self.assertIsNone(candidate_key(task(ended=90),None,100))

    def test_explicit_marker_allows_old_task_but_is_bound_to_turn(self):
        self.assertEqual(candidate_key(task('completed',ended=90),'turn-a',100),'thread-a:turn-a')
        self.assertIsNone(candidate_key(task('completed',turn='turn-b'),'turn-a',100))

    def test_archived_and_busy_task_never_resume(self):
        for state in ['active','waitingForInput']:
            detail=task();detail['thread']['status']={'type':state}
            self.assertIsNone(candidate_key(detail,'turn-a',100))
        detail=task();detail['thread']['archived']=True
        self.assertIsNone(candidate_key(detail,'turn-a',100))

    def test_quota_failure_in_error_state_is_resumable(self):
        for state in ['systemError','notLoaded']:
            detail=task(error='You’ve hit your usage limit. Try again at 5:27 PM.')
            detail['thread']['status']={'type':state}
            self.assertEqual(candidate_key(detail,None,100),'thread-a:turn-a')

    def test_waiting_flags_override_error_state(self):
        detail=task();detail['thread']['status']={'type':'systemError','activeFlags':['waitingOnApproval']}
        self.assertIsNone(candidate_key(detail,None,100))

if __name__=='__main__': unittest.main()
