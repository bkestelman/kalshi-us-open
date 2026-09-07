import copy
import subprocess
import unittest
from types import SimpleNamespace
from review_notify import deliver, enqueue, health_event


class NotificationTests(unittest.TestCase):
    def test_failed_queue_is_durable_and_retried(self):
        state = {'pending': {}, 'sent': {}}
        enqueue(state, 'review:test', 'hello')
        saved = []
        deliver(state, lambda s: saved.append(copy.deepcopy(s)),
                lambda *a, **k: SimpleNamespace(returncode=1, stderr='offline', stdout=''))
        self.assertIn('review:test', saved[-1]['pending'])
        self.assertNotIn('review:test', state['sent'])
        calls = []
        def success(*a, **k):
            calls.append(a)
            return SimpleNamespace(returncode=0)
        deliver(state, lambda s: None, success)
        enqueue(state, 'review:test', 'duplicate')
        deliver(state, lambda s: None, success)
        self.assertEqual(len(calls), 1)
        self.assertFalse(state['pending'])

    def test_health_deduplication_reminder_and_recovery(self):
        state = {'pending': {}, 'sent': {}}
        health_event(state, {'warnings': ['stale score']}, 2000)
        health_event(state, {'warnings': ['stale score']}, 2060)
        self.assertEqual(len(state['pending']), 1)
        health_event(state, {'warnings': ['stale score']}, 3800)
        self.assertEqual(len(state['pending']), 2)
        health_event(state, {'warnings': []}, 3860)
        self.assertEqual(len(state['pending']), 3)
        health_event(state, {'warnings': []}, 3920)
        self.assertEqual(len(state['pending']), 3)

    def test_timeout_retains_pending(self):
        state = {'pending': {'test': 'hello'}, 'sent': {}}
        def timeout(*a, **k):
            raise subprocess.TimeoutExpired('codex queue', 20)
        deliver(state, lambda s: None, timeout)
        self.assertIn('test', state['pending'])
        self.assertTrue(state['last_error'])


if __name__ == '__main__':
    unittest.main()
