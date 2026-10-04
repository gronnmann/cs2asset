# cs2asset implementation plan

Build a Windows-first Python CLI that imports Poly Haven textures, static models, and HDRIs into a selected CS2 Hammer addon. One command should download the required files, convert them, generate Source 2 source assets, invoke Valve's compiler, and install the complete result into the project. Use uv for packaging and execution, and Rich for terminal output.

This document is a plan, not an implemented or validated importer. Research and local discovery were performed on 2026-10-04.

**1. User workflow**

Proposed commands:

```powershell
uv run cs2asset init
uv run cs2asset search "mossy rock" --type models
uv run cs2asset info polyhaven:concrete_floor_01
uv run cs2asset import polyhaven:concrete_floor_01 --resolution 2k
uv run cs2asset import polyhaven:dirty_football --resolution 2k
uv run cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
uv run cs2asset list
uv run cs2asset rebuild polyhaven:dirty_football
uv run cs2asset doctor
```

`init` discovers CS2, Valve's tools, Blender, and available addons; shows a numbered Rich project selector; and remembers the choice. An import without prior setup runs the same selector. Also accept a Poly Haven asset URL. Infer asset type from the API rather than requiring separate import commands.

Display download size, selected resolution, current phase, progress, and final Hammer resource paths. Default to 2K texture maps and a 4K HDRI panorama; distinguish panorama resolution from any generated cubemap face size. An explicit unavailable resolution produces an actionable error listing available choices.

Support `projects list`, `projects use <name>`, and `--project <name-or-path>` for switching projects. Add `--dry-run`, `--verbose`, and `--json` for inspectability and scripting. Noninteractive use must specify or already have a selected project; never wait on an invisible prompt.

Success means assets are in the addon's content and compiled game directories, ready for Hammer's asset browser. Applying a material, placing a prop, or assigning a sky material to `env_sky` remains a normal mapping action. V1 does not rewrite the user's map. Verify whether an already-open Hammer session discovers imports immediately or requires an asset-browser refresh.

Package a console entry point using `[project.scripts]` and a build backend, with a committed `uv.lock`. Development uses `uv run cs2asset`; distribution can use `uvx --from <package-or-repository> cs2asset`, and eventually `uvx cs2asset` if published under that name. [uv project configuration](https://docs.astral.sh/uv/concepts/projects/config/)

**2. Technology choices**

| Component | Choice and purpose |
| --- | --- |
| Python and packaging | Python 3.12 baseline, uv, `uv_build`, `src/` package layout; validate supported versions against image-library wheels. |
| CLI | Typer for commands and argument validation; Rich for tables, prompts, progress, and diagnostic summaries. |
| Network | HTTPX for API calls and streamed downloads, with explicit timeouts and bounded retries. |
| Image processing | OpenImageIO plus NumPy for HDR/EXR, channel operations, color handling, and projection if needed. Verify Windows wheel availability before locking the runtime. |
| Model conversion | An installed, supported Blender version run headlessly as a subprocess, exporting FBX for the Source 2 pipeline. |
| Source 2 compilation | The user's CS2 `resourcecompiler.exe`; Valve remains responsible for generating compiled resources. |
| Supporting libraries | `platformdirs` for user data/cache paths and a maintained Valve KeyValues parser for Steam manifests. |
| Development checks | pytest and Ruff; recorded API fixtures and opt-in integration tests with the real Valve tools. |

Typer, Rich, HTTPX, Blender, and OpenImageIO are open-source components. Valve's compiler is an external prerequisite supplied by CS2 Workshop Tools. [Typer](https://typer.tiangolo.com/), [Rich](https://rich.readthedocs.io/en/latest/introduction.html), [HTTPX](https://www.python-httpx.org/), [OpenImageIO Python bindings](https://openimageio.readthedocs.io/en/latest/pythonbindings.html)

Keep Blender outside the uv environment and communicate through a JSON job file and result file. Detect its executable in PATH, standard installations, and Steam libraries; allow an explicit override. Do not require Blender for ordinary texture imports. If suitable OpenImageIO wheels are unavailable, choose a tested Blender-based image worker before shipping, rather than requiring users to compile native libraries.

**3. First milestone: prove the three Valve pipelines**

Before building the full CLI, create a disposable validation addon and prove one minimal material, static model, and HDR sky compile and render correctly. This resolves the highest-risk compatibility questions early.

Local discovery already confirmed:

- Steam's registry entry and `libraryfolders.vdf` locate the CS2 installation through `appmanifest_730.acf`.
- CS2 is installed under `C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive`.
- `game\bin\win64\resourcecompiler.exe` exists and its `-help` runs successfully. It documents `-i`, `-game`, `-nop4`, `-filelist`, and incremental dependency checks.
- Matching `content\csgo_addons` and `game\csgo_addons` directories exist, with projects including `de_campsite`, `de_frostlodge`, and `forest_1v1`.
- Blender executables exist in the standard Blender 4.3 and 5.1 installation directories, although Blender was not on PATH.
- A shipped Valve `.vmdl` example uses the `modeldoc36` text format. Treat that as evidence for this installation, not a permanent schema guarantee.

Validate the following before finalizing templates:

| Question | Required proof |
| --- | --- |
| Material contract | Correct CS2 shader, texture parameter names, color spaces, roughness handling, normal-map orientation, and opacity behavior. |
| Sky contract | The installed sky shader's accepted HDR input layout and texture settings; determine whether a panorama can be used directly or must become a cubemap. |
| Model contract | Minimal ModelDoc structure, accepted FBX output, material remapping, axes, scale, and collision generation. |
| Compiler context | Exact game/addon arguments, dependency search paths, and output mapping from `content` to `game`. |

Use installed Valve examples and resources, tool help, and the official editors to establish valid source templates. Store minimal project-owned templates and their tested CS2 build information. Do not implement binary resource formats or reverse-engineer compilation.

Compiler invocation will have the shape `resourcecompiler.exe -nop4 -game <validated-game-context> -i <source-file>`. The precise game context is a result of this milestone, not an assumption. Public Valve wiki pages returned HTTP 403 during planning, so the local tools and actual compilation tests are particularly important.

Completion criterion: all three sample assets load in Hammer and a small compiled test map, with no missing textures, incorrect orientation, or lost HDR range. Compiler help has been tested; these asset compilation tests have not yet been performed.

**4. Project discovery and configuration**

Read Steam locations from the Windows registry, then parse every library and find app 730 using its manifest's installation directory. Validate the executable and content/game roots; avoid depending on a fixed installation folder name.

Discover addon candidates from the content and game roots, distinguish shipped templates from user projects, and require an explicit initial selection. Validate the selected project and write access. Save machine-specific paths in user configuration and portable import settings in project metadata. Configuration precedence: command-line override, project configuration, user configuration, automatic discovery.

`doctor` reports the selected addon, compiler and Blender paths, versions, write access, and supported conversion capabilities. If Workshop Tools are missing, provide installation instructions. If Blender is missing, allow texture/sky operations that do not require it and explain how to configure it for models.

**5. Poly Haven provider and download cache**

Implement a small provider interface: `search`, `get_asset`, and `resolve_files`. Keep Poly Haven response parsing separate from conversion so later providers and local files can reuse the same pipeline.

Use the current `/search`, `/assets`, `/info/{id}`, and `/files/{id}` endpoints. Prefer API search with cached-catalog keyword search as an offline fallback. Normalize results into asset records containing type, identity, available variants, dimensions, authors, and dependencies. Resolve download URLs from file metadata; never construct CDN paths from naming guesses.

Send `User-Agent: cs2asset/<version>` and visibly label assets as coming from Poly Haven. The current API needs no key for default endpoints. [Poly Haven API](https://polyhaven.com/pl/our-api), [API schema](https://api.polyhaven.com/api-docs/swagger.json)

Download only the selected variant and its required dependencies. Preserve dependency-relative paths, especially for Blender files. Stream to temporary files, verify provided sizes/checksums, and promote completed files into a shared cache. Use bounded concurrency, retry transient failures with backoff, and honor rate-limit responses. Resume downloads only when the server supports validated range requests.

Validate archive and dependency paths before writing. Record source URLs, hashes, chosen variants, and provenance in the import manifest. Cache metadata with an expiry; allow explicit refresh and rebuilding from already-cached files.

**6. Texture-to-material pipeline**

Select base color, normal, roughness, AO, and metalness when available, plus opacity where relevant. Prefer separate lossless maps. Read packed maps only with a known channel layout.

Generate `.vmat` files from the validated CS2 shader template, plus source images and `.vtex` descriptors where that pipeline requires them. Keep color textures in the correct color space and data maps linear. Validate the target normal convention with a directional test; apply a green-channel conversion only when required. Map AO and roughness according to the actual shader contract.

Provide defaults for genuinely absent optional maps, record those decisions, and preserve physical texture dimensions when available. Expose tiling and surface-property overrides. Start with opaque PBR and alpha-tested materials; add blended transparency only for a validated shader path. Retain height maps in the cache, with displacement/parallax deferred until a suitable CS2 material path is tested.

Completion criterion: imported materials work on Hammer geometry and imported models, with visibly correct normals, roughness, scale, and supported opacity.

**7. HDRI-to-skybox pipeline**

Download the actual HDR/EXR panorama and retain floating-point lighting values. Do not use a tonemapped preview for the sky source. Feed the validated sky texture layout directly or convert to the required cubemap layout using deterministic projection.

Generate the sky material and required texture sources/descriptors. Offer yaw rotation and exposure adjustments, defaulting to an unchanged source. Validate poles, horizon alignment, seams, face orientation, and values above 1.0. Compile through Valve's tools and install under the addon's skybox material namespace.

The output is a selectable sky material. A photographic HDRI does not by itself configure the map's sun entity or guarantee matching baked illumination; keep those map-lighting decisions explicit. Indoor HDRIs remain importable but should be identified as such in asset information.

Completion criterion: a Poly Haven HDRI is selectable for `env_sky` and renders correctly in the test map.

**8. Model pipeline**

Use an available model format and all its dependencies from the API, preferring a Blender scene for material inspection and conversion. In a clean background Blender process, disable source-file script auto-execution and run only the bundled conversion script. Blender supports background execution. [Blender command-line documentation](https://docs.blender.org/manual/en/3.0/advanced/command_line/arguments.html)

Select intended render meshes; apply evaluated modifiers and transforms; handle instances; preserve UVs, material slots, and split normals; and export FBX with tested axes and units. Validate real-world dimensions against metadata when present, and offer a scale override. Avoid silent automatic resizing when metadata is missing.

Route each material through the shared material pipeline. Support conventional image-based PBR materials first. Report unsupported procedural nodes clearly; do not silently substitute broken materials. Preserve supplied LODs where compatible; offer optional reduction later.

Generate a `.vmdl` with material remaps and an appropriate static-prop collision policy. Start with a tested simple convex-hull option and an explicit no-collision option, showing which was selected. Complex concave collision, animated rigs, and physics props are later extensions. A single convex hull may be unsuitable for hollow furniture or architecture and must not be presented as exact collision.

Completion criterion: multi-material static props can be placed in Hammer, compile with their dependencies, and have correct size, orientation, shading, and the selected collision behavior.

**9. Compilation, installation, and repeatability**

Use a dedicated namespace, for example:

```text
content/csgo_addons/<project>/
  materials/cs2asset/polyhaven/<asset>/<variant>/...
  materials/skybox/cs2asset/polyhaven/<asset>/<variant>/...
  models/cs2asset/polyhaven/<asset>/<variant>/...
  .cs2asset/imports.json

game/csgo_addons/<project>/
  materials/...   # compiled .vmat_c and .vtex_c resources
  models/...      # compiled .vmdl_c and associated resources
```

Resolve all references relative to the addon, with stable normalized resource names. Include a settings/variant identity so different resolutions or sky adjustments can coexist.

Stage conversion and compilation in a tool-owned addon with the same relative resource paths. Validate this staging strategy in milestone 1. Capture the complete compiled dependency/output set, then publish into the selected project with a per-project lock, a transaction journal, and backups for rollback. If the installed compiler requires final-location compilation, retain the same rollback guarantees around those writes.

Pass subprocess arguments as an argument list, stream useful progress, retain full logs, enforce cancellation/timeouts, and check exit status plus expected fresh outputs. Existing stale compiled files must never make a failed import appear successful.

Record inputs, hashes, options, converter/template versions, compiler build, outputs, and provenance. Repeated unchanged imports should reuse downloads and conversions and let Valve check compilation dependencies. Rebuild when inputs or relevant tool versions change. Only replace tool-owned, unchanged files automatically; detect user edits and require an explicit overwrite choice or install another variant.

**10. Implementation sequence and acceptance**

| Milestone | Deliverable and acceptance condition |
| --- | --- |
| A: feasibility | The three minimal Valve pipelines compile and render in a disposable addon; template contracts and compiler context are recorded. |
| B: foundation | Installable uv CLI, Rich interface, discovery, project selection, configuration, and useful `doctor` output. |
| C: first end-to-end import | Poly Haven search/download/cache plus texture conversion, compilation, and transactional installation into a selected addon. |
| D: HDR skies | Full HDRI conversion and sky-material installation, with orientation and HDR tests. |
| E: static models | Blender conversion, shared materials, ModelDoc generation, collision options, and compiled dependency installation. |
| F: release quality | Repeatable rebuilds, interrupted-job recovery, clear error messages, packaging, documentation, and a full real-tool regression pass. |

Test discovery across multiple Steam libraries, Windows paths with spaces/non-ASCII characters, API variant selection, missing maps, checksums, dependency paths, color/normal conversions, model scale, sky orientation, stale outputs, and rollback after partial publication. Use recorded API fixtures for routine tests; avoid depending on live downloads in every test run.

Maintain a small representative integration set: opaque PBR material, alpha-tested material/model, multi-material static prop, and HDR sky. Automated tests check data and compiler results; a Hammer/test-map pass verifies appearance and collision. Mocked compilation alone does not establish success.

The first release is complete when a user can select a project once and import each of the three asset types with a single command, with no manual file movement or editor-based conversion. Future stages can add local FBX/OBJ/glTF and image-set import, more providers, material baking, richer collision/LOD generation, and batch manifests using the same conversion and installation pipeline.
