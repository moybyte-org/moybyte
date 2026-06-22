import gc
import time

import kidcode
from kidcode.runtime import Runtime


class ProjectRunner:
    def __init__(self, input_state, keyboard, renderer):
        self.input = input_state
        self.keyboard = keyboard
        self.renderer = renderer
        self.runtime = Runtime(self.input)
        kidcode.bind_runtime(self.runtime)
        self.env = None
        self.update = None
        self.draw = None
        self.frame = 0
        self.status_ms = _ticks_ms()
        self.status_frame = 0
        self.running = False
        self._stopped_rendered = False
        self._load_kind = None
        self._project_id = None
        self._source = None
        # Per-frame timing breakdown (ms) for the last frame; read by the shell
        # benchmark loop. kb=keyboard poll, upd=update(), drw=clear+draw+render.
        self.bench = {"kb": 0, "upd": 0, "drw": 0}

    def load_frozen_project(self, module_name):
        kidcode.reset_api()
        kidcode.bind_runtime(self.runtime)
        module = __import__(module_name, None, None, ("setup", "update", "draw"))
        setup = getattr(module, "setup", None)
        self.update = getattr(module, "update", None) or kidcode.game.update_handler
        self.draw = getattr(module, "draw", None) or kidcode.game.draw_handler
        if setup:
            setup()
        self.running = True
        self._stopped_rendered = False
        self._load_kind = "module"
        self._project_id = module_name
        self._source = None
        self.renderer.set_status("loaded " + module_name)

    def load_source(self, project_name, source):
        kidcode.reset_api()
        kidcode.bind_runtime(self.runtime)
        env = _make_source_env(project_name)
        source = _normalize_source(source)
        print("KidCode source exec starting", project_name)
        exec(source, env)
        print("KidCode source exec done", project_name)
        setup = env.get("setup")
        self.update = env.get("update") or kidcode.game.update_handler
        self.draw = env.get("draw") or kidcode.game.draw_handler
        if setup:
            print("KidCode source setup starting", project_name)
            setup()
            print("KidCode source setup done", project_name)
        self.env = env
        self.running = True
        self._stopped_rendered = False
        self._load_kind = "source"
        self._project_id = project_name
        self._source = source
        self.renderer.set_status("loaded " + project_name)
        print("KidCode source loaded", project_name)

    def try_load_source(self, project_name, source):
        try:
            self.load_source(project_name, source)
            return True
        except SyntaxError as exc:
            line = getattr(exc, "lineno", "?")
            print("KidCode friendly syntax error line", line, str(exc))
            self.renderer.set_status("syntax error line " + str(line))
        except Exception as exc:
            print("KidCode friendly project error:", exc)
            self.renderer.set_status("project error " + str(exc)[:16])
        self.cleanup()
        return False

    def try_load_file(self, paths):
        for path in paths:
            try:
                with open(path, "r") as handle:
                    source = handle.read()
            except OSError:
                continue
            print("KidCode loading project file", path)
            if self.try_load_source(path, source):
                return path
        return None

    def run_restart_cycles(self, module_name, count):
        before = _free_mem()
        for _index in range(count):
            self.cleanup()
            self.load_frozen_project(module_name)
        after = _free_mem()
        print("KidCode restart cycles", count, "free_before", before, "free_after", after)
        return before, after

    def step(self, dt):
        bench = self.bench
        _t = _ticks_ms()
        self.keyboard.poll()
        self.input.begin_frame()
        self._handle_shell_controls()
        bench["kb"] = _ticks_diff(_ticks_ms(), _t)
        if not self.running:
            self._render_stopped()
            return
        self._apply_demo_input_if_needed()
        _t = _ticks_ms()
        if self.update:
            self.update(dt)
        bench["upd"] = _ticks_diff(_ticks_ms(), _t)
        _t = _ticks_ms()
        self.runtime.canvas.clear(0)
        if self.draw:
            self.draw()
        self.renderer.render(self.runtime.canvas.commands)
        bench["drw"] = _ticks_diff(_ticks_ms(), _t)
        self.frame += 1
        now_ms = _ticks_ms()
        elapsed_ms = _ticks_diff(now_ms, self.status_ms)
        if elapsed_ms >= 1000:
            fps = ((self.frame - self.status_frame) * 1000) // elapsed_ms
            self.status_ms = now_ms
            self.status_frame = self.frame
            mask = self._held_mask()
            self.renderer.set_status(
                "fps=%d k=%02x r=%d m=%02x"
                % (fps, self.input.last_key, self.keyboard.raw_mode, mask)
            )

    def cleanup(self):
        self.runtime.canvas.clear(0)
        gc.collect()

    def reload(self):
        if self._load_kind == "module" and self._project_id:
            self.cleanup()
            self.load_frozen_project(self._project_id)
            return True
        if self._load_kind == "source" and self._project_id and self._source is not None:
            self.cleanup()
            self.load_source(self._project_id, self._source)
            return True
        return False

    def stop(self):
        self.running = False
        self._stopped_rendered = False
        self.cleanup()

    def _handle_shell_controls(self):
        if self.input.pressed("home") or self.input.pressed("stop"):
            self.stop()
            return
        if self.input.pressed("run") and not self.running:
            if not self.reload():
                self.running = True
            self._stopped_rendered = False

    def _render_stopped(self):
        if self._stopped_rendered:
            return
        self.renderer.render_message(
            "stopped",
            (
                "Tiny Runner stopped",
                "Run reloads",
                "Home stays here",
            ),
        )
        self._stopped_rendered = True

    def _apply_demo_input_if_needed(self):
        if self.keyboard.available:
            return
        phase = (self.frame // 30) % 4
        if phase == 0:
            self.input.set_button("right", True)
        elif phase == 1:
            self.input.set_button("down", True)
        elif phase == 2:
            self.input.set_button("left", True)
        else:
            self.input.set_button("up", True)

    def _held_mask(self):
        mask = 0
        if self.input.held("left"):
            mask |= 1
        if self.input.held("right"):
            mask |= 2
        if self.input.held("up"):
            mask |= 4
        if self.input.held("down"):
            mask |= 8
        if self.input.held("a"):
            mask |= 16
        if self.input.held("b"):
            mask |= 32
        return mask


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(end_ms, start_ms):
    try:
        return time.ticks_diff(end_ms, start_ms)
    except AttributeError:
        return end_ms - start_ms


def _free_mem():
    try:
        gc.collect()
        return gc.mem_free()
    except AttributeError:
        return -1


def _make_source_env(project_name):
    env = {"__name__": project_name}
    for name in getattr(kidcode, "__all__", ()):
        if name.startswith("_"):
            continue
        env[name] = getattr(kidcode, name)
    return env


def _normalize_source(source):
    normalized = []
    for line in source.splitlines():
        if line.strip() == "from kidcode import *":
            normalized.append("")
        else:
            normalized.append(line)
    if source.endswith("\n"):
        return "\n".join(normalized) + "\n"
    return "\n".join(normalized)
