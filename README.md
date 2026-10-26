# Enhanced Model Icons for Open WebUI

An Open WebUI function plugin that adds enhancements to the **model icon**. It works the same way as the
[Indicator Changer](https://github.com/iChristGit/OpenWebui-Tools): it publishes a script into `/static/loader.js`
through the Shared Assets Protocol, so it coexists with Indicator Changer, Theme Designer Pro and any other plugin that uses it.
No Open WebUI files are modified.

## Install

1. In Open WebUI go to **Admin Panel > Functions > +** and paste the contents of `enhanced_model_icons.py`.
2. Enable the function, then reload the page.
3. Open **Settings > Interface**. A **Model icon** section appears under *Chat Bubble UI*.

Requires Open WebUI 0.11.0 or newer (same as Indicator Changer).

## v0.1.0 features

| Setting | What it does |
|---|---|
| **Chat Message Icon Size** | 0.5x to 3x slider for the icon beside every model reply. Per-browser, with a Reset link. |
| **Start Screen Icon Size** | Separate 0.5x to 3x slider for the icon on the empty-chat screen. |
| **Model Icon Shape** | Default (Open WebUI's rounded square), Circle, Rounded Square, Squircle, Square, Portrait 3:4, Landscape 4:3, Wide 16:9, Hexagon. |
| **Model Icon Fit** | *Crop to fill* trims the picture to the shape; *Show whole image* fits all of it inside the box. |

Admin valves set what "Default" means for users who haven't chosen: `default_size` (message icon), `default_hero_size`, `default_shape`, `default_fit`.

With every setting at its default the plugin changes nothing on the page.

### How the icons are found

* Beside replies: `img.assistant-message-profile-image` (a stable class in Open WebUI).
* Empty-chat screen: the icon has no class or id, so it is matched by structure (the profile image inside the `-space-x-4`
  strip next to the model-name `<h2>`) and tagged `data-emi-hero`. If a future Open WebUI release changes that markup,
  `markHero()` in the loader script is the one place to update.

## Roadmap

Planned, in rough order. The settings catalogue (`SHAPES`, `FITS`) and the loader are data-driven so each is an additive change.

- [ ] Icon animations (idle, speaking while streaming, thinking)
- [ ] Sprite sheets: several profile images per model, chosen per message from context (emotion/keyword rules or a model-emitted tag)
- [ ] Per-model overrides (size/shape set per model instead of globally)
- [ ] Floating VRM avatar above the chat instead of the icon image, with audio-driven (TTS) animation

## Development

```
pip install pydantic pytest
pytest
```

The tests stub `open_webui`, build the loader fragment, and run `node --check` on it when Node is installed.
They do not run a live Open WebUI; verify visually after installing.

## License

MIT
