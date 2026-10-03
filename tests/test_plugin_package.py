import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakePluginContext:
    def __init__(self):
        self.hooks = []
        self.unload_callbacks = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def on_unload(self, callback):
        self.unload_callbacks.append(callback)


def load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        "usage_ledger_plugin",
        PROJECT_ROOT / "__init__.py",
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PluginPackageTests(unittest.TestCase):
    def test_manifest_and_registration_declare_narrow_phase_c_hooks(self):
        manifest = (PROJECT_ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("name: usage-ledger", manifest)
        for hook in ("pre_llm_call", "post_api_request", "post_llm_call", "on_session_finalize"):
            self.assertIn(f"- {hook}", manifest)

        plugin = load_plugin_module()
        context = FakePluginContext()
        plugin.register(context)

        self.assertEqual(
            [name for name, _ in context.hooks],
            ["pre_llm_call", "post_api_request", "post_llm_call", "on_session_finalize"],
        )
        for _, callback in context.hooks:
            self.assertTrue(
                any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in inspect.signature(callback).parameters.values()
                )
            )
        self.assertEqual(len(context.unload_callbacks), 1)
        self.assertTrue(callable(context.unload_callbacks[0]))


if __name__ == "__main__":
    unittest.main()
