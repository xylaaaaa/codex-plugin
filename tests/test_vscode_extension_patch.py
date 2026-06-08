import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from codex_fast_patch.bundle import patch_js
from codex_fast_patch.cli import build_parser
from codex_fast_patch.vscode import (
    VscodeExtensionPaths,
    backup_vscode_extension,
    detect_vscode_extension_paths,
    extension_version,
    rollback_vscode_extension,
)


class VscodeExtensionPatchTest(unittest.TestCase):
    def make_extension(self, root: Path, version: str) -> Path:
        extension = root / f"openai.chatgpt-{version}-linux-x64"
        assets = extension / "webview" / "assets"
        assets.mkdir(parents=True)
        (extension / "package.json").write_text(
            json.dumps(
                {
                    "name": "chatgpt",
                    "contributes": {
                        "commands": [
                            {
                                "command": "chatgpt.newChat",
                                "title": "New Thread in Codex Sidebar",
                                "category": "Codex",
                            }
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
        return extension

    def write_patchable_assets(self, extension: Path) -> None:
        assets = extension / "webview" / "assets"
        out_dir = extension / "out"
        out_dir.mkdir()
        (assets / "read-service-tier-for-request-a.js").write_text(
            "async function p(e,t){let n=await u(e,t);return n===`chatgpt`?"
            "(await e.query.fetch(c,{authMethod:n,hostId:t})).requirements?.featureRequirements?.fast_mode!==!1:!1}",
            encoding="utf-8",
        )
        (assets / "use-service-tier-settings-a.js").write_text(
            "let a=i?.authMethod===`chatgpt`,u=!!i?.isLoading||a&&l,"
            "d=a&&!u&&c!=null&&c?.requirements?.featureRequirements?.fast_mode!==!1",
            encoding="utf-8",
        )
        (assets / "use-plugins-a.js").write_text(
            "function he(e){return e!==`chatgpt`}"
            "function Pe(e,{isComputerUseAvailable:t,isExternalBrowserUseAvailable:n,isInAppBrowserUseAvailable:r})"
            "{return!(!r&&Le(e)||!n&&Re(e)||!t&&ze(e))}",
            encoding="utf-8",
        )
        (assets / "check-plugin-availability-a.js").write_text(
            "(r||n!=null&&!n.isPending&&n.error==null&&n.data==null)&&(i=`connector-unavailable`)",
            encoding="utf-8",
        )
        (out_dir / "extension.js").write_text(
            "triggerNewChatViaWebview(){this.sidebarView&&this.sidebarWebviewReady&&this.postMessageToWebview(this.sidebarView.webview,{type:\"new-chat\"})}"
            "postMessageToWebview(e,r){e.postMessage(r)}"
            "e.push(ft.commands.registerCommand(x7e,async()=>{await Li(),st.triggerNewChatViaWebview()})),Xr(\"commentCodeLensEnabled\",!0)",
            encoding="utf-8",
        )

    def test_detects_newest_vscode_server_extension(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            home = Path(raw_tmp)
            old_root = home / ".vscode-server" / "extensions"
            new_root = home / ".vscode-server" / "extensions"
            self.make_extension(old_root, "26.527.60818")
            newest = self.make_extension(new_root, "26.601.21317")

            paths = detect_vscode_extension_paths(home=home)

            self.assertEqual(paths.extension_dir, newest)
            self.assertEqual(paths.assets_dir, newest / "webview" / "assets")

    def test_detect_ignores_vscode_backup_directories(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            home = Path(raw_tmp)
            root = home / ".vscode-server" / "extensions"
            newest = self.make_extension(root, "26.602.40724")
            backup = root / "openai.chatgpt-26.999.99999-linux-x64.codex-fast-backup"
            (backup / "webview" / "assets").mkdir(parents=True)

            paths = detect_vscode_extension_paths(home=home)

            self.assertIsNone(extension_version(backup))
            self.assertEqual(paths.extension_dir, newest)

    def test_patches_vscode_extension_bundle_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)

            report = patch_js(paths)

            self.assertEqual(report.patch_actions, 8)
            self.assertEqual(report.patched_files, 6)
            self.assertIn(
                "async function p(e,t){return true}",
                (paths.assets_dir / "read-service-tier-for-request-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "u=!!i?.isLoading,d=!u",
                (paths.assets_dir / "use-service-tier-settings-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "function he(e){return false}",
                (paths.assets_dir / "use-plugins-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "return!(!r&&Le(e)||!t&&ze(e))",
                (paths.assets_dir / "use-plugins-a.js").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "false&&(i=`connector-unavailable`)",
                (paths.assets_dir / "check-plugin-availability-a.js").read_text(encoding="utf-8"),
            )
            package_json = json.loads((extension / "package.json").read_text(encoding="utf-8"))
            self.assertIn(
                {
                    "command": "chatgpt.renameThread",
                    "title": "Rename Codex Thread",
                    "category": "Codex",
                    "enablement": "chatgpt.sidebarView.visible",
                },
                package_json["contributes"]["commands"],
            )
            extension_js = (extension / "out" / "extension.js").read_text(encoding="utf-8")
            self.assertIn(
                'triggerRunCommandViaWebview(e){this.sidebarView&&this.sidebarWebviewReady&&this.postMessageToWebview(this.sidebarView.webview,{type:"run-command",id:e})}',
                extension_js,
            )
            self.assertIn(
                'ft.commands.registerCommand("chatgpt.renameThread",async()=>{await Li(),st.triggerRunCommandViaWebview("renameThread")})',
                extension_js,
            )

    def test_vscode_backup_and_rollback_restore_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)

            backup_vscode_extension(paths)
            original = (paths.assets_dir / "read-service-tier-for-request-a.js").read_text(encoding="utf-8")
            (paths.assets_dir / "read-service-tier-for-request-a.js").write_text("patched", encoding="utf-8")

            rollback_vscode_extension(paths)

            self.assertEqual(
                (paths.assets_dir / "read-service-tier-for-request-a.js").read_text(encoding="utf-8"),
                original,
            )
            self.assertFalse(paths.backup_dir.exists())

    def test_vscode_backup_refuses_to_overwrite_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)
            paths.backup_dir.mkdir()

            with self.assertRaises(SystemExit):
                backup_vscode_extension(paths)

    def test_vscode_backup_can_reuse_existing_backup_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            extension = self.make_extension(Path(raw_tmp), "26.601.21317")
            self.write_patchable_assets(extension)
            paths = VscodeExtensionPaths(extension)

            backup_vscode_extension(paths)
            original_backup_package = (paths.backup_dir / "package.json").read_text(encoding="utf-8")
            (extension / "package.json").write_text('{"name":"patched"}', encoding="utf-8")

            backup_vscode_extension(paths, allow_existing=True)

            self.assertEqual(
                (paths.backup_dir / "package.json").read_text(encoding="utf-8"),
                original_backup_package,
            )

    def test_cli_has_vscode_rename_patch_command(self) -> None:
        args = build_parser().parse_args(
            [
                "patch-vscode-rename",
                "--extension-dir",
                "/tmp/openai.chatgpt-26.601.21317-linux-x64",
            ]
        )

        self.assertEqual(args.handler.__name__, "patch_vscode_rename")


if __name__ == "__main__":
    unittest.main()
