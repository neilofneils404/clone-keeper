# Interface screenshots

These are unmodified captures of the actual Clone Keeper QML interface, rendered
by `python scripts/smoke-ui.py --native-load`. The examples use disposable,
synthetic Omarchy sources and plugin clones. They do not show an installed user
plugin or represent a change to the live desktop.

- **review.png:** A saved merge is ready to apply after a binary-file conflict
  has been resolved. The selected `BarWidget.qml` comparison shows the proposed
  upstream refresh-interval change; the fixture also preserves a local clock
  format customization. Applying still requires explicit confirmation.
- **create.png:** The tracked-clone creation form, populated from the synthetic
  built-in plugin catalog. Newly created clones remain disabled until the user
  explicitly enables them.

The images were captured in the current Omarchy theme. Colors, type size and
spacing follow each user's theme and accessibility settings.
