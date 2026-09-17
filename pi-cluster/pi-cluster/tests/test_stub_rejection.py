import contextlib
import io
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from controller import cli, executor
from controller.model import StubBackend
from tests.util import SMOKE, catalog


class StubRejection(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_every_stub_rejected_without_side_effects(self):
        cat = catalog()
        stubs = [n for n, b in cat.backends.items() if b["status"] == "stub"]
        self.assertEqual(sorted(stubs), ["laptop-docker", "laptop-vm", "pi5"])
        for name in stubs:
            with self.subTest(name):
                with self.assertRaisesRegex(
                        StubBackend, f"^backend {name} is declared but not implemented$"):
                    executor.run(SMOKE, name, cat, self.tmp)
                self.assertEqual(list(self.tmp.iterdir()), [], "no experiment record")
        if shutil.which("ip"):
            ns = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True).stdout
            self.assertNotIn("pic-", ns, "no node was created")

    def test_cli_message_and_exit_code(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.main(["run", str(SMOKE), "--backend", "pi5",
                           "--experiments", str(self.tmp)])
        self.assertEqual(rc, 2)
        self.assertEqual(err.getvalue().strip(),
                         "ERROR: backend pi5 is declared but not implemented")
        self.assertEqual(list(self.tmp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
