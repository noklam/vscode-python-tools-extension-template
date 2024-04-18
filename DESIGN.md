- [Overview](#overview)
  - [Backend LSP Server](#backend-lsp-server)
  - [Client](#client)
    - [User environment](#user-environment)
- [todo](#todo)
      - [Python Interpreter](#python-interpreter)
    - [Logging](#logging)
    - [Workspace settings](#workspace-settings)


# Overview

This extension includes two components:
- [Overview](#overview)
  - [Backend LSP Server](#backend-lsp-server)
  - [Client](#client)
    - [User environment](#user-environment)
- [todo](#todo)
      - [Python Interpreter](#python-interpreter)
    - [Logging](#logging)
    - [Workspace settings](#workspace-settings)

## Backend LSP Server
The LSP server is implemented in Python and the code is in `bundled`.

The most important logic is in `lsp_server.py`, the rest of the file is boilerplate code to allow the LSP communicate with the client properly (not the interesting bit).

## Client
The client is implemented in `src`, and the most important part is in `extension.ts`.

1. `extension.ts`: This is the main entry point for a VS Code extension. It typically contains the activate and deactivate functions that VS Code calls when the extension is activated or deactivated

2. `package.json`

The extension bundles a few Python libraries such as `pygls` (Python Language Server Protocol implementation). It also relies on `vscode/ms-python` extension to select the proper Python intepreter.

### User environment
The extension requires loading a Kedro project instead of just parsing configurations for few reasons:
- Project coded is needed to resolve `$resolver` properly
- The actual pipeline code is needed in order to build the lineage between `parameters.yml` and `pipeline.py`. Alternative is to hard code this but it will be very fragile.
- To support project settings defined in `settings.py`
- To support environment resolution. i.e. user can have `base`, `local`, `prod`. The extension need to know which environment the user is using in order to resolve properly.

todo: On the other hand, it's a heavy requirement for LSP to load a kedro project, this may also trigger connections with hooks etc and it should be avoided.

# todo
- [] Static validation of `catalog.yml` against a defined JSON schema (planning to experiment with the JSON `kedro` provide and move that to `kedro-datasets` so it can supported different version easily)
- [] Support references of configuration -> maybe able to support refactor as well.


#### Python Interpreter
- `PythonExtension.api()` returns the interpreter information from `vscode/ms-python`


### Logging
- `lsp_runner.py` controls the traceback that get sent back into output channel (VS Code)


### Workspace settings
The settings is defined in `package.json` and some extra logic in `src/common/settings.ts`.

For example, user can update the Python interpreter path to have a global settings.

You will find this in `package.json`

```json
                "kedro-lsp.interpreter": {
                    "default": [],
                    "description": "When set to a path to python executable, extension will use that to launch the server and any subprocess.",
                    "scope": "resource",
                    "items": {
                        "type": "string"
                    },
                    "type": "array"
                }
```