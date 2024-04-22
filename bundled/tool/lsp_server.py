# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Implementation of tool support over LSP."""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import sys
import traceback
import logging
from typing import Any, Dict, Optional, Sequence

from kedro.config import OmegaConfigLoader
from common import update_sys_path
from pathlib import Path
# **********************************************************
# Update sys.path before importing any bundled libraries.
# **********************************************************
logger = logging.getLogger(__name__)

# Ensure that we can import LSP libraries, and other bundled libraries.
before_update_path = sys.path.copy()
update_sys_path(
    os.fspath(pathlib.Path(__file__).parent.parent / "libs"),
    os.getenv("LS_IMPORT_STRATEGY", "useBundled"),
)
after_update_path = sys.path.copy()

logger.warn(f"{before_update_path=}")
logger.warn(f"{after_update_path=}")
# **********************************************************
# Imports needed for the language server goes below this.
# **********************************************************
# pylint: disable=wrong-import-position,import-error
import lsp_jsonrpc as jsonrpc
import lsp_utils as utils
import lsprotocol.types as lsp
from pygls import workspace, uris


WORKSPACE_SETTINGS = {}
GLOBAL_SETTINGS = {}
RUNNER = pathlib.Path(__file__).parent / "lsp_runner.py"

MAX_WORKERS = 5


# ******************************************************
# Kedro LSP Server.
# ******************************************************
import re

from lsprotocol.types import (
    TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_DEFINITION,
    WORKSPACE_DID_CHANGE_CONFIGURATION,
    CodeAction,
    CodeActionKind,
    CodeActionOptions,
    CodeActionParams,
    DidChangeConfigurationParams,
    Location,
    Position,
    Range,
    TextDocumentPositionParams,
    TextEdit,
    WorkspaceEdit,
)

from pygls.workspace import Document

"""Kedro Language Server."""

import re
from typing import List, Optional

import yaml
from kedro.framework.project import configure_project
from kedro.framework.session import KedroSession
from kedro.framework.startup import (
    ProjectMetadata,
    _get_project_metadata,
    bootstrap_project,
)
from pygls.server import LanguageServer
from yaml.loader import SafeLoader

# Need to stop kedro.framework.project.LOGGING from changing logging settings, otherwise pygls fails with unknown reason.


print("Checkpoint 1")


class KedroLanguageServer(LanguageServer):
    """Store Kedro-specific information in the language server."""

    project_metadata: Optional[ProjectMetadata] = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def is_kedro_project(self) -> bool:
        """Returns whether the current workspace is a kedro project."""
        return self.project_metadata is not None



    def _set_project_with_workspace(self):
        if self.project_metadata:
            return
        try:
            root_path = pathlib.Path(self.workspace.root_path) # From language server
            # project_metadata = _get_project_metadata(
            #     self.workspace.root_path
            # )  # From the LanguageServer
            project_metadata = bootstrap_project(root_path)
            session = KedroSession.create(root_path)
            context = session.load_context()
            config_loader: OmegaConfigLoader = context.config_loader
            return config_loader
        except RuntimeError:
            project_metadata = None
            context = None
            config_loader =None
        finally:
            self.project_metadata = project_metadata
            self.context = context
            self.config_loader = config_loader



LSP_SERVER = KedroLanguageServer("pygls-kedro-example", "v0.1")
ADDITION = re.compile(r"^\s*(\d+)\s*\+\s*(\d+)\s*=(?=\s*$)") # todo: remove this when mature
RE_START_WORD = re.compile("[A-Za-z_0-9:]*$")
RE_END_WORD = re.compile("^[A-Za-z_0-9:]*")
print("Checkpoint 2")

### Settings
GLOBAL_SETTINGS = {}
WORKSPACE_SETTINGS = {}


@LSP_SERVER.feature(lsp.INITIALIZE)
async def initialize(params: lsp.InitializeParams) -> None:
    log_to_output(f"CWD Server: {os.getcwd()}")
    paths = "\r\n   ".join(sys.path)
    log_to_output(f"sys.path used to run Server:\r\n   {paths}")
    GLOBAL_SETTINGS.update(**params.initialization_options.get("globalSettings", {}))
    settings = params.initialization_options["settings"]
    _update_workspace_settings(settings)

    log_to_output(
        f"Settings used to run Server:\r\n{json.dumps(settings, indent=4, ensure_ascii=False)}\r\n"
    )
    log_to_output(
        f"Global settings:\r\n{json.dumps(GLOBAL_SETTINGS, indent=4, ensure_ascii=False)}\r\n"
    )
    log_to_output(
        f"Workspace settings:\r\n{json.dumps(WORKSPACE_SETTINGS, indent=4, ensure_ascii=False)}\r\n"
    )


def _get_global_defaults():
    return {
        "path": GLOBAL_SETTINGS.get("path", []),
        "interpreter": GLOBAL_SETTINGS.get("interpreter", [sys.executable]),
        "args": GLOBAL_SETTINGS.get("args", []),
        "importStrategy": GLOBAL_SETTINGS.get("importStrategy", "useBundled"),
        "showNotifications": GLOBAL_SETTINGS.get("showNotifications", "off"),
    }


def _update_workspace_settings(settings):
    if not settings:
        key = os.getcwd()
        WORKSPACE_SETTINGS[key] = {
            "cwd": key,
            "workspaceFS": key,
            "workspace": uris.from_fs_path(key),
            **_get_global_defaults(),
        }
        return

    for setting in settings:
        key = uris.to_fs_path(setting["workspace"])
        WORKSPACE_SETTINGS[key] = {
            "cwd": key,
            **setting,
            "workspaceFS": key,
        }


def get_cwd(settings: Dict[str, Any], document: Optional[workspace.Document]) -> str:
    """Returns cwd for the given settings and document."""
    if settings["cwd"] == "${workspaceFolder}":
        return settings["workspaceFS"]

    if settings["cwd"] == "${fileDirname}":
        if document is not None:
            return os.fspath(pathlib.Path(document.path).parent)
        return settings["workspaceFS"]

    return settings["cwd"]


def _check_project():
    """This was a workaround because the server.workspace.root_path is not available at __init__ time.
    Ideally there should be some place to inject this logic after client send back the information.
    For now this function will be triggered for every LSP feature"""
    LSP_SERVER._set_project_with_workspace()


### Kedro LSP logic
def get_conf_paths(project_metadata):
    """
    Get conf paths
    """
    config_loader: OmegaConfigLoader = LSP_SERVER.config_loader
    patterns = config_loader.config_patterns.get("catalog", [])
    base_path = str(Path(config_loader.conf_source) / config_loader.base_env)

    # Extract from OmegaConfigLoader source code
    paths = []
    for pattern in patterns:
        for each in config_loader._fs.glob(Path(f"{str(base_path)}/{pattern}").as_posix()):
            if not config_loader._is_hidden(each):
                paths.append(Path(each))
    paths = set(paths)
    return paths


class SafeLineLoader(SafeLoader):  # pylint: disable=too-many-ancestors
    """A YAML loader that annotates loaded nodes with line number."""

    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        mapping["__line__"] = node.start_mark.line
        return mapping


@LSP_SERVER.feature(WORKSPACE_DID_CHANGE_CONFIGURATION)
def did_change_configuration(
    server: KedroLanguageServer,  # pylint: disable=unused-argument
    params: DidChangeConfigurationParams,  # pylint: disable=unused-argument
) -> None:
    """Implement event for workspace/didChangeConfiguration.
    Currently does nothing, but necessary for pygls.
    """


def _word_at_position(position: Position, document: Document) -> str:
    """Get the word under the cursor returning the start and end positions."""
    if position.line >= len(document.lines):
        return ""
    print("\nCalled _word_at_position")
    line = document.lines[position.line]
    i = position.character
    # Split word in two
    start = line[:i]
    end = line[i:]

    # Take end of start and start of end to find word
    # These are guaranteed to match, even if they match the empty string
    m_start = RE_START_WORD.findall(start)
    m_end = RE_END_WORD.findall(end)

    return m_start[0] + m_end[-1]


def _get_param_location(
    project_metadata: ProjectMetadata, word: str
) -> Optional[Location]:
    print("\nCalled _get_param_location")
    param = word.split("params:")[-1]
    log_to_output(f"Attempt to search `{param}` from parameters file")
    parameters_path = project_metadata.project_path / "conf" / "base" / "parameters.yml"
    # TODO: cache -- we shouldn't have to re-read the file on every request
    parameters_file = open(parameters_path)
    param_line_no = None
    for line_no, line in enumerate(parameters_file, 1):
        if line.startswith(param):
            param_line_no = line_no
            break
    parameters_file.close()

    if param_line_no is None:
        return

    location = Location(
        uri=f"file://{parameters_path}",
        range=Range(
            start=Position(line=param_line_no - 1, character=0),
            end=Position(
                line=param_line_no,
                character=0,
            ),
        ),
    )
    return location


@LSP_SERVER.feature(TEXT_DOCUMENT_DEFINITION)
def definition(
    server: KedroLanguageServer, params: TextDocumentPositionParams
) -> Optional[List[Location]]:
    """Support Goto Definition for a dataset or parameter.
    Currently only support catalog defined in `conf/base`
    """
    _check_project()
    if not server.is_kedro_project():
        return None

    document = server.workspace.get_text_document(params.text_document.uri)
    word = _word_at_position(params.position, document)

    log_to_output(f"Query keyword: {word}")

    if word.startswith("params:"):
        param_location = _get_param_location(server.project_metadata, word)
        if param_location:
            logger.warning(f"{param_location=}")
            return [param_location]

    print("Find Catalog")
    catalog_paths = get_conf_paths(server.project_metadata)
    log_to_output(f"Attempt to search `{word}` from catalog")
    log_to_output(f"{catalog_paths}")
    for catalog_path in catalog_paths:
        log_to_output("{catalog_path=}")
        catalog_conf = yaml.load(catalog_path.read_text(), Loader=SafeLineLoader)

        if word in catalog_conf:
            line = catalog_conf[word]["__line__"]
            location = Location(
                uri=f"file://{catalog_path}",
                range=Range(
                    start=Position(line=line - 1, character=0),
                    end=Position(
                        line=line,
                        character=0,
                    ),
                ),
            )
            logger.warning(f"{location=}")
            return [location]

    return None


### End of Old kedro-lsp


### Start of YAML template action
@LSP_SERVER.feature(
    TEXT_DOCUMENT_CODE_ACTION,
    CodeActionOptions(code_action_kinds=[CodeActionKind.QuickFix]),
)
def code_actions(params: CodeActionParams):
    print("Checkpoint 3: Code Action YAML")
    items = []
    document_uri = params.text_document.uri
    document = LSP_SERVER.workspace.get_text_document(document_uri)

    start_line = params.range.start.line
    end_line = params.range.end.line

    lines = document.lines[start_line : end_line + 1]
    for idx, line in enumerate(lines):
        match = ADDITION.match(line)
        if match is not None:
            range_ = Range(
                start=Position(line=start_line + idx, character=0),
                end=Position(line=start_line + idx, character=len(line) - 1),
            )

            left = int(match.group(1))
            right = int(match.group(2))
            answer = left + right

            text_edit = TextEdit(range=range_, new_text=f"{line.strip()} {answer}!")

            action = CodeAction(
                title=f"Evaluate '{match.group(0)}'",
                kind=CodeActionKind.QuickFix,
                edit=WorkspaceEdit(changes={document_uri: [text_edit]}),
            )
            items.append(action)

    return items


# *****************************************************
# Internal execution APIs.
# *****************************************************
def _run_tool_on_document(
    document: workspace.Document,
    use_stdin: bool = False,
    extra_args: Optional[Sequence[str]] = None,
) -> utils.RunResult | None:
    """Runs tool on the given document.

    if use_stdin is true then contents of the document is passed to the
    tool via stdin.
    """
    if extra_args is None:
        extra_args = []
    if str(document.uri).startswith("vscode-notebook-cell"):
        # TODO: Decide on if you want to skip notebook cells.
        # Skip notebook cells
        return None

    if utils.is_stdlib_file(document.path):
        # TODO: Decide on if you want to skip standard library files.
        # Skip standard library python files.
        return None

    # deep copy here to prevent accidentally updating global settings.
    settings = copy.deepcopy(_get_settings_by_document(document))

    code_workspace = settings["workspaceFS"]
    cwd = settings["cwd"]

    use_path = False
    use_rpc = False
    if settings["path"]:
        # 'path' setting takes priority over everything.
        use_path = True
        argv = settings["path"]
    elif settings["interpreter"] and not utils.is_current_interpreter(
        settings["interpreter"][0]
    ):
        # If there is a different interpreter set use JSON-RPC to the subprocess
        # running under that interpreter.
        argv = [TOOL_MODULE]
        use_rpc = True
    else:
        # if the interpreter is same as the interpreter running this
        # process then run as module.
        argv = [TOOL_MODULE]

    argv += TOOL_ARGS + settings["args"] + extra_args

    if use_stdin:
        # TODO: update these to pass the appropriate arguments to provide document contents
        # to tool via stdin.
        # For example, for pylint args for stdin looks like this:
        #     pylint --from-stdin <path>
        # Here `--from-stdin` path is used by pylint to make decisions on the file contents
        # that are being processed. Like, applying exclusion rules.
        # It should look like this when you pass it:
        #     argv += ["--from-stdin", document.path]
        # Read up on how your tool handles contents via stdin. If stdin is not supported use
        # set use_stdin to False, or provide path, what ever is appropriate for your tool.
        argv += []
    else:
        argv += [document.path]

    if use_path:
        # This mode is used when running executables.
        log_to_output(" ".join(argv))
        log_to_output(f"CWD Server: {cwd}")
        result = utils.run_path(
            argv=argv,
            use_stdin=use_stdin,
            cwd=cwd,
            source=document.source.replace("\r\n", "\n"),
        )
        if result.stderr:
            log_to_output(result.stderr)
    elif use_rpc:
        # This mode is used if the interpreter running this server is different from
        # the interpreter used for running this server.
        log_to_output(" ".join(settings["interpreter"] + ["-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")

        result = jsonrpc.run_over_json_rpc(
            workspace=code_workspace,
            interpreter=settings["interpreter"],
            module=TOOL_MODULE,
            argv=argv,
            use_stdin=use_stdin,
            cwd=cwd,
            source=document.source,
        )
        if result.exception:
            log_error(result.exception)
            result = utils.RunResult(result.stdout, result.stderr)
        elif result.stderr:
            log_to_output(result.stderr)
    else:
        # In this mode the tool is run as a module in the same process as the language server.
        log_to_output(" ".join([sys.executable, "-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")
        # This is needed to preserve sys.path, in cases where the tool modifies
        # sys.path and that might not work for this scenario next time around.
        with utils.substitute_attr(sys, "path", sys.path[:]):
            try:
                # TODO: `utils.run_module` is equivalent to running `python -m <pytool-module>`.
                # If your tool supports a programmatic API then replace the function below
                # with code for your tool. You can also use `utils.run_api` helper, which
                # handles changing working directories, managing io streams, etc.
                # Also update `_run_tool` function and `utils.run_module` in `lsp_runner.py`.
                result = utils.run_module(
                    module=TOOL_MODULE,
                    argv=argv,
                    use_stdin=use_stdin,
                    cwd=cwd,
                    source=document.source,
                )
            except Exception:
                log_error(traceback.format_exc(chain=True))
                raise
        if result.stderr:
            log_to_output(result.stderr)

    log_to_output(f"{document.uri} :\r\n{result.stdout}")
    return result


def _run_tool(extra_args: Sequence[str]) -> utils.RunResult:
    """Runs tool."""
    # deep copy here to prevent accidentally updating global settings.
    settings = copy.deepcopy(_get_settings_by_document(None))

    code_workspace = settings["workspaceFS"]
    cwd = settings["workspaceFS"]

    use_path = False
    use_rpc = False
    if len(settings["path"]) > 0:
        # 'path' setting takes priority over everything.
        use_path = True
        argv = settings["path"]
    elif len(settings["interpreter"]) > 0 and not utils.is_current_interpreter(
        settings["interpreter"][0]
    ):
        # If there is a different interpreter set use JSON-RPC to the subprocess
        # running under that interpreter.
        argv = [TOOL_MODULE]
        use_rpc = True
    else:
        # if the interpreter is same as the interpreter running this
        # process then run as module.
        argv = [TOOL_MODULE]

    argv += extra_args

    if use_path:
        # This mode is used when running executables.
        log_to_output(" ".join(argv))
        log_to_output(f"CWD Server: {cwd}")
        result = utils.run_path(argv=argv, use_stdin=True, cwd=cwd)
        if result.stderr:
            log_to_output(result.stderr)
    elif use_rpc:
        # This mode is used if the interpreter running this server is different from
        # the interpreter used for running this server.
        log_to_output(" ".join(settings["interpreter"] + ["-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")
        result = jsonrpc.run_over_json_rpc(
            workspace=code_workspace,
            interpreter=settings["interpreter"],
            module=TOOL_MODULE,
            argv=argv,
            use_stdin=True,
            cwd=cwd,
        )
        if result.exception:
            log_error(result.exception)
            result = utils.RunResult(result.stdout, result.stderr)
        elif result.stderr:
            log_to_output(result.stderr)
    else:
        # In this mode the tool is run as a module in the same process as the language server.
        log_to_output(" ".join([sys.executable, "-m"] + argv))
        log_to_output(f"CWD Linter: {cwd}")
        # This is needed to preserve sys.path, in cases where the tool modifies
        # sys.path and that might not work for this scenario next time around.
        with utils.substitute_attr(sys, "path", sys.path[:]):
            try:
                # TODO: `utils.run_module` is equivalent to running `python -m <pytool-module>`.
                # If your tool supports a programmatic API then replace the function below
                # with code for your tool. You can also use `utils.run_api` helper, which
                # handles changing working directories, managing io streams, etc.
                # Also update `_run_tool_on_document` function and `utils.run_module` in `lsp_runner.py`.
                result = utils.run_module(
                    module=TOOL_MODULE, argv=argv, use_stdin=True, cwd=cwd
                )
            except Exception:
                log_error(traceback.format_exc(chain=True))
                raise
        if result.stderr:
            log_to_output(result.stderr)

    log_to_output(f"\r\n{result.stdout}\r\n")
    return result


# *****************************************************
# Logging and notification.
# *****************************************************
def log_to_output(
    message: str, msg_type: lsp.MessageType = lsp.MessageType.Log
) -> None:
    LSP_SERVER.show_message_log(message, msg_type)


def log_error(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Error)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onError", "onWarning", "always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Error)


def log_warning(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Warning)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onWarning", "always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Warning)


def log_always(message: str) -> None:
    LSP_SERVER.show_message_log(message, lsp.MessageType.Info)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["always"]:
        LSP_SERVER.show_message(message, lsp.MessageType.Info)


# *****************************************************
# Start the server.
# *****************************************************
if __name__ == "__main__":
    LSP_SERVER.start_io()
