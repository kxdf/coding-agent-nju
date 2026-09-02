import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
    def test_help_documents_multi_agent_flag(self):
        completed = subprocess.run(
            [sys.executable, "-m", "coding_agent_nju", "--help"],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("--multi-agent", completed.stdout)


if __name__ == "__main__":
    unittest.main()
