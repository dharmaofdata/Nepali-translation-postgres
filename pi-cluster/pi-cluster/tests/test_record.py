import json
import pathlib
import shutil
import tempfile
import unittest

from controller.record import Recorder, git_state


class Record(unittest.TestCase):
    def test_write(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            rec = Recorder(tmp)
            rec.emit("a", x=1)
            rec.emit("b")
            path = rec.write({"verdict": "PASS"})
            r = json.loads(path.read_text())
            self.assertEqual(r["experiment_id"], rec.id)
            self.assertEqual([e["event"] for e in r["events"]], ["a", "b"])
            lines = (rec.dir / "events.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(l)["event"] for l in lines], ["a", "b"])
            self.assertTrue(all(e1["t"] <= e2["t"] for e1, e2 in zip(r["events"], r["events"][1:])))
        finally:
            shutil.rmtree(tmp)

    def test_git_state_shape(self):
        g = git_state()
        self.assertEqual(set(g), {"commit", "dirty"})


if __name__ == "__main__":
    unittest.main()
