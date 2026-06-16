# KidCode Project Format

KidCode v0 projects are folders ending in `.kcproj`.

```text
my_game.kcproj/
  manifest.json
  main.py
  blocks.json
  generated/
    main.generated.py
  assets/
```

`manifest.json` must use schema `kidcode.project.v1` and declare the project id, title, kind, age mode, entry file, canvas, and permissions.

The runtime starts with a 128x128 logical canvas. Generated block code should be written to `generated/main.generated.py` and can be run with:

```bash
kidcode run examples/blocks_demo.kcproj --entry generated/main.generated.py --headless --frames 60
```

PC v0 executes local Python code. Permissions are enforced for KidCode services, but they are not a complete Python sandbox.

Project entry paths must stay inside the `.kcproj` folder. Runtime entry overrides use the same rule.

The repo includes text, blocks, app, and radio stub examples under `examples/`.

Create a new project with:

```bash
kidcode new my_game
```
