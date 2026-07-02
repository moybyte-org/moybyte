# Moybyte Project Format

> **Legacy — `.moyproj` SDK format.** This is the older `.moyproj` project folder
> format. The current console format is **`.moy`** (a folder = `manifest.json` +
> `main.py` + `config.json`, plus optional sprites/tilemap/sounds); its cart API is in
> **[`moy_cart_api.md`](../moy_cart_api.md)**. Kept for the maintained `.moyproj` SDK.

Moybyte v0 projects are folders ending in `.moyproj`.

```text
my_game.moyproj/
  manifest.json
  main.py
  blocks.json
  generated/
    main.generated.py
  assets/
```

`manifest.json` must use schema `moybyte.project.v1` and declare the project id, title, kind, age mode, entry file, canvas, and permissions.

The runtime starts with a 128x128 logical canvas. Generated block code should be written to `generated/main.generated.py` and can be run with:

```bash
moybyte run examples/blocks_demo.moyproj --entry generated/main.generated.py --headless --frames 60
```

PC v0 executes local Python code. Permissions are enforced for Moybyte services, but they are not a complete Python sandbox.

Project entry paths must stay inside the `.moyproj` folder. Runtime entry overrides use the same rule.

The repo includes text, blocks, app, and radio stub examples under `examples/`.

Create a new project with:

```bash
moybyte new my_game
```

Pack a `.kc8` bundle with:

```bash
moybyte pack my_game.moyproj --out my_game.kc8
```

Bundles are zip files with a `moybyte_bundle.json` metadata file and project
files at archive root. Generated files and Python caches are excluded unless
`--include-generated` is used.
