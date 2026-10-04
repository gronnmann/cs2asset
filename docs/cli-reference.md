# CLI reference

Generated from the CLI with `uv run python scripts/document_cli.py`.

Run commands as `cs2asset ...` or `uvx cs2asset ...`. Global options precede the command.

## `cs2asset`

Convert local and provider assets into CS2 Hammer resources.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--project` | str | `None` | Addon name or its content/game path. |
| `--cs2-root` | path | `None` | Override detected CS2 installation. |
| `--blender` | path | `None` | Override Blender executable. |
| `--json` | boolean | `False` | Machine-readable output. |
| `--verbose / --no-verbose` | boolean | `False` | Show compiler output and diagnostic details. |
| `--version` | boolean | `False` |  |
| `--install-completion` | boolean | `None` | Install completion for the current shell. |
| `--show-completion` | boolean | `None` | Show completion for the current shell, to copy it or customize the installation. |

## `cs2asset blend`

Create experimental paintable environment blends.

## `cs2asset blend create`

Compile a two-layer blend; interactive Hammer painting remains unverified.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `a` | str | `required` |  |
| `b` | str | `required` |  |
| `--name` | str | `required` | Name for the two-layer material. |
| `--project` | str | `None` |  |
| `--resolution` | str | `None` |  |
| `--normal-format` | str | `None` |  |
| `--surface` | str | `None` |  |
| `--dry-run / --no-dry-run` | boolean | `False` |  |
| `--refresh / --no-refresh` | boolean | `False` |  |
| `--overwrite / --no-overwrite` | boolean | `False` |  |
| `--force / --no-force` | boolean | `False` |  |
| `--offline / --no-offline` | boolean | `False` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset config`

Inspect and set import defaults.

## `cs2asset config set`

Set a user default or a portable default for a specific addon.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `key` | str | `required` |  |
| `value` | str | `required` |  |
| `--project` | str | `None` |  |

## `cs2asset config show`



| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--project` | str | `None` |  |

## `cs2asset context-menu`

Manage the Windows Explorer import entry.

## `cs2asset context-menu add`

Register supported files for the current user (Windows 11: Show more options).

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--uv` | path | `None` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset context-menu remove`

Remove only the cs2asset Explorer entries.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset context-menu status`

Inspect per-user Explorer registration.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset doctor`

Check local tools, addon paths, and image-processing capabilities.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--project` | str | `None` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset import`

Resolve, convert, compile, and install a local or provider asset into Hammer.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `asset` | str | `required` |  |
| `--project` | str | `None` |  |
| `--resolution` | str | `None` |  |
| `--scale` | float | `None` |  |
| `--collision` | str | `None` |  |
| `--surface` | str | `None` |  |
| `--tiling` | float | `None` |  |
| `--yaw` | float | `None` |  |
| `--exposure` | float | `None` |  |
| `--auto-exposure / --no-auto-exposure` | boolean | `None` | Reduce sky exposure to fit half-float HDR. |
| `--normal-format` | str | `None` |  |
| `--blender` | path | `None` |  |
| `--dry-run / --no-dry-run` | boolean | `False` |  |
| `--refresh / --no-refresh` | boolean | `False` |  |
| `--overwrite / --no-overwrite` | boolean | `False` |  |
| `--force / --no-force` | boolean | `False` |  |
| `--json / --no-json` | boolean | `False` |  |
| `--offline / --no-offline` | boolean | `False` |  |
| `--object` | str | `None` |  |
| `--collection` | str | `None` |  |
| `--split` | str | `None` |  |
| `--origin` | str | `source` |  |
| `--bake-materials / --no-bake-materials` | boolean | `False` |  |
| `--bake-resolution` | int | `2048` |  |
| `--type` | str | `None` | Require material, model, or sky. |

`--exposure` adjusts sky brightness in stops (−32 to 32); `--yaw` rotates the panorama in degrees.
`--auto-exposure` applies an additional exposure reduction when needed to fit the half-float
maximum of 65,504, preserving highlight ratios. The report records the adjustment and effective
exposure. Small negative radiance values (down to −0.001) are clamped to zero; larger negative
or non-finite values cause an error. Resized HDR intermediates retain full-float precision.

```powershell
uvx cs2asset import https://polyhaven.com/a/valley_of_desolation --auto-exposure
```

## `cs2asset info`

Inspect a local or provider asset and the selected source files.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `asset` | str | `required` |  |
| `--resolution` | str | `None` |  |
| `--refresh / --no-refresh` | boolean | `False` |  |
| `--json / --no-json` | boolean | `False` |  |
| `--offline / --no-offline` | boolean | `False` |  |
| `--type` | str | `None` |  |

## `cs2asset init`

Discover tools and select the addon used by future imports.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--project` | str | `None` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset list`

List assets installed by cs2asset in the selected addon.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--project` | str | `None` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset model-list`

Download/inspect a model and list objects, collections, materials and LODs.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `asset` | str | `required` |  |
| `--blender` | path | `None` |  |
| `--resolution` | str | `None` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset projects`

Select a Hammer addon.

## `cs2asset projects list`

List detected addons, including marked Valve examples.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset projects use`

Remember a selected addon.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | str | `required` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset rebuild`

Rebuild saved variants; --cached-inputs uses verified local snapshots.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `asset` | str | `required` |  |
| `--project` | str | `None` |  |
| `--variant` | str | `None` |  |
| `--overwrite / --no-overwrite` | boolean | `False` |  |
| `--refresh / --no-refresh` | boolean | `False` |  |
| `--force / --no-force` | boolean | `False` |  |
| `--offline / --no-offline` | boolean | `False` |  |
| `--json / --no-json` | boolean | `False` |  |
| `--cached-inputs / --no-cached-inputs` | boolean | `False` |  |

## `cs2asset recover`

Recover an interrupted addon installation from its transaction journal.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `--project` | str | `None` |  |
| `--json / --no-json` | boolean | `False` |  |

## `cs2asset search`

Search provider assets by words and asset type.

| Argument / option | Type | Default | Description |
| --- | --- | --- | --- |
| `query` | str | `` | Words to find in provider assets. |
| `--type` | str | `None` |  |
| `--limit` | int | `20` |  |
| `--refresh / --no-refresh` | boolean | `False` |  |
| `--json / --no-json` | boolean | `False` |  |
| `--offline / --no-offline` | boolean | `False` |  |
| `--provider` | str | `polyhaven` |  |
