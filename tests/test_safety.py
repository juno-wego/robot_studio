"""Offline checks for routes that must reject a mismatched physical target."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installer_backend as backend
from installer_support import https_url


class RecoveryTest(unittest.TestCase):
    def test_detects_thor_and_old_jetson_without_guessing_carrier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            device = root / "2-1"
            device.mkdir()
            (device / "idVendor").write_text("0955\n")
            with patch.object(backend, "Path", return_value=root):
                (device / "idProduct").write_text("7026\n")
                found = backend.recovery_device()
                self.assertEqual((found.pid, found.port), ("7026", "2-1"))
                (device / "idProduct").write_text("7e19\n")
                self.assertIn("Xavier NX", backend.recovery_device().model)
                with self.assertRaisesRegex(ValueError, "SDK Manager"):
                    backend.recovery_pid()

    def test_other_module_cannot_flash(self):
        option = backend.PROFILES["p4071-7.2.1"]
        with patch.object(backend, "require_recovery") as probe, patch.object(backend, "run") as run:
            with self.assertRaisesRegex(ValueError, "맞지 않습니다"):
                backend.flash(option, backend.NVME, "7323")
            probe.assert_not_called()
            run.assert_not_called()

    def test_qspi_requires_nx16_and_d115(self):
        self.assertTrue(backend.PROFILES["d115-6.2.1"].supports_pid("7323", backend.QSPI))
        self.assertFalse(backend.PROFILES["d115-6.2.1"].supports_pid("7423", backend.QSPI))
        self.assertFalse(backend.PROFILES["d135-6.2.1"].supports_pid("7323", backend.QSPI))

    def test_wrong_usb_port_is_rejected(self):
        with patch.object(backend, "recovery_device", return_value=backend.RecoveryDevice("7323", "2-1")):
            with self.assertRaisesRegex(ValueError, "USB 포트"):
                backend.require_recovery("7323", "2-2")

    def test_rejects_nvme_layout_when_app_is_not_on_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plan.xml").write_text(
                '<layout><device type="external" instance="0"><partition name="APP_b"/></device></layout>'
            )
            with self.assertRaisesRegex(ValueError, "레이아웃"):
                backend.require_layout(root, "plan.xml", [("external", "0")], ("external", "0"))

    def test_manufacturer_download_requires_https(self):
        for url in ("http://vendor.example/BSP.tar.gz", "file:///tmp/bsp", "https://admin@vendor.example/bsp"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                https_url(url)


if __name__ == "__main__":
    unittest.main()
