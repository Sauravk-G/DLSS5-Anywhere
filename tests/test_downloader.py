"""
Component downloading, minus the network.

The parts worth pinning down here are the ones that went wrong in practice: choosing the
right release asset, unpacking a format the standard library cannot read, never leaving a
half-unpacked component behind, and giving OptiScaler.dll the names the installer looks
for. All of it runs offline.
"""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from dlss5_anywhere.core.downloader import ComponentDownloader, _SevenZipUnavailable


class TestAssetSelection(unittest.TestCase):
    """OptiScaler publishes one archive per release, and the name is not stable."""

    def pick(self, *names):
        assets = [{"name": n, "browser_download_url": f"https://example/{n}"} for n in names]
        chosen = ComponentDownloader._pick_optiscaler_asset(assets)
        return None if chosen is None else chosen["name"]

    def test_the_real_release_asset_is_found(self):
        """The actual name shipped by v0.9.4, which is not a tidy one."""
        self.assertEqual(
            self.pick("Optiscaler_0.9.4-final.20260718._MM.7z"),
            "Optiscaler_0.9.4-final.20260718._MM.7z",
        )

    def test_a_zip_is_preferred_over_a_7z(self):
        """The standard library can open a zip; a .7z needs an external tool."""
        self.assertEqual(
            self.pick("OptiScaler_v1.0.7z", "OptiScaler_v1.0.zip"),
            "OptiScaler_v1.0.zip",
        )

    def test_an_archive_named_only_for_its_version_still_counts(self):
        self.assertEqual(self.pick("v0.9.4.7z"), "v0.9.4.7z")

    def test_checksums_and_notes_are_not_archives(self):
        self.assertIsNone(self.pick("SHA256SUMS.txt", "release-notes.md"))

    def test_no_assets_at_all(self):
        self.assertIsNone(self.pick())


class TestSevenZipTool(unittest.TestCase):

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows-only tool discovery")
    def test_windows_ships_something_that_can_read_7z(self):
        """bsdtar has been in System32 since Windows 10 1803.

        If this ever fails on a supported Windows, the OptiScaler download has no way to
        unpack itself and the failure message is the only thing standing between the user
        and a silent half-install.
        """
        tool = ComponentDownloader._find_7z_tool()
        self.assertIsNotNone(tool, "no 7z-capable tool found on this system")
        self.assertTrue(Path(tool[0]).is_file(), tool[0])

    def test_a_missing_tool_raises_rather_than_half_extracting(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "x.7z"
            archive.write_bytes(b"not really an archive")
            target = Path(tmp) / "out"
            original = ComponentDownloader._find_7z_tool
            try:
                ComponentDownloader._find_7z_tool = staticmethod(lambda: None)
                with self.assertRaises(_SevenZipUnavailable):
                    ComponentDownloader._extract_7z(archive, target)
            finally:
                ComponentDownloader._find_7z_tool = original


class TestStagedPublish(unittest.TestCase):
    """A component directory must only ever see a complete unpack."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_pub_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.staged = self.tmp / "staged"
        self.target = self.tmp / "target"

    def test_a_whole_tree_is_published(self):
        (self.staged / "Licenses").mkdir(parents=True)
        (self.staged / "OptiScaler.dll").write_bytes(b"binary")
        (self.staged / "OptiScaler.ini").write_text("[Opti]", encoding="utf-8")
        (self.staged / "Licenses" / "XeSS.txt").write_text("licence", encoding="utf-8")

        published = ComponentDownloader._publish(self.staged, self.target)

        self.assertEqual(published, 3, "directories are not files")
        self.assertTrue((self.target / "OptiScaler.dll").is_file())
        self.assertTrue((self.target / "Licenses" / "XeSS.txt").is_file())

    def test_publishing_nothing_leaves_nothing(self):
        """The failure path: extraction blew up, so staging is empty.

        A download that failed used to extract straight into the component directory and
        leave whatever it had managed to write. `check_component_status` counts files, so
        the component then reported itself installed and the next build silently used a
        component that was missing its binaries.
        """
        self.staged.mkdir(parents=True)
        published = ComponentDownloader._publish(self.staged, self.target)

        self.assertEqual(published, 0)
        self.assertEqual(list(self.target.iterdir()), [])


class TestOptiScalerProxyNames(unittest.TestCase):
    """Current releases ship one binary that has to be renamed to be used."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlss5_proxy_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_installers_two_filenames_are_created(self):
        """The plan asks for optiscaler/dxgi.dll and optiscaler/nvngx.dll by name.

        Without these the install falls back to plain ReShade and the FSR bridge quietly
        does not happen.
        """
        (self.tmp / "OptiScaler.dll").write_bytes(b"MZ" + b"\x00" * 32)

        created = ComponentDownloader._name_optiscaler_proxies(self.tmp)

        self.assertEqual(created, 2)
        for proxy in ("dxgi.dll", "nvngx.dll"):
            self.assertTrue((self.tmp / proxy).is_file(), proxy)
            self.assertEqual((self.tmp / proxy).read_bytes(), b"MZ" + b"\x00" * 32)

    def test_an_older_release_that_already_names_them_is_left_alone(self):
        (self.tmp / "OptiScaler.dll").write_bytes(b"new")
        (self.tmp / "dxgi.dll").write_bytes(b"shipped already")

        created = ComponentDownloader._name_optiscaler_proxies(self.tmp)

        self.assertEqual(created, 1, "only the missing name is created")
        self.assertEqual((self.tmp / "dxgi.dll").read_bytes(), b"shipped already")

    def test_nothing_to_rename_is_not_an_error(self):
        self.assertEqual(ComponentDownloader._name_optiscaler_proxies(self.tmp), 0)


if __name__ == "__main__":
    unittest.main()
