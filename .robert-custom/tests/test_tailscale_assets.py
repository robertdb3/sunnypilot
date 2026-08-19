"""Static safety assertions for the AGNOS-independent Tailscale deployment."""
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parent.parent / "sunnypilot"


class TestTailscaleAssets(unittest.TestCase):
  def test_pinned_official_archive_and_checksum(self):
    install = (REPO / "openpilot/sunnypilot/system/tailscale/install.sh").read_text()
    self.assertIn("VERSION=1.98.9", install)
    self.assertIn("fa554ee808d7d07ee8e3ebbc0215ea087157e2a0abbf408e6e18ea7532554db6", install)
    self.assertIn("https://pkgs.tailscale.com/stable/", install)
    self.assertNotIn("curl |", install)

  def test_launch_is_early_async_and_fail_open(self):
    launcher = (REPO / "launch_chffrplus.sh").read_text()
    hook = launcher.index("tailscale/bootstrap.sh")
    manager = launcher.index("./manager.py")
    self.assertLess(hook, manager)
    self.assertIn("bootstrap.log 2>&1 &", launcher)
    bootstrap = (REPO / "openpilot/sunnypilot/system/tailscale/bootstrap.sh").read_text()
    self.assertIn("userspace-networking", bootstrap)
    self.assertNotIn("sudo", bootstrap)

  def test_enrollment_disables_routes_dns_and_tailscale_ssh(self):
    enroll = (REPO / "openpilot/sunnypilot/system/tailscale/enroll.sh").read_text()
    for flag in ("--accept-routes=false", "--accept-dns=false", "--ssh=false", "--advertise-tags=tag:comma"):
      self.assertIn(flag, enroll)
    self.assertIn("serve --bg --https=443 http://127.0.0.1:8843", enroll)
    self.assertNotIn(" funnel ", "\n".join(line for line in enroll.lower().splitlines()
                                            if not line.lstrip().startswith("#")))


if __name__ == "__main__":
  unittest.main(verbosity=2)
