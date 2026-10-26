"""
title: Enhanced Model Icons
description: Enhancements for the model icon in Open WebUI. Adds settings under Settings > Interface to resize the model icon (on the empty-chat screen and beside every model reply) and to choose its shape and crop (circle, rounded, squircle, square, portrait, landscape, wide, hexagon; crop to fill or show the whole image). Published into /static/loader.js through the Shared Assets Protocol, so it coexists with Indicator Changer, Theme Designer Pro and any other plugin using it.
author: fubarpie
author_url: https://github.com/fubarpie/open-webui-enhanced-model-icons
funding_url:
version: 0.1.0
license: MIT
required_open_webui_version: 0.11.0
"""

import asyncio
import hashlib
import json as _json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from open_webui.utils.json_codec import JSONCodec

ASSET_KEY = "enhanced-model-icons"  # fixed, used for asset_register too

# ===========================================================================
# Live reload broadcast
# ---------------------------------------------------------------------------
# Each load publishes over Redis; a peer whose cached code differs from the
# database re-executes it. register(app) is yours and must be idempotent; gate
# each producer on reload_active(app).
# ===========================================================================
RELOAD_STATE_ATTR = "_owui_live_reload"  # {key: {active, exec_id, listener}}
RELOAD_EXEC_ID = uuid.uuid4().hex  # new on every exec of this source
# Open WebUI executes each function as module "function_<id>", or on newer
# builds "function_<id>_<32 hex digits>"; the id is what is left of either.
RELOAD_FUNCTION_ID = __name__.removeprefix("function_")
if (
    len(RELOAD_FUNCTION_ID) > 33
    and RELOAD_FUNCTION_ID[-33] == "_"
    and all(c in "0123456789abcdef" for c in RELOAD_FUNCTION_ID[-32:])
):
    RELOAD_FUNCTION_ID = RELOAD_FUNCTION_ID[:-33]

reload_log = logging.getLogger("owui-live-reload")
# asyncio only keeps weak references to tasks.
reload_tasks: set = set()


def reload_state(app: Any) -> dict:
    registry = getattr(app.state, RELOAD_STATE_ATTR, None)
    if not isinstance(registry, dict):
        registry = {}
        app.state.__setattr__(RELOAD_STATE_ATTR, registry)
    return registry.setdefault(ASSET_KEY, {})


def reload_active(app: Any) -> bool:
    return bool(reload_state(app).get("active"))


def reload_channel() -> str:
    from open_webui.env import REDIS_KEY_PREFIX

    return f"{REDIS_KEY_PREFIX}:live-reload:{ASSET_KEY}"


def reload_mark_loaded(app: Any) -> None:
    register(app)
    state = reload_state(app)
    state["active"] = True
    state["exec_id"] = RELOAD_EXEC_ID


async def reload_publish(app: Any, active: bool) -> None:
    redis = getattr(app.state, "redis", None)
    if redis is None:
        return
    try:
        await redis.publish(reload_channel(), JSONCodec.dumps({"active": active}))
    except Exception as e:
        reload_log.warning("[%s] publish failed: %s", ASSET_KEY, type(e).__name__)


async def reload_ensure_listener(app: Any) -> None:
    state = reload_state(app)
    if state.get("listener") is not None:
        return
    redis = getattr(app.state, "redis", None)
    if redis is None:
        return

    from types import SimpleNamespace

    from open_webui.models.functions import Functions
    from open_webui.utils.plugin import (
        get_function_module_from_cache,
        get_functions_cache,
    )

    # Mark before awaiting so a concurrent event() can't double-subscribe.
    state["listener"] = True
    try:
        pubsub = redis.pubsub()
        await pubsub.subscribe(reload_channel())
    except Exception as e:
        state["listener"] = None
        reload_log.warning("[%s] subscribe failed: %s", ASSET_KEY, type(e).__name__)
        return

    async def listen():
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                if not JSONCodec.loads(message["data"])["active"]:
                    state["active"] = False
                    continue
                was_active = state.get("active")
                # Enable broadcasts arrive before is_active commits; bootstrap trusts this flag.
                state["active"] = True
                context = SimpleNamespace(app=app)
                if not was_active:
                    # Force a fresh exec so its bootstrap registers again.
                    get_functions_cache(context).pop(RELOAD_FUNCTION_ID, None)
                try:
                    function_module, _, _ = await get_function_module_from_cache(
                        context, RELOAD_FUNCTION_ID
                    )
                    # Peers never see a valves save; re-read them like Open WebUI's dispatch does.
                    if hasattr(function_module, "Valves"):
                        valves = await Functions.get_function_valves_by_id(
                            RELOAD_FUNCTION_ID
                        )
                        function_module.valves = function_module.Valves(
                            **(valves or {})
                        )
                except Exception as e:
                    reload_log.warning(
                        "[%s] reload from db failed: %s", ASSET_KEY, type(e).__name__
                    )
        except Exception as e:
            reload_log.warning("[%s] listener stopped: %s", ASSET_KEY, type(e).__name__)

    state["listener"] = asyncio.create_task(listen())


def reload_bootstrap() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def bootstrap():
        try:
            from open_webui.main import app
            from open_webui.models.functions import Functions

            own = await Functions.get_function_by_id(RELOAD_FUNCTION_ID)
            # Loading a disabled function (e.g. for its valves) must not switch it on.
            if own is None or not (own.is_active or reload_active(app)):
                return
            reload_mark_loaded(app)
            await reload_ensure_listener(app)
            await reload_publish(app, True)
        except Exception as e:
            reload_log.warning("[%s] bootstrap failed: %s", ASSET_KEY, type(e).__name__)

    task = loop.create_task(bootstrap())
    reload_tasks.add(task)
    task.add_done_callback(reload_tasks.discard)


async def reload_on_event(
    app: Any, event: Optional[dict], event_name: Optional[str]
) -> None:
    await reload_ensure_listener(app)
    state = reload_state(app)

    is_own = ((event or {}).get("subject") or {}).get("id") == RELOAD_FUNCTION_ID
    if is_own and event_name == "function.disable_started":
        state["active"] = False
        await reload_publish(app, False)
        return
    if is_own and event_name == "function.valves_updated":
        await reload_publish(app, True)

    current = state.get("active") and state.get("exec_id") == RELOAD_EXEC_ID
    if current and event_name != "system.startup.completed":
        return
    reload_mark_loaded(app)
    await reload_publish(app, True)


# =========================== end live reload block =========================


# ===========================================================================
# Shared static-asset registry
# --- KEEP BYTE-IDENTICAL IN EVERY PLUGIN THAT USES IT ----------------------
# ---------------------------------------------------------------------------
# Each plugin publishes a fragment into one app.state registry and the route
# composes them per request, so load order is irrelevant. Per-process: each
# container serves what it has loaded.
#
# Contract:
#   * ASSET_REGISTRY_ATTR, ASSET_ROUTE_ATTR, the entry shape and the paths are
#     the interop surface. The route owner's copy serves everyone.
#   * `key` must be a module-level constant, or a re-exec adds a second entry.
#   * Lower `order` composes first (default 0). Never encode priority in the key.
#   * Producers run on the event loop for every request, 304s included: return
#     a prebuilt string, no I/O, no locks.
#   * Return "" to withdraw. A deleted plugin never sees its own deletion, so
#     disable it first.
#   * Inline fragments only: <script src> / @import lands after hydration.
# ===========================================================================
LOADER_PATH = "/static/loader.js"
CUSTOM_CSS_PATH = "/static/custom.css"
SHARED_ASSET_TYPES = {
    LOADER_PATH: "application/javascript; charset=utf-8",
    CUSTOM_CSS_PATH: "text/css; charset=utf-8",
}
ASSET_REGISTRY_ATTR = "_owui_static_fragments"  # {path: {key: entry}}
ASSET_ROUTE_ATTR = "_owui_shared_asset"  # set to the path the route serves
ASSET_IMPL_ATTR = "_owui_shared_asset_impl"  # implementation version of the route
# Bump on any behaviour change: the newest copy takes over the route.
ASSET_IMPL_VERSION = 4

# Warn once per (path, key, exception type): compose runs on every page load.
_ASSET_WARNED: set = set()


def asset_fragments(app: Any, path: str) -> dict:
    registry = getattr(app.state, ASSET_REGISTRY_ATTR, None)
    if not isinstance(registry, dict):
        registry = {}
        app.state.__setattr__(ASSET_REGISTRY_ATTR, registry)
    bucket = registry.get(path)
    if not isinstance(bucket, dict):
        bucket = {}
        registry[path] = bucket
    return bucket


def asset_sort_key(item):
    """(order, key); a non-int order falls back to 0 so it cannot break sorted()."""
    key, entry = item
    try:
        order = int(entry.get("order", 0))
    except (TypeError, ValueError):
        order = 0
    return (order, key)


def asset_strip_block(content: str, start_marker: str, end_marker: str) -> str:
    """Remove every marker-wrapped block, leaving other content untouched."""
    while start_marker in content:
        start = content.find(start_marker)
        end = content.find(end_marker, start)
        if end == -1:
            # No end marker: the block was appended last, so drop to EOF.
            content = content[:start]
            break
        end += len(end_marker)
        if content[end : end + 1] == "\n":
            end += 1
        content = content[:start] + content[end:]
    return content


def asset_compose(app: Any, path: str) -> str:
    """Disk file plus every registered fragment, in (order, key) order."""
    import logging

    try:
        from open_webui.env import STATIC_DIR

        target = Path(STATIC_DIR) / path.rsplit("/", 1)[-1]
        body = (
            ""
            if (target.is_symlink() or not target.is_file())
            else target.read_text(encoding="utf-8")
        )
    except Exception:
        body = ""

    ordered = sorted(asset_fragments(app, path).items(), key=asset_sort_key)
    # Strip first: an older file-writing build may have left a block on disk.
    for _key, entry in ordered:
        body = asset_strip_block(body, entry["start"], entry["end"])
    body = body.rstrip()

    for key, entry in ordered:
        try:
            block = (entry["js"]() or "").strip()
        except Exception as exc:
            mark = (path, key, type(exc).__name__)
            if mark not in _ASSET_WARNED:
                if len(_ASSET_WARNED) > 256:
                    _ASSET_WARNED.clear()
                _ASSET_WARNED.add(mark)
                logging.getLogger("owui-shared-assets").warning(
                    "fragment %r failed for %s - it will be omitted",
                    key,
                    path,
                    exc_info=True,
                )
            continue
        if block:
            body = (body + "\n\n" if body else "") + block
    return body + "\n" if body else ""


def asset_register(
    app: Any, path: str, key: str, start: str, end: str, producer, order: int = 0
) -> None:
    """Publish a fragment and ensure the route exists. Idempotent."""
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    asset_fragments(app, path)[key] = {
        "start": start,
        "end": end,
        "js": producer,
        "order": order,
    }

    for existing in app.routes:
        if getattr(existing, ASSET_ROUTE_ATTR, None) != path:
            continue
        if getattr(existing, ASSET_IMPL_ATTR, 0) >= ASSET_IMPL_VERSION:
            return  # an equal or newer implementation already owns the route
        break  # ours is newer - fall through and replace it

    # Fragments live on app.state, so replacing the route loses nothing.
    app.routes[:] = [r for r in app.routes if getattr(r, "path", "") != path]
    media_type = SHARED_ASSET_TYPES.get(path, "text/plain; charset=utf-8")

    async def serve_asset(request):
        content = asset_compose(app, path)
        etag = (
            '"owui-'
            # Cache validator only; a bare md5() raises on FIPS hosts.
            + hashlib.md5(
                (path + "\x00" + content).encode("utf-8"), usedforsecurity=False
            ).hexdigest()
            + '"'
        )
        # no-cache, not no-store: no-store would disable the 304 below.
        headers = {
            "Cache-Control": "no-cache, must-revalidate, private",
            "ETag": etag,
        }
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        # Starlette only adds charset for text/*.
        return Response(content, media_type=media_type, headers=headers)

    insert_at = len(app.routes)
    for position, existing in enumerate(app.routes):
        if isinstance(existing, Mount) and getattr(existing, "name", "") == "static":
            insert_at = position
            break
    shared = Route(path, serve_asset, methods=["GET"])
    setattr(shared, ASSET_ROUTE_ATTR, path)
    setattr(shared, ASSET_IMPL_ATTR, ASSET_IMPL_VERSION)
    app.routes.insert(insert_at, shared)


# =========================== end shared asset block ========================


# ===========================================================================
# Feature catalogue
# ---------------------------------------------------------------------------
# Each feature (size, shape, fit, and later sprites / animations / avatar) is
# described by data here and consumed by the loader script, so adding an
# option is one dict entry rather than edits spread through CSS and JS.
# ===========================================================================

# Shape = border radius, box proportions (relative to the icon's base height)
# and an optional clip-path. "default" keeps Open WebUI's own look.
SHAPES = {
    "default": {"name": "Default"},
    "circle": {"name": "Circle", "r": "50%"},
    "rounded": {"name": "Rounded Square", "r": "1rem"},
    "squircle": {"name": "Squircle", "r": "34%"},
    "square": {"name": "Square", "r": "0"},
    "portrait": {"name": "Portrait (3:4)", "r": "0.75rem", "wm": 0.75},
    "landscape": {"name": "Landscape (4:3)", "r": "0.75rem", "wm": 1.333},
    "wide": {"name": "Wide (16:9)", "r": "0.75rem", "wm": 1.778},
    "hexagon": {
        "name": "Hexagon",
        "r": "0",
        "clip": "polygon(50% 0,100% 25%,100% 75%,50% 100%,0 75%,0 25%)",
    },
}
SHAPE_LITERAL = Literal[tuple(SHAPES)]

# Fit = how the picture sits inside the box.
FITS = {
    "default": {"name": "Default"},
    "cover": {"name": "Crop to fill"},
    "contain": {"name": "Show whole image"},
}
FIT_LITERAL = Literal[tuple(FITS)]

SIZE_MIN = 0.5
SIZE_MAX = 3.0


def clamp_size(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    if number != number:  # NaN
        return 1.0
    return min(SIZE_MAX, max(SIZE_MIN, number))


EMI_CSS = r"""
:root{--emi-s:1;--emi-mb:1.75rem;--emi-hb:2.25rem}
@media (min-width:640px){:root{--emi-hb:2.5rem}}
html[data-emi] img.assistant-message-profile-image,
html[data-emi] img[data-emi-hero]{
  width:calc(var(--emi-mb) * var(--emi-s) * var(--emi-wm,1))!important;
  height:calc(var(--emi-mb) * var(--emi-s))!important;
  border-radius:var(--emi-r,1rem)!important;
  object-fit:var(--emi-fit,cover)!important;
  clip-path:var(--emi-clip,none)
}
html[data-emi] img[data-emi-hero]{
  width:calc(var(--emi-hb) * var(--emi-s) * var(--emi-wm,1))!important;
  height:calc(var(--emi-hb) * var(--emi-s))!important
}
.emi-heading{margin:.85rem 0 .5rem;font-size:.6875rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--color-gray-400,#9ca3af)}
.emi-select{width:fit-content;max-width:14rem;padding:0 .5rem;border-radius:.125rem;background:transparent;font-size:.75rem;text-align:right;cursor:pointer;outline:none}
.dark .emi-select{background:var(--color-gray-900,#171717)}
.emi-range-row{display:flex;align-items:center;gap:8px}
.emi-range{width:128px;height:4px;cursor:pointer;accent-color:#d97757}
.emi-range-v{min-width:2.4rem;text-align:right;font-size:.75rem;font-variant-numeric:tabular-nums;color:var(--color-gray-500,#6b7280)}
.emi-range-r{padding:0;border:0;background:none;font-size:.6875rem;text-decoration:underline;cursor:pointer;color:var(--color-gray-400,#9ca3af)}
.emi-range-r[hidden]{display:none}
""".strip()

LOADER_SCRIPT = r"""
(function () {
  'use strict';
  if (window.__owuiEnhancedModelIcons) return;
  window.__owuiEnhancedModelIcons = true;

  var CFG = __CONFIG__;
  var CSS = __CSS__;
  var ROOT = document.documentElement;
  var KEYS = {
    size: 'owui-model-icon-size',
    shape: 'owui-model-icon-shape',
    fit: 'owui-model-icon-fit'
  };
  var scheduled = false;

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }
  function drop(key) {
    try { localStorage.removeItem(key); } catch (e) {}
  }
  function clamp(v) {
    v = parseFloat(v);
    if (!isFinite(v)) return null;
    return Math.min(CFG.sizeMax, Math.max(CFG.sizeMin, v));
  }

  // ---- state -----------------------------------------------------------
  function size() {
    var stored = clamp(read(KEYS.size));
    return stored === null ? CFG.size : stored;
  }
  function choice(group, key) {
    var value = read(key);
    return Object.prototype.hasOwnProperty.call(CFG[group], value) ? value : 'default';
  }
  function effective(group, key, fallback) {
    var value = choice(group, key);
    return value === 'default' ? fallback : value;
  }

  // Writes CSS variables on <html>. Everything is skipped (attribute removed)
  // when all settings are at their defaults, so a stock install is untouched.
  function apply() {
    var s = size();
    var shape = effective('shapes', KEYS.shape, CFG.shape);
    var fit = effective('fits', KEYS.fit, CFG.fit);
    var def = CFG.shapes[shape] || {};
    var props = {
      '--emi-s': String(s),
      '--emi-r': def.r || null,
      '--emi-wm': def.wm ? String(def.wm) : null,
      '--emi-clip': def.clip || null,
      '--emi-fit': fit === 'default' ? null : fit
    };
    Object.keys(props).forEach(function (name) {
      if (props[name] === null) ROOT.style.removeProperty(name);
      else ROOT.style.setProperty(name, props[name]);
    });
    var active = s !== 1 || shape !== 'default' || fit !== 'default';
    if (active) ROOT.setAttribute('data-emi', '1');
    else ROOT.removeAttribute('data-emi');
  }

  // ---- marking the empty-chat icon ------------------------------------
  // Open WebUI gives the in-reply icon a stable class
  // (assistant-message-profile-image) but the icon on the empty-chat screen has
  // no class or id. It is the model image inside the "-space-x-4" strip that
  // sits next to the model name <h2>, so find it structurally. Model pickers
  // and the sidebar use the same image URL but never match this shape.
  function markHero() {
    var imgs = document.querySelectorAll('img[src*="/models/model/profile/image"]:not(.assistant-message-profile-image)');
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (img.hasAttribute('data-emi-hero')) continue;
      var strip = img.closest('.-space-x-4');
      var row = strip && strip.parentElement && strip.parentElement.parentElement;
      if (row && row.querySelector(':scope > h2')) img.setAttribute('data-emi-hero', '1');
    }
  }

  // ---- settings UI -----------------------------------------------------
  function label(group, value, fallback) {
    var name = CFG[group][value].name;
    if (value !== 'default' || fallback === 'default') return name;
    return name + ' (' + CFG[group][fallback].name + ')';
  }

  function selectRow(at, id, title, note, group, key, fallback) {
    var block = document.createElement('div');
    block.id = id;
    var line = document.createElement('div');
    line.className = at.rowClass || 'flex items-center justify-between gap-2.5';
    var name = document.createElement('label');
    name.id = id + '-label';
    name.setAttribute('for', id + '-select');
    name.className = at.nameClass || 'min-w-0 text-xs text-gray-600 dark:text-gray-400';
    name.textContent = title;
    var select = document.createElement('select');
    select.id = id + '-select';
    select.className = 'emi-select text-gray-500 dark:text-gray-400';
    Object.keys(CFG[group]).forEach(function (value) {
      var opt = document.createElement('option');
      opt.value = value;
      opt.textContent = label(group, value, fallback);
      select.appendChild(opt);
    });
    select.value = choice(group, key);
    select.addEventListener('change', function () {
      if (select.value === 'default') drop(key); else write(key, select.value);
      apply();
    });
    line.appendChild(name);
    line.appendChild(select);
    var p = document.createElement('p');
    p.className = 'mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600';
    p.textContent = note;
    block.appendChild(line);
    block.appendChild(p);
    return block;
  }

  function sliderRow(at) {
    var id = 'emi-size';
    function fmt(v) { return v.toFixed(1) + '\u00d7'; }
    var block = document.createElement('div');
    block.id = id;
    var line = document.createElement('div');
    line.className = at.rowClass || 'flex items-center justify-between gap-2.5';
    var name = document.createElement('label');
    name.id = id + '-label';
    name.setAttribute('for', id + '-input');
    name.className = at.nameClass || 'min-w-0 text-xs text-gray-600 dark:text-gray-400';
    name.textContent = 'Model Icon Size';
    var right = document.createElement('div');
    right.className = 'emi-range-row';
    var input = document.createElement('input');
    input.type = 'range';
    input.id = id + '-input';
    input.className = 'emi-range';
    input.min = String(CFG.sizeMin);
    input.max = String(CFG.sizeMax);
    input.step = '0.1';
    input.value = String(size());
    var val = document.createElement('span');
    val.className = 'emi-range-v';
    val.textContent = fmt(size());
    var reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'emi-range-r';
    reset.textContent = 'Reset';
    reset.hidden = read(KEYS.size) === null;
    input.addEventListener('input', function () {
      var v = clamp(input.value);
      write(KEYS.size, String(v));
      val.textContent = fmt(v);
      reset.hidden = false;
      apply();
    });
    reset.addEventListener('click', function () {
      drop(KEYS.size);
      input.value = String(size());
      val.textContent = fmt(size());
      reset.hidden = true;
      apply();
    });
    right.appendChild(input);
    right.appendChild(val);
    right.appendChild(reset);
    line.appendChild(name);
    line.appendChild(right);
    var p = document.createElement('p');
    p.className = 'mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600';
    p.textContent = 'Size of the model icon on the empty-chat screen and beside each model reply. Saved in this browser.';
    block.appendChild(line);
    block.appendChild(p);
    return block;
  }

  function ensureSettings() {
    if (document.getElementById('emi-settings')) return;
    var anchor = document.getElementById('chat-bubble-ui-label');
    if (!anchor || !anchor.closest('#tab-interface')) return;
    var row = anchor.parentElement;
    var wrapper = row && row.parentElement;
    if (!wrapper || !wrapper.parentElement) return;
    var at = { rowClass: row.className, nameClass: anchor.className };

    var box = document.createElement('div');
    box.id = 'emi-settings';
    var head = document.createElement('div');
    head.className = 'emi-heading';
    head.textContent = 'Model icon';
    box.appendChild(head);
    box.appendChild(sliderRow(at));
    box.appendChild(selectRow(at, 'emi-shape', 'Model Icon Shape',
      'Shape of the crop around the model icon. Default keeps Open WebUI\u2019s own rounded square.',
      'shapes', KEYS.shape, CFG.shape));
    box.appendChild(selectRow(at, 'emi-fit', 'Model Icon Fit',
      'Crop to fill trims the picture to the shape; Show whole image fits all of it inside, which can leave empty margins.',
      'fits', KEYS.fit, CFG.fit));
    wrapper.parentElement.insertBefore(box, wrapper.nextSibling);
  }

  // ---- lifecycle -------------------------------------------------------
  function ensureStyle() {
    if (document.getElementById('owui-emi-style')) return;
    var style = document.createElement('style');
    style.id = 'owui-emi-style';
    style.textContent = CSS;
    (document.head || ROOT).appendChild(style);
  }

  function scan() {
    scheduled = false;
    markHero();
    ensureSettings();
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(scan);
  }

  window.addEventListener('storage', function (evt) {
    if (evt.key && evt.key.indexOf('owui-model-icon-') !== 0) return;
    apply();
    var box = document.getElementById('emi-settings');
    if (box) box.remove();
    schedule();
  });

  ensureStyle();
  apply();
  new MutationObserver(schedule).observe(ROOT, { childList: true, subtree: true });
  schedule();
})();
"""


class Event:
    class Valves(BaseModel):
        default_size: float = Field(
            default=1.0,
            description="Default model icon size multiplier (0.5 to 3.0, clamped) for users who have not moved the slider in Settings > Interface. 1.0 keeps Open WebUI's size.",
        )
        default_shape: SHAPE_LITERAL = Field(
            default="default",
            description="What Default means in the Model Icon Shape setting. 'default' keeps Open WebUI's rounded square. Others: circle, rounded, squircle, square, portrait, landscape, wide, hexagon.",
        )
        default_fit: FIT_LITERAL = Field(
            default="default",
            description="What Default means in the Model Icon Fit setting. 'cover' crops the picture to fill the shape; 'contain' shows the whole image inside it.",
        )

    LOADER_BLOCK_START = "// owui-enhanced-model-icons:start"
    LOADER_BLOCK_END = "// owui-enhanced-model-icons:end"
    ASSET_KEY = ASSET_KEY
    ASSET_ORDER = 10  # after Indicator Changer (0) so both coexist predictably

    _fragment_cache = None
    instance = None

    def __init__(self):
        self.valves = self.Valves()
        Event.instance = self
        reload_bootstrap()

    def _loader_fragment(self) -> str:
        valves = self.valves
        dump = valves.model_dump() if hasattr(valves, "model_dump") else valves.dict()
        cache_key = _json.dumps(dump, sort_keys=True, default=str)
        cached = Event._fragment_cache
        if cached and cached[0] == cache_key:
            return cached[1]
        config = _json.dumps(
            {
                "size": clamp_size(valves.default_size),
                "sizeMin": SIZE_MIN,
                "sizeMax": SIZE_MAX,
                "shape": valves.default_shape,
                "fit": valves.default_fit,
                "shapes": SHAPES,
                "fits": FITS,
            }
        )
        js = (
            LOADER_SCRIPT.strip()
            .replace("__CONFIG__", config)
            .replace("__CSS__", _json.dumps(EMI_CSS))
        )
        fragment = f"{self.LOADER_BLOCK_START}\n{js}\n{self.LOADER_BLOCK_END}"
        Event._fragment_cache = (cache_key, fragment)
        return fragment

    def _publish(self, app) -> None:
        asset_register(
            app,
            LOADER_PATH,
            self.ASSET_KEY,
            self.LOADER_BLOCK_START,
            self.LOADER_BLOCK_END,
            lambda: Event.instance._loader_fragment() if reload_active(app) else "",
            order=self.ASSET_ORDER,
        )

    async def event(
        self,
        event: Optional[dict] = None,
        __event_name__: str = "",
        __app__: Any = None,
        **kwargs,
    ) -> None:
        if __app__ is not None:
            await reload_on_event(__app__, event, __event_name__)


def register(app: Any) -> None:
    Event.instance._publish(app)
