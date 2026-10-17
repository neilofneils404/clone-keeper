# Interface screenshots

These are unmodified captures of the actual Clone Keeper QML interface, rendered
by `python scripts/smoke-ui.py`. The examples use disposable, synthetic Omarchy
sources and plugin clones. They do not show an installed user plugin or represent
a change to the live desktop.

- **review.png:** Both sides changed the same line of `Theme.qml`. The panel shows
  your lines and upstream's for the overlap, with Keep yours, Take upstream, Keep
  both and Use original; whole-file choices remain below. Applying still requires
  explicit confirmation.
- **origin.png:** An existing customized clone with no recorded baseline. The panel
  found its original in a cached package of the release it came from and asks the
  owner to confirm it.
- **../../preview.png:** The marketplace preview: the one-click Update confirmation
  for a clean merge, with the prepared review behind it.

The images were captured in the current Omarchy theme. Colors, type size and
spacing follow each user's theme and accessibility settings.
