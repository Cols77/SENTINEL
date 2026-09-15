# Third-party notices

SENTINEL's real Tello backend is an adapter around DJITelloPy 2.5.0. The
upstream release is pinned to Git commit `0066a1e` from the
[DJITelloPy repository](https://github.com/damiafuentes/DJITelloPy), and is
licensed under the MIT License by Damià Fuentes Escoté, Jakob Löw, and the
project contributors. The upstream license text is available in that release's
`LICENSE.txt`.

The adapter keeps SENTINEL-specific changes outside the upstream protocol
implementation: explicit local-IP socket binding, lifecycle cleanup, decoder
failure visibility, and the direct emergency-send boundary. PyAV remains a
runtime dependency used for H.264 decoding and is distributed under its own
BSD-3-Clause license; its license and notices apply to the installed package.
