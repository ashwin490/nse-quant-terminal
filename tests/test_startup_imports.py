"""Guard against publishing app.py without compatible option-chain exports."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class StartupImportsTest(unittest.TestCase):
    def test_app_option_chain_exports_present(self):
        app_tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.ImportFrom) and node.module == "nse_option_chain":
                imports.extend(alias.name for alias in node.names)
        self.assertTrue(imports, "app.py should explicitly import option-chain exports")
        import nse_option_chain
        missing = [name for name in imports if not callable(getattr(nse_option_chain, name, None))]
        self.assertEqual(missing, [], "app.py expects missing symbols from nse_option_chain.py")


if __name__ == "__main__":
    unittest.main()
