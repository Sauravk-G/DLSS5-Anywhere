"""The CLI builds under the profile it was given.

The GUI has passed the selected profile to both deploy paths for a while. The CLI
imported ProfileManager, offered no way to name a profile, and passed none - so every
`build` and `install` from the command line quietly used the defaults. On a profile whose
point is the neural consumer that is not a missing convenience: `--profile Dfc` and no
flag at all produced identical folders, one of them silently RenoDX.
"""

import argparse
import unittest
from unittest import mock

from dlss5_anywhere import cli
from dlss5_anywhere.core.profiles import ProfileManager


class TestProfileResolution(unittest.TestCase):

    def test_no_profile_named_means_the_defaults(self):
        self.assertIsNone(cli._profile_for(argparse.Namespace(profile=None)))

    def test_a_named_profile_is_loaded(self):
        with mock.patch.object(
            ProfileManager, "load_profile", return_value={"neural_consumer": "dfc"}
        ) as load:
            profile = cli._profile_for(argparse.Namespace(profile="Dfc"))
        load.assert_called_once_with("Dfc")
        self.assertEqual(profile["neural_consumer"], "dfc")

    def test_an_older_namespace_without_the_flag_still_works(self):
        """cmd_build and cmd_install are called with whatever argparse produced, and a
        missing attribute would be an AttributeError rather than a default."""
        self.assertIsNone(cli._profile_for(argparse.Namespace()))


class TestBothDeployPathsCarryIt(unittest.TestCase):

    def _args(self, **kw):
        base = dict(
            executable="game.exe", strategy=None, output=None, profile="Dfc", force=True
        )
        base.update(kw)
        return argparse.Namespace(**base)

    def _run(self, command, installer_method):
        profile = {"neural_consumer": "dfc"}
        with mock.patch.object(cli.GameDetector, "analyze"), \
             mock.patch.object(ProfileManager, "load_profile", return_value=profile), \
             mock.patch.object(cli.ModInstaller, installer_method) as deploy:
            deploy.return_value = mock.Mock(
                success=True, message="", files_copied=[], configs_written=[],
                target_directory="out", backup_manifest="manifest",
            )
            command(self._args())
        return deploy.call_args.kwargs

    def test_build_passes_it(self):
        self.assertEqual(
            self._run(cli.cmd_build, "prepare_build_folder")["profile"],
            {"neural_consumer": "dfc"},
        )

    def test_install_passes_it(self):
        self.assertEqual(
            self._run(cli.cmd_install, "install_to_game")["profile"],
            {"neural_consumer": "dfc"},
        )


class TestTheFlagIsReachable(unittest.TestCase):

    def test_build_and_install_both_take_profile(self):
        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        if parser is None:
            self.skipTest("CLI parser is built inside main()")
        for command in ("build", "install"):
            args = parser.parse_args([command, "game.exe", "--profile", "Dfc"])
            self.assertEqual(args.profile, "Dfc")


if __name__ == "__main__":
    unittest.main()
