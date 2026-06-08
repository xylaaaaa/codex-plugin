"""Patch minified JavaScript gates inside the extracted Codex bundle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .app import AppPaths
from .chrome import patch_chrome_plugin_preservation
from .patterns import (
    APIKEY_GATE_PATTERNS,
    CONNECTOR_PATTERNS,
    FAST_AUTH_PATTERNS,
    FAST_HOOK_AUTH_PATTERNS,
    FAST_MODELS_PATTERNS,
)
from .zed_remote import patch_zed_remote_open


SIDEBAR_GATE_RE = re.compile(r"([A-Z])\?\(0,\$\.jsx\)\(Sl,\{tooltipContent")

# Fallback regex for the gradient-*.js gate. Matches either the old
# "API key" phrasing or the current "not chatgpt" phrasing, regardless of
# what identifier names the minifier has picked.
APIKEY_GATE_FALLBACK_RE = re.compile(
    r"function\s+(?P<fn>[A-Za-z_$][\w$]*)"
    r"\((?P<arg>[A-Za-z_$][\w$]*)\)"
    r"\{return\s+(?P=arg)(?:===|!==)`(?:apikey|chatgpt)`\}"
)

VSCODE_FAST_QUERY_RE = re.compile(
    r"async function (?P<fn>[A-Za-z_$][\w$]*)"
    r"\((?P<args>[^)]*)\)\{let [^;]+;return .*?fast_mode!==!1:!1\}"
)

VSCODE_SERVICE_TIER_GATE_RE = re.compile(
    r"(?P<loading>[A-Za-z_$][\w$]*=!![^,]+)\|\|[A-Za-z_$][\w$]*&&[A-Za-z_$][\w$]*,"
    r"(?P<allowed>[A-Za-z_$][\w$]*)=[A-Za-z_$][\w$]*&&!?(?P<loading_ref>[A-Za-z_$][\w$]*)"
    r"&&[^,;]+fast_mode!==!1"
)

VSCODE_NEW_CHAT_COMMAND_RE = re.compile(
    r"(?P<existing>[A-Za-z_$][\w$]*\.push\("
    r"(?P<vscode>[A-Za-z_$][\w$]*)\.commands\.registerCommand\("
    r"(?P<new_chat>[A-Za-z_$][\w$]*),async\(\)=>\{await "
    r"(?P<focus>[A-Za-z_$][\w$]*)\(\),"
    r"(?P<provider>[A-Za-z_$][\w$]*)\.triggerNewChatViaWebview\(\)\}\)\)),"
    r"(?P<tail>[A-Za-z_$][\w$]*\(\"commentCodeLensEnabled\")"
)

VSCODE_RENAME_COMMAND = {
    "command": "chatgpt.renameThread",
    "title": "Rename Codex Thread",
    "category": "Codex",
}


@dataclass
class PatchReport:
    """Counts and warnings collected while patching bundled JavaScript files."""

    patched_files: int = 0
    patch_actions: int = 0
    warnings: list[str] | None = None
    patched_paths: set[Path] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    def add_patch(self, message: str) -> None:
        self.patch_actions += 1
        print(f"[PATCHED] {message}")

    def add_file(self, path: Path | None = None) -> None:
        if path is None:
            self.patched_files += 1
            return
        if path in self.patched_paths:
            return
        self.patched_paths.add(path)
        self.patched_files += 1

    def warn(self, message: str) -> None:
        assert self.warnings is not None
        self.warnings.append(message)
        print(f"[WARN] {message}")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def replace_first(content: str, old: str, new: str) -> tuple[str, bool]:
    if old not in content:
        return content, False
    return content.replace(old, new, 1), True


def patch_fast_mode(paths: AppPaths, report: PatchReport) -> None:
    files = sorted(paths.assets_dir.glob("permissions-mode-helpers-*.js"))
    for path in files:
        content = read_text(path)
        original = content

        content = patch_fast_auth(path, content, report)
        content = patch_fast_hook(path, content, report)
        content = patch_fast_models(path, content, report)
        content = patch_vscode_fast_service_tier(path, content, report)

        if content != original:
            write_text(path, content)
            report.add_file(path)

    if not files:
        patch_vscode_fast_mode(paths, report)


def patch_fast_auth(path: Path, content: str, report: PatchReport) -> str:
    for pattern in FAST_AUTH_PATTERNS:
        content, changed = replace_first(content, pattern, "return true")
        if changed:
            report.add_patch(f"{path.name}: fast auth check -> return true")
            return content

    content, count = VSCODE_FAST_QUERY_RE.subn(
        lambda match: f"async function {match.group('fn')}({match.group('args')}){{return true}}",
        content,
        count=1,
    )
    if count > 0:
        report.add_patch(f"{path.name}: VS Code fast auth check -> return true")
        return content

    if "authMethod" in content and "fast_mode" in content:
        report.warn(f"{path.name}: fast auth pattern changed; inspect manually")
    return content


def patch_fast_hook(path: Path, content: str, report: PatchReport) -> str:
    for old, new in FAST_HOOK_AUTH_PATTERNS:
        content, changed = replace_first(content, old, new)
        if changed:
            report.add_patch(f"{path.name}: fast hook auth early return disabled")
            return content
    return content


def patch_fast_models(path: Path, content: str, report: PatchReport) -> str:
    for old, new in FAST_MODELS_PATTERNS:
        content, changed = replace_first(content, old, new)
        if changed:
            report.add_patch(f"{path.name}: model fast-tier check -> true")
            return content

    if "modelsByType.models.some" in content or ".models.some(" in content:
        report.warn(f"{path.name}: fast model pattern changed; inspect manually")
    return content


def patch_vscode_fast_mode(paths: AppPaths, report: PatchReport) -> None:
    files = sorted(paths.assets_dir.glob("read-service-tier-for-request-*.js"))
    files.extend(sorted(paths.assets_dir.glob("use-service-tier-settings-*.js")))
    if not files:
        report.warn("No fast_mode bundles found; inspect VS Code extension assets manually")
        return

    for path in files:
        content = read_text(path)
        original = content

        content = patch_vscode_fast_service_tier(path, content, report)
        content = patch_fast_auth(path, content, report)

        if content != original:
            write_text(path, content)
            report.add_file(path)


def patch_vscode_fast_service_tier(path: Path, content: str, report: PatchReport) -> str:
    def replacement(match: re.Match[str]) -> str:
        loading_name = match.group("loading").split("=", 1)[0]
        return f"{match.group('loading')},{match.group('allowed')}=!{loading_name}"

    content, count = VSCODE_SERVICE_TIER_GATE_RE.subn(replacement, content, count=1)
    if count > 0:
        report.add_patch(f"{path.name}: VS Code service-tier gate -> allowed")
    return content


def find_likely_fast_file(paths: AppPaths, report: PatchReport) -> None:
    for path in sorted(paths.assets_dir.glob("*.js")):
        content = read_text(path)
        if "authMethod" in content and "fast_mode" in content:
            report.warn(f"Likely fast-mode bundle: {path.name}")
            return


def patch_plugin_sidebar(paths: AppPaths, report: PatchReport) -> None:
    for path in sorted(paths.assets_dir.glob("index-*.js")):
        content = read_text(path)
        original = content
        marker = "pluginsDisabledTooltip"

        if marker in content:
            idx = content.find(marker)
            window = content[max(0, idx - 240) : idx + 120]
            match = SIDEBAR_GATE_RE.search(window)
            if match:
                content = replace_sidebar_gate(path, content, match.group(1), report)
            else:
                report.warn(f"{path.name}: plugins sidebar gate pattern changed")

        if content != original:
            write_text(path, content)
            report.add_file(path)


def replace_sidebar_gate(path: Path, content: str, gate: str, report: PatchReport) -> str:
    old = f"{gate}?(0,$.jsx)(Sl,{{tooltipContent"
    new = "0?(0,$.jsx)(Sl,{tooltipContent"
    content, changed = replace_first(content, old, new)
    if changed:
        report.add_patch(f"{path.name}: plugins sidebar gate {gate}? -> 0?")
    return content


def patch_apikey_gate(paths: AppPaths, report: PatchReport) -> None:
    files = sorted(paths.assets_dir.glob("gradient-*.js"))
    patched_any = False
    for path in files:
        patched_any = patch_apikey_gate_file(path, report) or patched_any

    if not patched_any:
        fallback_files = [
            path
            for path in sorted(paths.assets_dir.glob("*.js"))
            if path not in files and APIKEY_GATE_FALLBACK_RE.search(read_text(path))
        ]
        for path in fallback_files:
            patched_any = patch_apikey_gate_file(path, report) or patched_any

    if not patched_any:
        if files:
            report.warn("Known apikey gate patterns not found; inspect gradient and plugin auth bundles manually")
        else:
            report.warn("gradient-*.js not found; search for return e===`apikey` or return e!==`chatgpt` manually")


def patch_apikey_gate_file(path: Path, report: PatchReport) -> bool:
    content = read_text(path)
    original = content

    for pattern in APIKEY_GATE_PATTERNS:
        content, changed = replace_first(content, pattern, "function e(e){return false}")
        if changed:
            report.add_patch(f"{path.name}: apikey gate -> return false")
            break
    else:
        match = APIKEY_GATE_FALLBACK_RE.search(content)
        if match:
            fn = match.group("fn")
            arg = match.group("arg")
            replacement = f"function {fn}({arg}){{return false}}"
            content = content[: match.start()] + replacement + content[match.end() :]
            report.add_patch(
                f"{path.name}: apikey gate (regex fallback) -> return false"
            )
        elif "apikey" in content or "chatgpt" in content:
            report.warn(
                f"{path.name}: known gate patterns not found; inspect manually"
            )

    if content == original:
        return False
    write_text(path, content)
    report.add_file(path)
    return True


def patch_connector_gate(paths: AppPaths, report: PatchReport) -> None:
    files = sorted(paths.assets_dir.glob("use-plugin-install-flow-*.js"))
    files.extend(sorted(paths.assets_dir.glob("check-plugin-availability-*.js")))
    fallback_files = [
        path
        for path in sorted(paths.assets_dir.glob("*.js"))
        if path not in files and "connector-unavailable" in read_text(path)
    ]
    files.extend(fallback_files)
    for path in files:
        content = read_text(path)
        original = content

        for old, new in CONNECTOR_PATTERNS:
            if old in content and f"false&&{old}" not in content:
                idx = content.find(old)
                if "false&&" not in content[max(0, idx - 20) : idx]:
                    content = content.replace(old, new, 1)
                    report.add_patch(f"{path.name}: connector unavailable gate disabled")
                    break

        if content != original:
            write_text(path, content)
            report.add_file(path)


def patch_vscode_rename(paths: AppPaths, report: PatchReport) -> None:
    if not getattr(paths, "is_vscode_extension", False):
        return
    patch_vscode_rename_package(paths, report)
    patch_vscode_rename_extension_js(paths, report)


def patch_vscode_rename_package(paths: AppPaths, report: PatchReport) -> None:
    package_path = paths.extracted_app_dir / "package.json"
    if not package_path.exists():
        report.warn("VS Code package.json not found; rename command contribution was not patched")
        return

    package_json = json.loads(read_text(package_path))
    contributes = package_json.setdefault("contributes", {})
    commands = contributes.setdefault("commands", [])
    for command in commands:
        if command.get("command") == VSCODE_RENAME_COMMAND["command"]:
            if command != VSCODE_RENAME_COMMAND:
                command.clear()
                command.update(VSCODE_RENAME_COMMAND)
                package_path.write_text(json.dumps(package_json, indent=2) + "\n", encoding="utf-8")
                refresh_vscode_extensions_index(paths, report)
                report.add_file(package_path)
                report.add_patch(f"{package_path.name}: VS Code rename command contribution updated")
            else:
                refresh_vscode_extensions_index(paths, report)
            return

    commands.append(VSCODE_RENAME_COMMAND)
    package_path.write_text(json.dumps(package_json, indent=2) + "\n", encoding="utf-8")
    refresh_vscode_extensions_index(paths, report)
    report.add_file(package_path)
    report.add_patch(f"{package_path.name}: VS Code rename command contribution added")


def refresh_vscode_extensions_index(paths: AppPaths, report: PatchReport) -> None:
    extension_dir = getattr(paths, "extension_dir", None)
    if extension_dir is None:
        return

    extensions_index = extension_dir.parent / "extensions.json"
    if not extensions_index.exists():
        return

    extensions_index.touch()
    print(f"[OK] Refreshed VS Code extension manifest cache input -> {extensions_index}")


def patch_vscode_rename_extension_js(paths: AppPaths, report: PatchReport) -> None:
    extension_js = paths.extracted_app_dir / "out" / "extension.js"
    if not extension_js.exists():
        report.warn("VS Code out/extension.js not found; rename command handler was not patched")
        return

    content = read_text(extension_js)
    original = content

    content = patch_vscode_rename_webview_bridge(extension_js, content, report)
    content = patch_vscode_rename_command_handler(extension_js, content, report)

    if (
        content == original
        and "triggerRenameThreadViaWebview" not in content
        and "triggerNewChatViaWebview" not in content
    ):
        report.warn(f"{extension_js.name}: rename webview entrypoints not found; inspect manually")

    if content != original:
        write_text(extension_js, content)
        report.add_file(extension_js)


def patch_vscode_rename_webview_bridge(path: Path, content: str, report: PatchReport) -> str:
    if "triggerRenameThreadViaWebview" in content:
        return content

    new_chat_method = (
        'triggerNewChatViaWebview(){this.sidebarView&&this.sidebarWebviewReady&&'
        'this.postMessageToWebview(this.sidebarView.webview,{type:"new-chat"})}'
    )
    rename_method = (
        'triggerRenameThreadViaWebview(){this.sidebarView&&this.sidebarWebviewReady&&'
        'this.postMessageToWebview(this.sidebarView.webview,{type:"rename-thread"})}'
    )
    content, changed = replace_first(content, new_chat_method, new_chat_method + rename_method)
    if changed:
        report.add_patch(f"{path.name}: VS Code rename-thread host message bridge added")
    elif "triggerNewChatViaWebview" in content:
        report.warn(f"{path.name}: rename webview bridge pattern changed; inspect manually")
    return content


def patch_vscode_rename_command_handler(path: Path, content: str, report: PatchReport) -> str:
    content, updated_count = re.subn(
        r'\.triggerRunCommandViaWebview\("renameThread"\)',
        ".triggerRenameThreadViaWebview()",
        content,
    )
    if updated_count > 0:
        report.add_patch(f"{path.name}: VS Code rename command handler updated")
        return content

    if '"chatgpt.renameThread"' in content:
        return content

    def replacement(match: re.Match[str]) -> str:
        return (
            f"{match.group('existing')},"
            f'{match.group("existing").split(".push", 1)[0]}.push('
            f'{match.group("vscode")}.commands.registerCommand("chatgpt.renameThread",async()=>'
            f'{{await {match.group("focus")}(),'
            f'{match.group("provider")}.triggerRenameThreadViaWebview()}})),'
            f"{match.group('tail')}"
        )

    content, count = VSCODE_NEW_CHAT_COMMAND_RE.subn(replacement, content, count=1)
    if count > 0:
        report.add_patch(f"{path.name}: VS Code rename command handler registered")
    elif "triggerNewChatViaWebview" in content:
        report.warn(f"{path.name}: rename command registration pattern changed; inspect manually")
    return content


def patch_js(paths: AppPaths, *, include_fast_plugins: bool = True, include_zed_remote: bool = False) -> PatchReport:
    if not paths.assets_dir.exists():
        raise SystemExit(f"Assets directory not found after extraction: {paths.assets_dir}")

    report = PatchReport()
    if include_fast_plugins:
        patch_fast_mode(paths, report)
        patch_plugin_sidebar(paths, report)
        patch_apikey_gate(paths, report)
        patch_connector_gate(paths, report)
        patch_chrome_plugin_preservation(
            paths,
            report,
            include_desktop_surfaces=not getattr(paths, "is_vscode_extension", False),
        )
        patch_vscode_rename(paths, report)
    if include_zed_remote:
        patch_zed_remote_open(paths, report)

    if report.patch_actions == 0:
        raise SystemExit(
            "No patches were applied. Codex bundle patterns likely changed. "
            "See README troubleshooting commands."
        )
    return report
