"""Regression test: capture.py must wait through the engine's initial
"running" record until the record is final."""
import json, os, sys, tempfile, threading, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402


class WaitFinal(unittest.TestCase):
    def test_waits_through_running_record(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "forwarded-1-0001.json")
            json.dump({"termination": "running", "emitted_text": ""}, open(path, "w"))

            def finalize():
                time.sleep(0.5)
                json.dump({"termination": "finished_stop", "emitted_text": "done"}, open(path, "w"))

            writer = threading.Thread(target=finalize)
            writer.start()
            start = time.monotonic()
            rec = capture.wait_final(d, set())
            writer.join()
            self.assertGreaterEqual(time.monotonic() - start, 0.4)
            self.assertEqual(rec["termination"], "finished_stop")
            self.assertEqual(rec["emitted_text"], "done")

    def test_running_record_is_never_final(self):
        with tempfile.TemporaryDirectory() as d:
            json.dump({"termination": "running"}, open(os.path.join(d, "forwarded-1-0001.json"), "w"))
            rec = capture.wait_final(d, set(), attempts=3, interval=0.01)
            self.assertFalse(capture.is_final(rec))


if __name__ == "__main__":
    unittest.main()
