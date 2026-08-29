import unittest

from coding_agent_nju.tools import ToolBox


class ToolBoxTests(unittest.TestCase):
    def test_file_tools_stay_inside_workspace(self) -> None:
        with self.subTest("write, read and replace"):
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                tools = ToolBox(directory)

                result = tools.write_file("src/app.py", "print('hello')\n")
                self.assertTrue(result["ok"])

                read = tools.read_file("src/app.py")
                self.assertEqual(read, {"ok": True, "content": "print('hello')\n"})

                replaced = tools.replace_in_file("src/app.py", "hello", "world")
                self.assertTrue(replaced["ok"])
                self.assertEqual(tools.read_file("src/app.py")["content"], "print('world')\n")

    def test_path_escape_is_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tools = ToolBox(directory)
            result = tools.call("read_file", '{"path": "../secret.txt"}')
            self.assertTrue(result.startswith('{"ok": false'))


if __name__ == "__main__":
    unittest.main()
