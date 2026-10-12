import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakePluginContext:
    def __init__(self, finalize_tally=True):
        self.hooks = []
        self.unload_callbacks = []
        self.finalize_tally = finalize_tally
        self.commands = []

    def get_config(self, key, default=None):
        return self.finalize_tally if key == 'finalize_tally' else default

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def on_unload(self, callback):
        self.unload_callbacks.append(callback)

    def register_command(self, name, handler, description=''):
        self.commands.append((name, handler))


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


    def test_public_numeric_command_is_read_only_and_rejects_unconfirmed_clear(self):
        import json
        from unittest.mock import patch

        plugin = load_plugin_module()
        context = FakePluginContext()
        plugin.register(context)
        self.assertEqual([name for name, handler in context.commands], ['usage-ledger'])
        handler = context.commands[0][1]
        with patch.object(plugin, '_read_numeric_endpoint', return_value={'current':{'input_tokens':12}}) as endpoint:
            self.assertEqual(json.loads(handler('{"action":"tally"}')), {'current':{'input_tokens':12}})
            endpoint.assert_called_once_with('tally', False)
            endpoint.reset_mock()
            self.assertEqual(json.loads(handler('{"action":"clear","confirm":false}'))['error'], 'explicit confirmation required')
            endpoint.assert_not_called()

    def test_resume_after_external_interval_preserves_newer_totals(self):
        import sqlite3
        import tempfile
        import hermes_constants
        from plugins import plugin_storage
        from unittest.mock import patch

        for pending in (False, True):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                tally = root / 'tally.md'
                tally.write_text('## Current response\nWords Running tally: 40\nWorking Time Running tally: 30 seconds\nWorking Time Unavailable Responses: 2\n', encoding='utf-8')
                plugin = load_plugin_module()
                def fixture_db(_plugin_id, name):
                    return sqlite3.connect(root / name)
                with patch.object(hermes_constants, 'get_hermes_home', return_value=root), patch.object(plugin_storage, 'plugin_db', side_effect=fixture_db):
                    plugin.register(FakePluginContext())
                    plugin.record_turn_started(turn_id='fixture-one')
                    plugin.record_completed_response(turn_id='fixture-one', assistant_response='one')
                    if pending:
                        with patch.object(plugin._tally_writer, '_replace', side_effect=OSError('fixture failure')):
                            plugin.record_completed_response(turn_id='fixture-pending', assistant_response='one')
                    plugin._close_resources()
                    plugin.register(FakePluginContext(finalize_tally=False))
                    plugin.record_observed_request(api_request_id='fixture-external', usage={'input_tokens':12,'output_tokens':3})
                    external = '## Current response\nWords Running tally: 400\nWorking Time Running tally: 300 seconds\nWorking Time Unavailable Responses: 5\n'
                    tally.write_text(external, encoding='utf-8')
                    plugin._close_resources()
                    plugin.register(FakePluginContext())
                    plugin.record_turn_started(turn_id='fixture-resumed')
                    plugin.record_completed_response(turn_id='fixture-resumed', assistant_response='one')
                    plugin.recover_pending_tally()
                    self.assertEqual(tally.read_text(encoding='utf-8'), external)
                    self.assertIsNone(plugin._tally_writer)
                    plugin._close_resources()

    def test_external_tally_mode_never_invokes_the_automatic_writer(self):
        from unittest.mock import Mock, patch

        plugin = load_plugin_module()
        context = FakePluginContext(finalize_tally=False)
        plugin.register(context)
        observed = Mock()
        with patch.object(plugin, '_get_tally_writer') as writer, patch.object(plugin, '_get_ledger', return_value=observed):
            callbacks = dict(context.hooks)
            callbacks['pre_llm_call'](turn_id='fixture-external-turn')
            callbacks['post_api_request'](turn_id='fixture-external-turn', api_request_id='fixture-external-request',
                                          usage={'input_tokens':12,'output_tokens':3})
            callbacks['post_llm_call'](turn_id='fixture-external-turn', assistant_response='fixture only')
            callbacks['on_session_finalize']()
            writer.assert_not_called()
            observed.record_observed_request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
