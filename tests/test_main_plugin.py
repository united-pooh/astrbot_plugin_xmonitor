from __future__ import annotations

import unittest

import main
from config import XMonitorRuntimeConfig


class XMonitorPluginTest(unittest.TestCase):
    def test_build_services_does_not_require_star_tools_send_message_by_id(
        self,
    ) -> None:
        plugin = object.__new__(main.XMonitor)
        plugin.context = object()
        plugin.config = {}
        plugin.history_store = object()
        runtime_config = XMonitorRuntimeConfig(
            api_key="secret",
            target_account="Blue_ArchiveJP",
            check_interval_minutes=10,
            subscribe_groups=["group-1"],
            notify_user=None,
            source_logo=None,
            auto_translation_enabled=False,
            translation_provider_id=None,
            translation_system_prompt="翻译提示",
            group_render_overrides={},
        )

        original = getattr(main.StarTools, "send_message_by_id", None)
        if hasattr(main.StarTools, "send_message_by_id"):
            delattr(main.StarTools, "send_message_by_id")
        try:
            plugin._build_services(runtime_config)
        finally:
            if original is not None:
                setattr(main.StarTools, "send_message_by_id", original)

        self.assertIs(plugin.notification_sender.send_message.__self__, plugin)
        self.assertIs(
            plugin.notification_sender.send_message.__func__,
            main.XMonitor._send_message_by_id,
        )


if __name__ == "__main__":
    unittest.main()
